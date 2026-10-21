#!/usr/bin/env python3
"""Trusted lab supervisor. Run only in the dedicated, capped helper container."""
import argparse
import collections
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from boot_check import guest_evidence, normalize_console, digest
from jailer_runtime import request, runtime_is_hardened, api_filter_ready
from helper_memory import release_stopped_clones
from startup_pid import read_startup_pid

CG = Path('/sys/fs/cgroup')
INPUT = Path('/inputs')
OUTPUT = Path('/outputs')
PROFILE = json.loads(Path('/profile.json').read_text())
UID, GID = PROFILE['uid'], PROFILE['gid']


def check(value, message):
    if not value:
        raise AssertionError(message)


def kv(path):
    return {line.split()[0]:int(line.split()[1]) for line in path.read_text().splitlines()}


def status(pid):
    return dict(line.split(':',1) for line in Path(f'/proc/{pid}/status').read_text().splitlines())




def new_netns():
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.unshare(0x40000000):
        raise OSError(ctypes.get_errno(),'unshare network namespace')


class VM:
    def __init__(self, name, netns, memory=None, no_file=None, no_seccomp=False):
        self.name=name
        self.memory_max=memory or PROFILE['memory_max']
        self.no_file=no_file or PROFILE['no_file']
        self.directory=OUTPUT/name
        self.directory.mkdir()
        self.root=self.directory/'jails'/'firecracker'/name/'root'
        self.root.mkdir(parents=True)
        self.cg=CG/'vms'/name
        self.raw=bytearray()
        self.report={'scenario':name,'status':'failed','checks':[],'cleanup_forced_stop':False}
        self.pid=None
        self.exit_code=None
        self.token=uuid.uuid4().hex
        for src,dst,mode in [('vmlinux','vmlinux',0o444),('rootfs.ext4','rootfs.ext4',0o600)]:
            shutil.copyfile(INPUT/src,self.root/dst)
            os.chown(self.root/dst,UID,GID)
            (self.root/dst).chmod(mode)
        self.report['clone_sha256_before']=digest(self.root/'rootfs.ext4')
        check(self.report['clone_sha256_before']==digest(INPUT/'rootfs.ext4'),'clone differs from input')
        (self.root/'probe.ext4').write_bytes(b'\0'*4096)
        os.chown(self.root/'probe.ext4',UID,GID)
        self.socket=self.root/'api.socket'
        self.canary=Path('/tmp')/('outside-'+name)
        self.canary.write_text('Disposable outside-jail canary\n')
        os.chown(self.canary,UID,GID)
        self.canary.chmod(0o600)
        self.canary_hash=digest(self.canary)
        self.canary_fd=os.open(self.canary,os.O_RDONLY)
        os.set_inheritable(self.canary_fd,True)
        self.master,slave=pty.openpty()
        os.set_blocking(self.master,False)
        self.selector=selectors.DefaultSelector()
        self.selector.register(self.master,selectors.EVENT_READ)
        self.log=(self.directory/'console.log').open('wb')
        args=[str(INPUT/'jailer'),'--id',name,'--exec-file',str(INPUT/'firecracker'),'--uid',str(UID),'--gid',str(GID),'--cgroup-version','2','--parent-cgroup','vms','--cgroup','cpu.max='+PROFILE['cpu_max'],'--cgroup','memory.max='+str(memory or PROFILE['memory_max']),'--cgroup','memory.swap.max=0','--cgroup','pids.max='+str(PROFILE['pids_max']),'--resource-limit','no-file='+str(no_file or PROFILE['no_file']),'--chroot-base-dir',str(self.directory/'jails'),'--netns',netns,'--new-pid-ns','--','--api-sock','/api.socket']
        if no_seccomp:args+=['--no-seccomp']
        env=os.environ.copy();env['JAILER_TEST_CANARY']=self.token
        def no_new_privileges():
            libc=ctypes.CDLL(None,use_errno=True)
            if libc.prctl(38,1,0,0,0):raise OSError(ctypes.get_errno(),'PR_SET_NO_NEW_PRIVS')
        self.launch=subprocess.Popen(args,stdin=slave,stdout=slave,stderr=slave,env=env,pass_fds=(self.canary_fd,),start_new_session=True,preexec_fn=no_new_privileges)
        os.close(slave)
        deadline=time.monotonic()+10
        self.report['pidfile_readiness']={'pending_reads':0}
        while time.monotonic()<deadline:
            self.capture(.05)
            pidfile=self.root/'firecracker.pid'
            published_pid=read_startup_pid(pidfile)
            if published_pid is None:self.report['pidfile_readiness']['pending_reads']+=1
            else:self.pid=published_pid
            if self.pid and self.socket.exists():break
            if self.launch.poll() not in (None,0):raise RuntimeError('Jailer startup failed: '+normalize_console(self.raw)[-1500:])
        check(self.pid and self.socket.exists(),'jailed API did not start')
        self.start_time=Path(f'/proc/{self.pid}/stat').read_text().split()[21]

    def capture(self,timeout=.1):
        for key,_ in self.selector.select(timeout):
            try:data=os.read(key.fd,65536)
            except OSError as exc:
                if exc.errno==errno.EIO:self.selector.unregister(key.fd);continue
                raise
            if data:self.raw.extend(data);self.log.write(data);self.log.flush()
            else:self.selector.unregister(key.fd)

    def alive(self):
        if self.exit_code is not None:return False
        try:
            pid,code=os.waitpid(self.pid,os.WNOHANG)
            if pid:self.exit_code=os.waitstatus_to_exitcode(code);return False
        except ChildProcessError:pass
        return Path(f'/proc/{self.pid}').exists()

    def expect(self,value,label):
        check(value,label)
        self.report['checks'].append(label)

    def inspect(self,phase="preboot"):
        observed={}
        namespaces={key:{'supervisor':os.readlink('/proc/self/ns/'+key),'vmm':os.readlink(f'/proc/{self.pid}/ns/'+key)} for key in ['mnt','pid','net','ipc','uts','user','cgroup']}
        observed['namespaces']=namespaces
        for key in ['mnt','pid','net']:self.expect(namespaces[key]['supervisor']!=namespaces[key]['vmm'],key+' namespace differs')
        actual_root=os.stat(f'/proc/{self.pid}/root');expected_root=self.root.stat()
        self.expect((actual_root.st_dev,actual_root.st_ino)==(expected_root.st_dev,expected_root.st_ino),'process root is jail root')
        threads={p.name:status(int(p.name)) for p in Path(f'/proc/{self.pid}/task').iterdir()}
        observed['threads']={tid:{k:fields[k].strip() for k in ['Name','Uid','Gid','Groups','CapEff','CapPrm','CapInh','CapAmb','CapBnd','NoNewPrivs','Seccomp','NSpid']} for tid,fields in threads.items()}
        for fields in threads.values():
            self.expect(set(fields['Uid'].split())=={str(UID)},'thread UIDs dropped')
            self.expect(set(fields['Gid'].split())=={str(GID)},'thread GIDs dropped')
            self.expect('0' not in fields['Groups'].split(),'no supplementary root group')
            self.expect(all(int(fields[k],16)==0 for k in ['CapEff','CapPrm','CapInh','CapAmb']),'no effective/permitted/inherited/ambient capabilities')
        observed['hardened_runtime']=runtime_is_hardened(threads,UID,GID)
        observed['environment_keys']=[entry.split(b'=')[0].decode() for entry in Path(f'/proc/{self.pid}/environ').read_bytes().split(b'\0') if entry]
        self.expect('JAILER_TEST_CANARY' not in observed['environment_keys'],'inherited environment canary absent')
        targets=[]
        for fd in Path(f'/proc/{self.pid}/fd').iterdir():
            try:targets.append(os.readlink(fd))
            except FileNotFoundError:pass
        self.expect(str(self.canary) not in targets,'inherited canary file descriptor closed')
        observed['open_fd_count']=len(targets)
        observed['limits']=Path(f'/proc/{self.pid}/limits').read_text()
        self.expect(bool(re.search(r'Max open files\s+'+str(self.no_file)+r'\s+'+str(self.no_file),observed['limits'])),'RLIMIT_NOFILE configured')
        observed['cgroup']=Path(f'/proc/{self.pid}/cgroup').read_text().strip()
        self.expect(observed['cgroup']=='0::/vms/'+self.name,'VMM resides in its cgroup leaf')
        observed['resource_files']={key:(self.cg/key).read_text().strip() for key in ['cpu.max','memory.max','memory.swap.max','pids.max']}
        self.expect(observed['resource_files']=={'cpu.max':PROFILE['cpu_max'],'memory.max':str(self.memory_max),'memory.swap.max':'0','pids.max':str(PROFILE['pids_max'])},'all cgroup limits match requested profile')
        observed['network_devices']=Path(f'/proc/{self.pid}/net/dev').read_text()
        self.expect(status(self.pid)['NSpid'].split()[-1]=='1','VMM is PID 1 in its namespace')
        self.expect(all(fields['NoNewPrivs'].strip()=='1' for fields in threads.values()),'no_new_privs active before and after boot')
        self.report.setdefault('runtime_snapshots',{})[phase]=observed
        self.report['runtime']=observed
        return observed

    def wait_api_filter(self):
        started=time.monotonic();samples=[]
        self.report['api_filter_readiness']={'confirmed':False,'deadline_seconds':5,'samples':samples}
        while time.monotonic()-started<5:
            rows={}
            for task in Path(f'/proc/{self.pid}/task').glob('*'):
                try:
                    fields=status(int(task.name));rows[task.name]={k:fields[k].strip() for k in ('Name','Seccomp','NoNewPrivs')}
                except (OSError,KeyError):continue
            samples.append({'seconds':round(time.monotonic()-started,6),'threads':rows})
            if api_filter_ready(rows):
                self.report['api_filter_readiness'].update(confirmed=True,seconds=round(time.monotonic()-started,6));return
            check(self.alive(),'VMM exited before API seccomp readiness')
            self.capture(.01)
        raise TimeoutError('API thread seccomp readiness deadline exceeded')

    def boot(self):
        for endpoint,body in [('/boot-source',{'kernel_image_path':'/vmlinux','boot_args':'console=ttyS0 reboot=k panic=1 pci=off root=/dev/vda rw init=/sbin/init'}),('/drives/rootfs',{'drive_id':'rootfs','path_on_host':'/rootfs.ext4','is_root_device':True,'is_read_only':False}),('/machine-config',{'vcpu_count':2,'mem_size_mib':256}),('/actions',{'action_type':'InstanceStart'})]:
            code,text=request(self.socket,'PUT',endpoint,body);self.expect(code==204,'API accepted '+endpoint+': '+str(code))
        sent=False;deadline=time.monotonic()+PROFILE['boot_timeout']
        command=("printf '\\nFC_BEGIN_%s\\n' "+self.token+"; printf 'uid='; id -u; printf 'kernel='; uname -r; printf 'rootfs='; awk '$2==\"/\"{print $1,$3,$4}' /proc/mounts; printf 'root_device='; awk '$5==\"/\"{print $3}' /proc/self/mountinfo; printf 'vda_device='; cat /sys/class/block/vda/dev; printf 'FC_END_%s\\n' "+self.token+"\n").encode()
        while time.monotonic()<deadline:
            self.capture()
            if not sent and re.search(r'^root@[^\n]*# ?',normalize_console(self.raw),re.M):os.write(self.master,command);sent=True
            evidence=guest_evidence(self.raw,self.token)
            if evidence:self.report['guest']=evidence;self.expect(True,'complete guest evidence');return
            check(self.alive(),'VMM exited before guest response')
        raise TimeoutError('guest response deadline exceeded')

    def wait_keyboard(self):
        if self.report.get('keyboard_readiness',{}).get('confirmed'):return
        program='/^N: Name="AT Raw Set 2 keyboard"/ {kbd=1} /^H: Handlers=/ {if(kbd && $0 ~ /[ =]kbd( |$)/)ready=1} /^$/ {kbd=0} END {exit !ready}'
        command=("while ! awk '"+program+"' /proc/bus/input/devices; do sleep 0.1; done; printf '\\nKBD_BEGIN_%s\\n' "+self.token+"; cat /proc/bus/input/devices; printf '\\nKBD_READY_%s\\n' "+self.token+"\n")
        os.write(self.master,command.encode())
        started=time.monotonic();deadline=started+10
        pattern=r'^KBD_BEGIN_'+self.token+r'\n(.*?)\nKBD_READY_'+self.token+r'$'
        while time.monotonic()<deadline:
            self.capture(.05)
            found=re.search(pattern,normalize_console(self.raw),re.M|re.S)
            if found:
                self.report['keyboard_readiness']={'confirmed':True,'seconds':round(time.monotonic()-started,6),'input_devices':found.group(1)}
                self.expect(True,'guest keyboard kbd handler confirmed before shutdown request')
                return
            check(self.alive(),'VMM exited during keyboard readiness check')
        self.report['keyboard_readiness']={'confirmed':False,'timeout_seconds':10}
        raise TimeoutError('guest keyboard kbd handler was not confirmed')

    def stop(self):
        self.wait_keyboard()
        before=(self.cg/'cpu.max').read_text().strip()
        (self.cg/'cpu.max').write_text(PROFILE['shutdown_cpu_max'])
        after=(self.cg/'cpu.max').read_text().strip()
        self.report['shutdown_cpu_transition']={'before':before,'after':after}
        self.expect(after==PROFILE['shutdown_cpu_max'],'bounded CPU reserve applied for shutdown')
        code,_=request(self.socket,'PUT','/actions',{'action_type':'SendCtrlAltDel'})
        self.report['shutdown_http_status']=code
        self.expect(code==204,'shutdown API returned 204')
        deadline=time.monotonic()+PROFILE['shutdown_timeout']
        while self.alive() and time.monotonic()<deadline:self.capture()
        self.capture(0)
        self.expect(not self.alive(),'VMM exited within shutdown deadline')
        self.expect(self.exit_code==0,'VMM exited with zero')
        self.expect(' ro.' in normalize_console(self.raw),'root remounted read-only before exit')

    def close(self):
        if self.pid and self.alive():
            self.report['cleanup_forced_stop']=True
            # Only the dedicated VM leaf is killed, never another host cgroup.
            if (self.cg/'cgroup.kill').exists():(self.cg/'cgroup.kill').write_text('1')
            else:os.kill(self.pid,signal.SIGKILL)
            deadline=time.monotonic()+5
            while self.alive() and time.monotonic()<deadline:self.capture(.05)
        self.capture(0)
        self.report['exit_code']=self.exit_code
        self.report['console_sha256']=hashlib.sha256(self.raw).hexdigest()
        self.report['console_findings']=[line for line in normalize_console(self.raw).splitlines() if re.search(r'vulnerab|failed|error|unsupported',line,re.I)]
        self.report['outside_canary_unchanged']=digest(self.canary)==self.canary_hash
        self.canary.unlink()
        os.close(self.canary_fd);os.close(self.master);self.selector.close();self.log.close()
        self.launch.wait(timeout=5)
        for name in ['dev/kvm','dev/net/tun']:
            path=self.root/name
            if path.exists() and stat.S_ISCHR(path.lstat().st_mode):path.unlink()
        if self.cg.exists():
            self.report['final_cgroup_events']={key:kv(self.cg/key) for key in ['memory.events','pids.events','cpu.stat']}
            self.cg.rmdir()
        self.after_stop()
        (self.directory/'result.json').write_text(json.dumps(self.report,indent=2)+'\n')

    def after_stop(self):
        policy=PROFILE.get('helper_cache_policy','retain')
        check(policy in ('retain','release-stopped-clones'),'unknown helper cache policy')
        if policy=='release-stopped-clones':
            start=time.monotonic()
            self.report['cache_release']={'files':release_stopped_clones(self.root,stopped=not self.alive()),'seconds':round(time.monotonic()-start,6)}


