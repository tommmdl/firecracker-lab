#!/usr/bin/env python3
"""Build a kernel and a minimal rootfs from pinned sources, without host root."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

REPO = Path(__file__).resolve().parents[1]


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require_hash(path, expected):
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f'SHA256 mismatch: {Path(path).name}; expected {expected}, got {actual}')
    return actual


def fetch(item, cache):
    path = cache / item['archive']
    if path.exists():
        require_hash(path, item['sha256'])
        return path
    errors = []
    for url in item['urls']:
        temporary = None
        try:
            with urllib.request.urlopen(url, timeout=60) as response, tempfile.NamedTemporaryFile(dir=cache, delete=False) as target:
                temporary = Path(target.name)
                shutil.copyfileobj(response, target)
            require_hash(temporary, item['sha256'])
            temporary.rename(path)
            return path
        except (OSError, ValueError) as exc:
            errors.append(f'{url}: {exc}')
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    raise ValueError('Source download/verification failed: ' + '; '.join(errors))


def require_config(path, fragment):
    lines = set(path.read_text().splitlines())
    for requested in fragment.read_text().splitlines():
        if not requested.startswith('CONFIG_'):
            continue
        if requested in lines:
            continue
        if requested.endswith('=n') and '# ' + requested[:-2] + ' is not set' in lines:
            continue
        raise ValueError('Required configuration was not applied: ' + requested)


def build(args):
    if platform.machine() != 'x86_64':
        raise ValueError('This recipe targets x86_64')
    work = Path(args.work_dir).resolve()
    if re.search(r'\s', str(work)):
        raise ValueError('Use a Linux build directory without whitespace')
    work.mkdir(parents=True, exist_ok=False)
    cache = Path(args.cache_dir).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    lock = json.loads((REPO / 'config/sources.lock.json').read_text())
    manifest = {'schema':1, 'started_at':datetime.now(timezone.utc).isoformat(), 'status':'failed', 'stage':'source-verification', 'lock_sha256':sha256(REPO / 'config/sources.lock.json'), 'sources':{}, 'artifacts':{}}
    env = os.environ.copy()
    env.update(SOURCE_DATE_EPOCH='1792022400', KBUILD_BUILD_TIMESTAMP='Thu Oct 15 00:00:00 UTC 2026', KBUILD_BUILD_USER='builder', KBUILD_BUILD_HOST='firecracker-lab', KBUILD_BUILD_VERSION='1', KCONFIG_NOTIMESTAMP='1', LC_ALL='C', TZ='UTC')
    try:
        with (work / 'build.log').open('w') as log:
            def run(command, cwd=work, input=None):
                log.write(json.dumps(command) + '\n')
                log.flush()
                return subprocess.run(command, cwd=cwd, env={**env, 'PWD':str(cwd)}, input=input, text=True, stdout=log, stderr=subprocess.STDOUT, check=True)

            kernel = fetch(lock['kernel'], cache)
            signature = fetch(lock['kernel']['signature'], cache)
            busybox = fetch(lock['busybox'], cache)
            key = REPO / lock['kernel']['key_file']
            require_hash(key, lock['kernel']['key_sha256'])
            home = work / 'gnupg'
            home.mkdir(mode=0o700)
            run(['gpg','--homedir',str(home),'--batch','--import',str(key)])
            with (work / 'kernel-signature.status').open('w') as status:
                with subprocess.Popen(['xz','-dc',str(kernel)], stdout=subprocess.PIPE, stderr=log) as unpack:
                    checked = subprocess.run(['gpg','--homedir',str(home),'--batch','--status-fd','1','--verify',str(signature),'-'], stdin=unpack.stdout, stdout=status, stderr=log)
                    unpack.stdout.close()
                    if unpack.wait() or checked.returncode:
                        raise ValueError('Kernel detached signature verification failed')
            valid = re.findall(r'^\[GNUPG:\] VALIDSIG (\S+)', (work / 'kernel-signature.status').read_text(), re.M)
            if valid != [lock['kernel']['signer_fingerprint']]:
                raise ValueError('Unexpected kernel signer fingerprint')
            manifest['sources'] = {'kernel':{'sha256':sha256(kernel),'signer_fingerprint':valid[0],'signature':'verified'},'busybox':{'sha256':sha256(busybox),'verification':'official HTTPS checksum, pinned SHA256'}}
            if args.verify_only:
                manifest['status'] = 'sources-verified'
                return manifest
            manifest['stage'] = 'compile'
            for archive, prefix in [(kernel,'linux-'+lock['kernel']['version']), (busybox,'busybox-'+lock['busybox']['version'])]:
                with tarfile.open(archive) as source:
                    if any(not (member.name == prefix or member.name.startswith(prefix+'/')) for member in source.getmembers()):
                        raise ValueError('Unexpected archive layout')
                    source.extractall(work, filter='data')
            linux = work / ('linux-'+lock['kernel']['version'])
            bb = work / ('busybox-'+lock['busybox']['version'])
            seed = REPO / 'config/kernel.fragment'
            shutil.copyfile(seed, linux / 'build.seed')
            run(['make','KCONFIG_ALLCONFIG=build.seed','allnoconfig'],linux)
            require_config(linux / '.config',seed)
            run(['make','-j'+str(args.jobs),'vmlinux'],linux)
            run(['make','allnoconfig'],bb)
            text = (bb / '.config').read_text()
            for requested in (REPO / 'config/busybox.fragment').read_text().splitlines():
                name = requested.split('=')[0]
                pattern = r'^(?:'+re.escape(name)+r'=.*|# '+re.escape(name)+r' is not set)$'
                text, count = re.subn(pattern, requested, text, flags=re.M)
                if count != 1:
                    raise ValueError('Unknown BusyBox option: '+name)
            (bb / '.config').write_text(text)
            run(['make','oldconfig'],bb,input='\n'*10000)
            require_config(bb / '.config',REPO / 'config/busybox.fragment')
            run(['make','-j'+str(args.jobs)],bb)
            manifest['stage'] = 'rootfs'
            root = work / 'rootfs-tree'
            root.mkdir()
            run(['make','CONFIG_PREFIX='+str(root),'install'],bb)
            for directory in ('dev','proc','sys','tmp','root','etc/init.d'):
                (root / directory).mkdir(parents=True,exist_ok=True)
            (root / 'tmp').chmod(0o1777)
            (root / 'root').chmod(0o700)
            for source,target,mode in [('guest/inittab','etc/inittab',0o644),('guest/profile','etc/profile',0o644),('guest/rcS','etc/init.d/rcS',0o755),('guest/sbin/reboot-clean','sbin/reboot-clean',0o755)]:
                shutil.copyfile(REPO / source, root / target)
                (root / target).chmod(mode)
            (root / 'etc/passwd').write_text('root:x:0:0:Lab console:/root:/bin/sh\n')
            (root / 'etc/shadow').write_text('root:!:20000:0:99999:7:::\n')
            (root / 'etc/shadow').chmod(0o600)
            (root / 'etc/group').write_text('root:x:0:\n')
            (root / 'etc/os-release').write_text('NAME="Firecracker lab minimal Linux"\nID=fc-lab\nVERSION_ID="'+lock['busybox']['version']+'"\n')
            artifacts = work / 'artifacts'
            artifacts.mkdir()
            shutil.copyfile(linux / 'vmlinux',artifacts / 'vmlinux')
            for directory,name in [(linux,'kernel.config'),(bb,'busybox.config')]:
                shutil.copyfile(directory / '.config',artifacts / name)
            disk = artifacts / 'rootfs.ext4'
            with disk.open('xb') as image:
                image.truncate(lock['rootfs']['size_mib'] * 1024 * 1024)
            # fakeroot changes ownership only in the emulated view used by mke2fs.
            run(['fakeroot','sh','-ec','chown -hR 0:0 "$1"; exec mke2fs -q -t ext4 -F -L "$3" -U "$4" -E lazy_itable_init=0,lazy_journal_init=0 -d "$1" "$2"','build-rootfs',str(root),str(disk),lock['rootfs']['label'],lock['rootfs']['uuid']])
            run(['e2fsck','-fn',str(disk)])
            config = {'boot-source':{'kernel_image_path':'./vmlinux','boot_args':'console=ttyS0 reboot=k panic=1 pci=off root=/dev/vda rw init=/sbin/init'},'drives':[{'drive_id':'rootfs','path_on_host':'./rootfs.ext4','is_root_device':True,'is_read_only':False}],'machine-config':{'vcpu_count':2,'mem_size_mib':256}}
            (artifacts / 'vm-config.json').write_text(json.dumps(config,indent=2)+'\n')
            manifest['artifacts'] = {p.name:{'sha256':sha256(p),'bytes':p.stat().st_size} for p in artifacts.iterdir()}
            manifest['recipe_inputs'] = {str(p.relative_to(REPO)):sha256(p) for p in [REPO/'config/kernel.fragment',REPO/'config/busybox.fragment',*sorted((REPO/'guest').rglob('*'))] if p.is_file()}
            manifest['toolchain'] = {tool:subprocess.check_output([tool,'--version'],env=env,text=True,stderr=subprocess.STDOUT).splitlines()[0] for tool in ('gcc','ld','make','bison','flex','m4','fakeroot')}
            manifest['status'] = 'built'
            manifest['stage'] = 'complete'
            return manifest
    except (Exception, KeyboardInterrupt) as exc:
        manifest['failure'] = type(exc).__name__+': '+str(exc)
        raise
    finally:
        (work / 'build-result.json').write_text(json.dumps(manifest,indent=2)+'\n')
        print(json.dumps(manifest,indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir',required=True)
    parser.add_argument('--cache-dir',required=True)
    parser.add_argument('--jobs',type=int,default=4)
    parser.add_argument('--verify-only',action='store_true')
    args = parser.parse_args()
    if args.jobs < 1 or args.jobs > 4:
        parser.error('Use 1 to 4 jobs for this local lab')
    try:
        build(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    main()
