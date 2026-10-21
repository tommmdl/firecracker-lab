#!/usr/bin/env python3
"""Run trusted jailer checks inside an explicitly privileged, capped helper."""
import argparse
import fcntl
import hashlib
import shutil
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from build_shutdown_candidate import static_properties, require_static


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-load', action='store_true', help='Bounded CPU contention on the same two host CPUs')
    parser.add_argument('--profile', help='Explicit diagnostic profile; default is the approved jailer profile')
    parser.add_argument('--suite', choices=['jailer_checks', 'shutdown_investigation', 'vcpu_scheduling', 'vcpu_protocol', 'vcpu_memory'], default='jailer_checks')
    parser.add_argument('--binary-dir',required=True)
    parser.add_argument('--artifacts',required=True)
    parser.add_argument('--output-parent',required=True)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    profile_path=Path(args.profile).resolve(strict=True) if args.profile else repo/'config/jailer-profile.json'
    profile=json.loads(profile_path.read_text())
    if profile.get('helper_cache_policy','retain') not in ('retain','release-stopped-clones'):parser.error('Unknown helper cache policy')
    if profile.get('investigation_matrix')=='host-load' and not args.host_load:parser.error('host-load matrix requires --host-load')
    binaries=Path(args.binary_dir).resolve(strict=True)
    artifacts=Path(args.artifacts).resolve(strict=True)
    parent=Path(args.output_parent).resolve(strict=True)
    inputs={name:binaries/name for name in ['firecracker','jailer']}
    inputs.update({name:artifacts/name for name in ['vmlinux','rootfs.ext4']})
    for path in inputs.values():
        if not path.is_file():parser.error('Missing artifact: '+str(path))
    preflight={}
    for binary in ['firecracker','jailer']:
        preflight[binary]=static_properties(inputs[binary])
        try:require_static(preflight[binary])
        except ValueError as exc:parser.error(binary+': '+str(exc))
        if preflight[binary]['sha256']!=profile[binary+'_sha256']:parser.error('Unreviewed '+binary+' hash')
    load_lock = None
    if args.host_load:
        load_lock = open('/tmp/firecracker-lab-'+str(os.getuid())+'-host-load.lock', 'a')
        try:
            fcntl.flock(load_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Another host-load campaign from this wrapper is active')
    out=Path(tempfile.mkdtemp(prefix='jailer-',dir=parent))
    (out/'binary-preflight.json').write_text(json.dumps(preflight,indent=2)+'\n')
    snapshot=out/'source';snapshot.mkdir()
    for source in (repo/'scripts').glob('*.py'):shutil.copyfile(source,snapshot/source.name)
    shutil.copyfile(profile_path,out/'profile.json')
    name='fc-jailer-lab-'+uuid.uuid4().hex[:10]
    load_name=name+'-load'
    deadline=int(profile.get('suite_timeout',180))
    if not 1<=deadline<=600:parser.error('suite_timeout must be between 1 and 600 seconds')
    cpus=sorted(os.sched_getaffinity(0))[-2:]
    if args.host_load and len(cpus)!=2:parser.error('host-load needs two available host CPUs')
    command=['docker','run','--rm','--name',name,'--privileged','--network','none','--cgroupns','private','--memory','768m','--memory-swap','768m','--cpus','2','--pids-limit','256']
    if args.host_load:command+=['--cpuset-cpus',','.join(map(str,cpus))]
    for source,target,readonly in [(snapshot,'/suite',True),(out/'profile.json','/profile.json',True),(out,'/outputs',False),*[(path,'/inputs/'+name,True) for name,path in inputs.items()]]:
        if ',' in str(source):parser.error('Docker bind paths cannot contain commas')
        command+=['--mount',f'type=bind,src={source},dst={target}'+(',readonly' if readonly else '')]
    command += [profile['helper_image'],'python','/suite/'+args.suite+'.py']
    (out/'launch.json').write_text(json.dumps({'command':command,'privileged_bootstrap':True,'host_uid':os.getuid(),'host_gid':os.getgid(),'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.glob('*.py')}},indent=2)+'\n')
    code=1
    try:
        if args.host_load:
            stress="import multiprocessing as m,time,json,os\ndef burn():\n while True:pass\nworkers=[m.get_context('fork').Process(target=burn,daemon=True) for _ in range(2)]\nfor worker in workers:worker.start()\nprint(json.dumps({'workers':[w.pid for w in workers],'affinity':sorted(os.sched_getaffinity(0))}),flush=True)\nend=time.monotonic()+600\nwhile time.monotonic()<end:\n if not all(w.is_alive() for w in workers):raise RuntimeError('CPU worker exited early')\n time.sleep(.5)"
            load_command=['docker','run','-d','--name',load_name,'--network','none','--cap-drop','ALL','--security-opt','no-new-privileges','--user','65534:65534','--cpus','2','--cpuset-cpus',','.join(map(str,cpus)),'--memory','64m','--memory-swap','64m','--pids-limit','8',profile['helper_image'],'python','-u','-c',stress]
            (out/'host-load.json').write_text(json.dumps({'command':load_command,'same_host_cpus':cpus,'workers':2,'maximum_seconds':600,'note':'Separate unprivileged container; CPU contention, no memory or I/O stress'},indent=2)+'\n')
            subprocess.run(load_command,check=True,stdout=subprocess.DEVNULL,timeout=30)
            subprocess.run(['docker','inspect',load_name],check=True,stdout=(out/'host-load-inspect-before.json').open('w'),timeout=10)
            ready=False
            for _ in range(50):
                logs=subprocess.run(['docker','logs',load_name],capture_output=True,text=True,timeout=5)
                if logs.stdout.strip():
                    state=json.loads(logs.stdout.strip().splitlines()[0])
                    ready=len(state.get('workers',[]))==2 and state.get('affinity')==cpus
                    break
                time.sleep(.1)
            if not ready:raise RuntimeError('CPU load workers did not confirm startup')
        with (out/'supervisor.log').open('wb') as log:
            code=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=deadline).returncode
    except subprocess.TimeoutExpired:
        code=124
        (out/'watchdog.json').write_text(json.dumps({'deadline_seconds':deadline,'status':'failed','action':'stop only this named helper container'})+'\n')
    finally:
        if args.host_load:
            diagnostics=[(['docker','inspect',load_name],'host-load-inspect-after.json'),(['docker','logs',load_name],'host-load.log'),(['docker','stats','--no-stream','--format','{{json .}}',load_name],'host-load-stats.json')]
            try:
                for command_log,filename in diagnostics:
                    try:
                        with (out/filename).open('w') as log:subprocess.run(command_log,stdout=log,stderr=subprocess.DEVNULL,timeout=10)
                    except subprocess.TimeoutExpired:code=1
            finally:
                subprocess.run(['docker','rm','-f',load_name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
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
