#!/usr/bin/env python3
"""Boot an existing, trusted local rootfs clone; no network or downloads."""
import argparse
import copy
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import selectors
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone


def normalize_console(raw):
    text = raw.decode('utf-8', errors='replace')
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    return re.sub(r'\r+\n|\r+', '\n', text)


def guest_evidence(raw, token):
    text = normalize_console(raw)
    # Whole lines prevent the shell's echoed command from passing as output.
    match = re.search(r'^FC_BEGIN_' + re.escape(token) + r'\n(.*?)^FC_END_' + re.escape(token) + r'$', text, re.M | re.S)
    if not match:
        return None
    fields = dict(re.findall(r'^(uid|kernel|rootfs)=(.*)$', match[1], re.M))
    mount = fields.get('rootfs', '').split()
    if fields.get('uid') != '0' or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+[^\s]*', fields.get('kernel', '')):
        raise ValueError('Guest identity or kernel evidence is invalid')
    if len(mount) != 3 or mount[:2] != ['/dev/vda', 'ext4'] or 'rw' not in mount[2].split(','):
        raise ValueError('Guest root filesystem is not /dev/vda ext4 rw')
    return fields


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def shutdown(sock_path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(str(sock_path))
        body = b'{"action_type":"SendCtrlAltDel"}'
        client.sendall(b'PUT /actions HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
        response = b''
        while b'\r\n' not in response:
            chunk = client.recv(4096)
            if not chunk:
                break
            response += chunk
        return int(response.split(b' ', 2)[1])


def run(args):
    paths = {key: Path(getattr(args, key)).resolve(strict=True) for key in ('firecracker', 'kernel', 'rootfs', 'config')}
    config = json.loads(paths['config'].read_text())
    if config.get('network-interfaces') or len(config.get('drives', [])) != 1:
        raise ValueError('This boot test requires no network interfaces and exactly one drive')
    if not config['drives'][0].get('is_root_device') or config['drives'][0].get('is_read_only'):
        raise ValueError('Expected one writable root drive')
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        raise ValueError('Read/write access to /dev/kvm is required')
    parent = Path(args.output_parent).resolve(strict=True)
    directory = Path(tempfile.mkdtemp(prefix='boot-', dir=parent))
    sock = directory / 'api.socket'
    if len(os.fsencode(sock)) >= 108:
        raise ValueError('Output path is too long for a Unix socket; choose a shorter parent')
    report = {'schema': 1, 'started_at': datetime.now(timezone.utc).isoformat(),
              'status': 'failed', 'failure': None, 'guest': None, 'forced_stop': False,
              'shutdown_http_status': None, 'exit_code': None, 'network_interfaces': 0,
              'run_directory': str(directory), 'original_sha256_before': {}, 'original_sha256_after': {}}
    raw = bytearray()
    process = None
    master = slave = None
    selector = selectors.DefaultSelector()
    started = time.monotonic()
    # Advisory lock serializes this harness against other runs on the same source.
    with paths['rootfs'].open('rb') as source_lock, (directory / 'console.log').open('wb') as log:
        fcntl.flock(source_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            report['original_sha256_before'] = {key: digest(path) for key, path in paths.items()}
            clone = directory / 'rootfs.ext4'
            shutil.copyfile(paths['rootfs'], clone)
            report['clone_sha256_before'] = digest(clone)
            if report['clone_sha256_before'] != report['original_sha256_before']['rootfs']:
                raise ValueError('Clone hash differs from source')
            generated = copy.deepcopy(config)
            generated['boot-source']['kernel_image_path'] = str(paths['kernel'])
            generated['drives'][0]['path_on_host'] = str(clone)
            (directory / 'vm-config.json').write_text(json.dumps(generated, indent=2) + '\n')
            master, slave = pty.openpty()
            process = subprocess.Popen([str(paths['firecracker']), '--api-sock', str(sock), '--config-file', str(directory / 'vm-config.json')], stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            slave = None
            os.set_blocking(master, False)
            selector.register(master, selectors.EVENT_READ)
            token = uuid.uuid4().hex
            command = ("printf '\\nFC_BEGIN_%s\\n' " + token + "; printf 'uid='; id -u; printf 'kernel='; uname -r; printf 'rootfs='; findmnt -n -o SOURCE,FSTYPE,OPTIONS /; printf 'FC_END_%s\\n' " + token + "\n").encode()
            sent = False

            def capture(wait):
                for key, _ in selector.select(wait):
                    try:
                        chunk = os.read(key.fd, 65536)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            selector.unregister(key.fd)
                            continue
                        raise
                    if chunk:
                        raw.extend(chunk)
                        log.write(chunk)
                        log.flush()
                    else:
                        selector.unregister(key.fd)

            boot_start = time.monotonic()
            try:
                while time.monotonic() - boot_start < args.boot_timeout:
                    capture(0.1)
                    if not sent and re.search(r'^root@[^\n]*[#$] ?$', normalize_console(raw), re.M):
                        os.write(master, command)
                        sent = True
                    report['guest'] = guest_evidence(raw, token)
                    if report['guest']:
                        report['boot_evidence_seconds'] = round(time.monotonic() - boot_start, 3)
                        break
                    if process.poll() is not None:
                        raise RuntimeError('Firecracker exited before guest evidence')
                else:
                    raise TimeoutError('Timed out waiting for complete guest evidence')
            finally:
                # Even failed tests attempt a graceful shutdown, while preserving failure.
                if process.poll() is None:
                    try:
                        report['shutdown_http_status'] = shutdown(sock)
                    except (OSError, ValueError, IndexError) as exc:
                        report['shutdown_error'] = str(exc)
                    deadline = time.monotonic() + args.shutdown_timeout
                    while process.poll() is None and time.monotonic() < deadline:
                        capture(0.1)
                    if process.poll() is None:
                        report['forced_stop'] = True
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                capture(0)
                report['exit_code'] = process.returncode
            if report['shutdown_http_status'] != 204 or report['exit_code'] != 0 or report['forced_stop']:
                raise RuntimeError('Guest evidence exists but shutdown was not clean')
        except (Exception, KeyboardInterrupt) as exc:
            report['failure'] = type(exc).__name__ + ': ' + str(exc)
        finally:
            if process is not None and process.poll() is None:
                report['forced_stop'] = True
                process.kill()
                process.wait(timeout=5)
                report['exit_code'] = process.returncode
            selector.close()
            for fd in (master, slave):
                if fd is not None:
                    os.close(fd)
            report['original_sha256_after'] = {key: digest(path) for key, path in paths.items()}
            report['originals_unchanged'] = report['original_sha256_before'] == report['original_sha256_after']
            if not report['originals_unchanged']:
                report['failure'] = (report['failure'] or '') + ' Original artifact integrity changed'
            report['console_sha256'] = hashlib.sha256(raw).hexdigest()
            report['elapsed_seconds'] = round(time.monotonic() - started, 3)
            report['status'] = 'passed' if report['failure'] is None else 'failed'
            (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('firecracker', 'kernel', 'rootfs', 'config', 'output-parent'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--boot-timeout', type=float, default=90)
    parser.add_argument('--shutdown-timeout', type=float, default=30)
    args = parser.parse_args()
    if args.boot_timeout <= 0 or args.shutdown_timeout <= 0:
        parser.error('Timeouts must be positive')
    try:
        return run(args)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'Preflight failed: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
