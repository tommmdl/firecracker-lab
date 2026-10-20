#!/usr/bin/env python3
"""Run trusted jailer checks inside an explicitly privileged, capped helper."""
import argparse
import hashlib
import shutil
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary-dir',required=True)
    parser.add_argument('--artifacts',required=True)
    parser.add_argument('--output-parent',required=True)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    profile=json.loads((repo/'config/jailer-profile.json').read_text())
    binaries=Path(args.binary_dir).resolve(strict=True)
    artifacts=Path(args.artifacts).resolve(strict=True)
    parent=Path(args.output_parent).resolve(strict=True)
    inputs={name:binaries/name for name in ['firecracker','jailer']}
    inputs.update({name:artifacts/name for name in ['vmlinux','rootfs.ext4']})
    for path in inputs.values():
        if not path.is_file():parser.error('Missing artifact: '+str(path))
    out=Path(tempfile.mkdtemp(prefix='jailer-',dir=parent))
    snapshot=out/'source';snapshot.mkdir()
    for source in (repo/'scripts').glob('*.py'):shutil.copyfile(source,snapshot/source.name)
    shutil.copyfile(repo/'config/jailer-profile.json',out/'profile.json')
    name='fc-jailer-lab-'+uuid.uuid4().hex[:10]
    command=['docker','run','--rm','--name',name,'--privileged','--network','none','--cgroupns','private','--memory','768m','--memory-swap','768m','--cpus','2','--pids-limit','256']
    for source,target,readonly in [(snapshot,'/suite',True),(out/'profile.json','/profile.json',True),(out,'/outputs',False),*[(path,'/inputs/'+name,True) for name,path in inputs.items()]]:
        if ',' in str(source):parser.error('Docker bind paths cannot contain commas')
        command+=['--mount',f'type=bind,src={source},dst={target}'+(',readonly' if readonly else '')]
    command += [profile['helper_image'],'python','/suite/jailer_checks.py']
    (out/'launch.json').write_text(json.dumps({'command':command,'privileged_bootstrap':True,'host_uid':os.getuid(),'host_gid':os.getgid(),'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.glob('*.py')}},indent=2)+'\n')
    code=1
    try:
        with (out/'supervisor.log').open('wb') as log:
            code=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=180).returncode
    except subprocess.TimeoutExpired:
        code=124
        (out/'watchdog.json').write_text(json.dumps({'deadline_seconds':180,'status':'failed','action':'stop only this named helper container'})+'\n')
    finally:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        # Normalize only this run's files after all test processes have stopped.
        normalize="import os,stat,sys; root='/data'; uid=int(sys.argv[1]); gid=int(sys.argv[2])\ndef fail(error):raise error\nfor parent,dirs,files in os.walk(root,topdown=False,followlinks=False,onerror=fail):\n for name in files+dirs:\n  path=os.path.join(parent,name); mode=os.lstat(path).st_mode\n  if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISSOCK(mode):os.unlink(path)\n  else:os.chown(path,uid,gid,follow_symlinks=False)\nos.chown(root,uid,gid)"
        cleanup=subprocess.run(['docker','run','--rm','--network','none','--cap-drop','ALL','--cap-add','CHOWN','--cap-add','DAC_OVERRIDE','--mount',f'type=bind,src={out},dst=/data',profile['helper_image'],'python','-c',normalize,str(os.getuid()),str(os.getgid())],capture_output=True,text=True,timeout=30)
        if cleanup.returncode:
            print('Output ownership normalization failed:',cleanup.stderr)
            code=1
    if (out/'result.json').exists():
        report=json.loads((out/'result.json').read_text())
        print(json.dumps({'run_directory':str(out),'status':report['status'],'scenarios':[(row['scenario'],row['status']) for row in report['scenarios']],'failure':report.get('failure'),'findings':report['findings'],'exit_code':code},indent=2))
    else:
        print(json.dumps({'run_directory':str(out),'status':'failed','exit_code':code,'diagnostic':'See supervisor.log; no suite report was produced'},indent=2))
    return code


if __name__=='__main__':
    raise SystemExit(main())
