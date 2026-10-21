"""API framing and runtime approval rules, independent of container setup."""
import json
import re
import socket

def request(path, method, endpoint, body=None):
    data = b'' if body is None else json.dumps(body).encode()
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(path))
        client.sendall(f'{method} {endpoint} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n'.encode()+data)
        response=b''
        while b'\r\n\r\n' not in response:
            part=client.recv(65536)
            if not part:raise ConnectionError('API closed before headers')
            response+=part
        headers,_,payload=response.partition(b'\r\n\r\n')
        length=re.search(rb'(?im)^content-length:\s*(\d+)',headers)
        expected=int(length.group(1)) if length else 0
        while len(payload)<expected:
            part=client.recv(65536)
            if not part:raise ConnectionError('API closed before response body')
            payload+=part
    return int(headers.split(b' ',2)[1]),payload[:expected].decode(errors='replace')


def runtime_is_hardened(threads, uid, gid):
    """Approve only a running snapshot with VMM, API and two vCPU threads."""
    if len(threads) < 4:
        return False
    try:
        return all(
            set(row['Uid'].split()) == {str(uid)}
            and set(row['Gid'].split()) == {str(gid)}
            and '0' not in row['Groups'].split()
            and all(int(row[key], 16) == 0 for key in ('CapEff','CapPrm','CapInh','CapAmb'))
            and row['Seccomp'].strip() == '2'
            and row['NoNewPrivs'].strip() == '1'
            for row in threads.values()
        )
    except (KeyError, ValueError):
        return False


def api_filter_ready(threads):
    """Socket existence is insufficient: observe the actual filtered API thread."""
    return any(row.get('Name','').strip()=='fc_api'
               and row.get('Seccomp','').strip()=='2'
               and row.get('NoNewPrivs','').strip()=='1'
               for row in threads.values())