def run_suite():
    report={'schema':1,'started_at':datetime.now(timezone.utc).isoformat(),'status':'failed','scenarios':[],'findings':[]}
    original={name:digest(INPUT/name) for name in ['firecracker','jailer','vmlinux','rootfs.ext4']}
    report['original_sha256_before']=original
    check(original['firecracker']==PROFILE['firecracker_sha256'],'unexpected Firecracker binary')
    check(original['jailer']==PROFILE['jailer_sha256'],'unexpected jailer binary')
    check(Path('/proc/self/cgroup').read_text().strip()=='0::/','expected private container cgroup namespace')
    check((CG/'memory.max').read_text().strip()=='805306368','outer container memory cap missing')
    check((CG/'pids.max').read_text().strip()=='256','outer container pids cap missing')
    report['outer_limits_before']={key:(CG/key).read_text().strip() for key in ['memory.max','pids.max','cpu.max']}
    report['outer_memory_events_before']=kv(CG/'memory.events')
    (CG/'supervisor').mkdir()
    for pid in (CG/'cgroup.procs').read_text().split():(CG/'supervisor/cgroup.procs').write_text(pid)
    (CG/'cgroup.subtree_control').write_text('+cpu +memory +pids')
    (CG/'vms').mkdir()
    keeper=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)'],preexec_fn=new_netns)
    try:
        time.sleep(.1)
        netns=f'/proc/{keeper.pid}/ns/net'
        def scenario(name,action,**options):
            vm=None
            try:
                vm=VM(name,netns,**options)
                action(vm)
                vm.report['status']='passed'
            except Exception as exc:
                if vm is None:report['findings'].append({'scenario':name,'startup_failure':str(exc)});raise
                vm.report['failure']=type(exc).__name__+': '+str(exc)
                raise
            finally:
                if vm is not None:
                    vm.close();report['scenarios'].append(vm.report)

        def baseline(vm):
            vm.inspect('preboot-initial')
            vm.wait_api_filter()
            observation=vm.inspect('preboot')
            vm.report['preboot_filter_note']='VMM main seccomp is installed during InstanceStart; API thread is filtered before boot'
            vm.expect(any(t['Name']=='fc_api' and t['Seccomp']=='2' for t in observation['threads'].values()),'preboot API thread has seccomp filter')
            for endpoint,body in [('/boot-source',{'kernel_image_path':'/inputs/vmlinux'}),('/boot-source',{'kernel_image_path':'../../../inputs/vmlinux'}),('/logger',{'log_path':str(vm.canary),'level':'Info'})]:
                code,message=request(vm.socket,'PUT',endpoint,body)
                vm.report.setdefault('path_denials',[]).append({'endpoint':endpoint,'status':code,'message':message})
                vm.expect(code==400 and ('No such file' in message or 'os error 2' in message),'outside-jail path denied with ENOENT')
            vm.boot()
            # Reinspect after boot: the vCPU threads must also carry the protections.
            vm.expect(vm.inspect('running')['hardened_runtime'],'all running VM threads retain seccomp and no_new_privs')
            before=kv(vm.cg/'cpu.stat')
            os.write(vm.master,b"while :; do :; done &\nwhile :; do :; done &\n")
            started=time.monotonic()
            while time.monotonic()-started<3:vm.capture(.1)
            after=kv(vm.cg/'cpu.stat')
            vm.report['cpu_stress']={'before':before,'after':after,'elapsed_seconds':round(time.monotonic()-started,3)}
            vm.expect(after['nr_throttled']>before['nr_throttled'],'CPU contention produced cgroup throttling')
            os.write(vm.master,("kill %1 %2; wait; printf '\\nCPU_IDLE_%s\\n' "+vm.token+"\n").encode())
            idle_deadline=time.monotonic()+5
            while not re.search(r'^CPU_IDLE_'+vm.token+r'$',normalize_console(vm.raw),re.M) and time.monotonic()<idle_deadline:vm.capture(.1)
            vm.expect(bool(re.search(r'^CPU_IDLE_'+vm.token+r'$',normalize_console(vm.raw),re.M)),'guest CPU workers stopped before PID probe and shutdown')
            pids_before=kv(vm.cg/'pids.events')
            helper="import threading,json; event=threading.Event(); threads=[]; blocked=False\ntry:\n for i in range(48):\n  t=threading.Thread(target=event.wait);t.start();threads.append(t)\nexcept RuntimeError:\n blocked=True\nfinally:\n event.set()\n for t in threads:t.join()\nprint(json.dumps({'created_threads':len(threads),'blocked':blocked}))"
            def enter_leaf():
                (vm.cg/'cgroup.procs').write_text(str(os.getpid()));os.setgroups([]);os.setgid(GID);os.setuid(UID)
            probe=subprocess.run([sys.executable,'-c',helper],preexec_fn=enter_leaf,capture_output=True,text=True,timeout=10,check=True)
            vm.report['pids_probe']=json.loads(probe.stdout)
            vm.expect(vm.report['pids_probe']['blocked'],'trusted helper encountered pids ceiling in VM cgroup')
            vm.expect(kv(vm.cg/'pids.events')['max']>pids_before['max'],'pids.events records rejected thread creation')
            vm.stop()
        scenario('baseline',baseline)
        scenario('repeat',lambda vm:(vm.boot(),vm.expect(vm.inspect('running')['hardened_runtime'],'repeat retains runtime protection'),vm.stop()))

        def seccomp_control(vm):
            vm.boot()
            observed=vm.inspect('running')
            vm.expect(not observed['hardened_runtime'],'negative control detects disabled seccomp')
            vm.report['classification']='expected policy rejection; trusted guest booted only to validate the detector, not an approved profile'
            vm.stop()
        scenario('seccomp-disabled-control',seccomp_control,no_seccomp=True)

        def fd_control(vm):
            errors=[]
            for index in range(32):
                code,text=request(vm.socket,'PUT',f'/drives/p{index}',{'drive_id':f'p{index}','path_on_host':'/probe.ext4','is_root_device':False,'is_read_only':True})
                if code!=204:errors.append({'index':index,'status':code,'message':text});break
            vm.report['fd_limit_errors']=errors
            vm.expect(bool(errors) and ('Too many open files' in errors[0]['message'] or 'os error 24' in errors[0]['message']),'real API file opens hit RLIMIT_NOFILE')
            vm.report['classification']='expected resource rejection; no guest started'
        scenario('fd-exhaustion-control',fd_control,no_file=16)

        def memory_control(vm):
            vm.boot();before=kv(vm.cg/'memory.events')
            os.write(vm.master,b"awk 'BEGIN{for(i=0;i<200000;i++)a[i]=sprintf(\"%01024d\",i);print \"ALLOC_DONE\"}'\n")
            deadline=time.monotonic()+20
            while vm.alive() and time.monotonic()<deadline:vm.capture(.1)
            after=kv(vm.cg/'memory.events')
            vm.report['memory_stress']={'before':before,'after':after,'memory_peak':(vm.cg/'memory.peak').read_text().strip()}
            vm.expect(after['oom_kill']>before['oom_kill'],'guest allocation triggered cgroup OOM kill')
            vm.expect(not vm.alive() and vm.exit_code==-signal.SIGKILL,'limited VMM was killed by SIGKILL')
            vm.report['classification']='expected OOM failure; guest is not claimed healthy or gracefully stopped'
        scenario('memory-oom-control',memory_control,memory=PROFILE['oom_memory_max'])
        def deadline_control(vm):
            vm.boot()
            started=time.monotonic()
            while time.monotonic()-started<1:vm.capture(.05)
            (vm.cg/'cgroup.kill').write_text('1')
            deadline=time.monotonic()+5
            while vm.alive() and time.monotonic()<deadline:vm.capture(.05)
            vm.report['deadline_control']={'budget_seconds':1,'elapsed_seconds':round(time.monotonic()-started,3),'action':'kill only the dedicated VM cgroup'}
            vm.expect(not vm.alive() and vm.exit_code==-signal.SIGKILL,'wall-clock budget stopped the VMM')
            vm.report['classification']='expected deadline failure; no graceful shutdown claimed'
        scenario('deadline-control',deadline_control)
        report['original_sha256_before']=original
        report['original_sha256_after']={name:digest(INPUT/name) for name in original}
        check(report['original_sha256_after']==original,'input artifacts changed')
        report['outer_memory_events_after']=kv(CG/'memory.events')
        # Outer events include descendants; local counters distinguish the container itself.
        report['outer_memory_events_local']=kv(CG/'memory.events.local')
        check(report['outer_memory_events_local']['oom_kill']==0,'outer container OOM occurred')
        report['status']='passed'
    except Exception as exc:
        report['failure']=type(exc).__name__+': '+str(exc)
    finally:
        keeper.terminate();keeper.wait(timeout=5)
        for leaf in (CG/'vms').iterdir():
            if leaf.is_dir():
                if (leaf/'cgroup.procs').read_text().strip():(leaf/'cgroup.kill').write_text('1');time.sleep(.2)
                leaf.rmdir()
        (CG/'vms/cgroup.subtree_control').write_text('-cpu -memory -pids')
        (CG/'vms').rmdir()
        (CG/'cgroup.subtree_control').write_text('-cpu -memory -pids')
        (CG/'cgroup.procs').write_text(str(os.getpid()))
        (CG/'supervisor').rmdir()
        report['cgroups_cleaned']=True
        report['original_sha256_after']={name:digest(INPUT/name) for name in original}
        report['originals_unchanged']=report['original_sha256_after']==original
        if not report['originals_unchanged']:report['status']='failed';report['integrity_failure']='original input hash changed'
        (OUTPUT/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2),flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(run_suite())
