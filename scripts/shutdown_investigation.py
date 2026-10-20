#!/usr/bin/env python3
"""Bounded diagnostic matrix, including deliberately induced teardown failures.

Linux x86_64 only. The ptrace control holds a vCPU at the teardown log write;
it is fault injection, not a reproduction of the historical scheduling cause.
"""
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from jailer_checks import CG, INPUT, OUTPUT, PROFILE, UID, GID, VM, check, digest, kv, new_netns, normalize_console, request

libc=ctypes.CDLL(None,use_errno=True)
libc.ptrace.restype=ctypes.c_long

def ptrace(op,pid,addr=0,data=0):
    result=libc.ptrace(op,pid,ctypes.c_void_p(addr),ctypes.c_void_p(data))
    if result==-1:raise OSError(ctypes.get_errno(),f'ptrace {op} pid {pid}')
    return result

def registers(pid):
    buf=(ctypes.c_ulonglong*27)()
    ptrace(12,pid,data=ctypes.addressof(buf))
    keys='r15 r14 r13 r12 rbp rbx r11 r10 r9 r8 rax rcx rdx rsi rdi orig_rax rip cs eflags rsp ss fs_base gs_base ds es fs gs'.split()
    return dict(zip(keys,buf))

def trace_stop(vm,inject):
    """Trace main thread only; optionally freeze one live vCPU during teardown."""
    pid=vm.pid
    events=[];held=[];request_result={};injected=False; samples=[]; next_sample=0; quota_injected_at=None; hold_until=None
    mem=os.open(f'/proc/{pid}/mem',os.O_RDONLY)
    maps=Path(f'/proc/{pid}/maps').read_text()
    ptrace(0x4206,pid) # SEIZE
    ptrace(0x4207,pid) # INTERRUPT
    os.waitpid(pid,0x40000000)
    ptrace(24,pid) # SYSCALL
    def shutdown():
        try:request_result['status'],request_result['body']=request(vm.socket,'PUT','/actions',{'action_type':'SendCtrlAltDel'})
        except Exception as exc:request_result['error']=str(exc)
    worker=threading.Thread(target=shutdown);worker.start()
    start=time.monotonic();entry=True
    try:
        while time.monotonic()-start<PROFILE['shutdown_timeout']:
            vm.capture(0)
            elapsed=time.monotonic()-start
            if elapsed>=next_sample:
                samples.append(observe(vm,elapsed));next_sample=elapsed+.02
            if hold_until is not None and elapsed>=hold_until:
                for target in held:
                    try:ptrace(17,target)
                    except OSError:pass
                held.clear();hold_until=None
                events.append({'t':round(elapsed,6),'action':'release-held-vcpu'})
            if inject=='quota-recover' and quota_injected_at is not None and elapsed-quota_injected_at>.25:
                (vm.cg/'cpu.max').write_text(PROFILE['shutdown_cpu_max'])
                events.append({'t':round(elapsed,6),'action':'restore-bounded-shutdown-quota'})
                quota_injected_at=None
            tid,st=os.waitpid(pid,os.WNOHANG|0x40000000)
            if not tid:time.sleep(.001);continue
            if os.WIFEXITED(st) or os.WIFSIGNALED(st):
                vm.exit_code=os.waitstatus_to_exitcode(st);break
            sig=os.WSTOPSIG(st)
            r=registers(pid)
            ev={'t':round(time.monotonic()-start,6),'signal':sig,'syscall':r['orig_rax'],'rax':r['rax'],'rip':hex(r['rip']),'args':[r[k] for k in ['rdi','rsi','rdx','r10','r8','r9']]}
            entry=r['rax']==(1<<64)-38 if sig==signal.SIGTRAP else entry
            if sig==signal.SIGTRAP:
                if entry and r['orig_rax']==1:
                    data=os.pread(mem,min(r['rdx'],4096),r['rsi'])
                    ev['write']=data.decode(errors='replace')
                    if inject in ('quota','quota-recover') and not injected and b'Killing vCPU threads' in data:
                        (vm.cg/'cpu.max').write_text('1000 1000000')
                        injected=True;quota_injected_at=time.monotonic()-start
                        ev['injected_quota']='1000 1000000'
                    if inject in (True,'hold-short','hold-long') and not injected and b'Killing vCPU threads' in data:
                        for task in sorted(Path(f'/proc/{pid}/task').iterdir()):
                            if task.joinpath('comm').read_text().strip().startswith('fc_vcpu'):
                                target=int(task.name)
                                try:
                                    ptrace(0x4206,target);ptrace(0x4207,target)
                                    os.waitpid(target,0x40000000)
                                    held.append(target);injected=True
                                    ev['held_vcpu']=target
                                    vm.report['injected_vcpu_tid']=target
                                    if inject in ('hold-short','hold-long'):
                                        hold_until=time.monotonic()-start+(1.5 if inject=='hold-short' else 6)
                                        ev['hold_seconds']=1.5 if inject=='hold-short' else 6
                                    break
                                except ProcessLookupError:continue
                ptrace(24,pid)
            else:
                info=(ctypes.c_ubyte*128)()
                ptrace(0x4202,pid,data=ctypes.addressof(info))
                ev['siginfo_hex']=bytes(info).hex()
                try:ev['instruction_hex']=os.pread(mem,16,r['rip']).hex()
                except OSError:pass
                ev['registers']=r
                if sig==signal.SIGSEGV:
                    events.append(ev)
                    # Release traced siblings before letting the fatal main fault proceed.
                    # A traced sibling may already have exited; its wait status must be reaped.
                    retired=list(held)
                    for target in retired:
                        try:ptrace(17,target)
                        except OSError as exc:events.append({'action':'detach-vcpu-error','tid':target,'errno':exc.errno})
                    held.clear()
                    # Suppress the debugger delivery and let HLT fault afresh after detaching.
                    ptrace(17,pid)
                    while time.monotonic()-start<PROFILE['shutdown_timeout']:
                        for target in retired:
                            try:
                                tid,status=os.waitpid(target,os.WNOHANG|0x40000000)
                                if tid:events.append({'action':'reap-traced-vcpu','tid':tid,'wait_status':status})
                            except ChildProcessError:pass
                        if not vm.alive():break
                        vm.capture(.01)
                    break
                ptrace(24,pid,data=sig)
            events.append(ev)
        worker.join(timeout=2)
        vm.capture(0)
        vm.report['trace']={'architecture':'x86_64','maps':maps,'events':events,'injection_requested':inject,'held_vcpu_tids':held,'http':request_result,'samples':samples}
        check(vm.exit_code is not None,'trace exceeded shutdown deadline')
        check(request_result.get('status')==204,'shutdown API did not return 204')
        check(' ro.' in normalize_console(vm.raw),'guest did not remount root read-only')
        if inject in (True,'hold-short','hold-long'):
            check(injected,'no live vCPU available for controlled hold')
            panic=b'Timed out waiting for vCPU thread' in vm.raw
            expected_panic=inject!='hold-short' or PROFILE.get('diagnostic_join_timeout_seconds',1)==1
            check(panic==expected_panic,'unexpected bounded-hold result')
            check(vm.exit_code==(-11 if expected_panic else 0),'unexpected bounded-hold exit')
            vm.report['expected_result_matched']=True
        return vm.exit_code
    finally:
        os.close(mem)
        for tid in held:
            try:ptrace(17,tid)
            except OSError:pass
        if vm.exit_code is None:
            try:ptrace(17,pid)
            except OSError:pass


def observe(vm,elapsed):
    threads={}
    for task in Path(f'/proc/{vm.pid}/task').glob('*'):
        try:
            threads[task.name]={'name':(task/'comm').read_text().strip(),'state':next(l for l in (task/'status').read_text().splitlines() if l.startswith('State:')),'schedstat':(task/'schedstat').read_text().strip(),'wchan':(task/'wchan').read_text().strip(),'syscall':(task/'syscall').read_text().strip()}
        except (OSError,StopIteration):continue
    return {'t':round(elapsed,6),'cpu_max':(vm.cg/'cpu.max').read_text().strip(),'cpu_stat':kv(vm.cg/'cpu.stat'),'threads':threads}

def shutdown(vm,quota):
    (vm.cg/'cpu.max').write_text(quota)
    vm.report['shutdown_quota']=quota
    before=kv(vm.cg/'cpu.stat');start=time.monotonic();samples=[];next_sample=0
    code,_=request(vm.socket,'PUT','/actions',{'action_type':'SendCtrlAltDel'})
    while vm.alive() and time.monotonic()-start<PROFILE['shutdown_timeout']:
        vm.capture(.01)
        elapsed=time.monotonic()-start
        if elapsed>=next_sample:
            samples.append(observe(vm,elapsed));next_sample=elapsed+.05
    vm.capture(0)
    vm.report['shutdown_observation']={'http':code,'seconds':round(time.monotonic()-start,6),'cpu_before':before,'cpu_after':kv(vm.cg/'cpu.stat'),'samples':samples}
    check(not vm.alive(),'shutdown deadline exceeded')
    check(code==204,'shutdown API did not return 204')
    check(' ro.' in normalize_console(vm.raw),'guest did not remount root read-only')


def main():
    check(os.uname().machine=='x86_64','tracer requires Linux x86_64')
    report={'schema':1,'host_kernel':os.uname().release,'started_at':datetime.now(timezone.utc).isoformat(),'status':'failed','scenarios':[],'findings':[],'original_sha256_before':{n:digest(INPUT/n) for n in ['firecracker','jailer','vmlinux','rootfs.ext4']}}
    check(report['original_sha256_before']['firecracker']==PROFILE['firecracker_sha256'],'unexpected VMM')
    check(report['original_sha256_before']['jailer']==PROFILE['jailer_sha256'],'unexpected jailer')
    check(Path('/proc/self/cgroup').read_text().strip()=='0::/','private cgroup required')
    check((CG/'memory.max').read_text().strip()=='805306368','outer memory ceiling missing')
    check((CG/'pids.max').read_text().strip()=='256','outer task ceiling missing')
    check((CG/'cpu.max').read_text().strip()=='200000 100000','outer CPU ceiling missing')
    (CG/'supervisor').mkdir()
    for pid in (CG/'cgroup.procs').read_text().split():(CG/'supervisor/cgroup.procs').write_text(pid)
    (CG/'cgroup.subtree_control').write_text('+cpu +memory +pids');(CG/'vms').mkdir()
    keeper=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)'],preexec_fn=new_netns)
    try:
        cases=[('half-idle-1','50000 100000',False,None),('half-load-1','50000 100000',True,None),('half-load-2','50000 100000',True,None),('boost-load','200000 100000',True,None),('trace-clean','200000 100000',False,False),('trace-held-vcpu','200000 100000',False,True),('quota-starved','200000 100000',False,'quota'),('quota-recovered','200000 100000',False,'quota-recover')]
        if PROFILE.get('investigation_matrix')=='deadline':
            cases=[('deadline-idle','50000 100000',False,None),('deadline-load','200000 100000',True,None),('deadline-short-hold','200000 100000',False,'hold-short'),('deadline-long-hold','200000 100000',False,'hold-long')]
        if PROFILE.get('investigation_matrix')=='host-load':
            count=int(PROFILE.get('diagnostic_repetitions',10))
            check(1<=count<=12,'repetitions must be between 1 and 12')
            cases=[(f'host-repeat-{i+1:02d}','200000 100000',bool(i%2),None) for i in range(count)]+[('host-short-hold','200000 100000',False,'hold-short'),('host-long-hold','200000 100000',False,'hold-long')]
        report['host_cpu_pressure_before']=Path('/proc/pressure/cpu').read_text()
        report['affinity']=sorted(os.sched_getaffinity(0))
        report['matrix']=PROFILE.get('investigation_matrix','initial')
        report['diagnostic_join_timeout_seconds']=PROFILE.get('diagnostic_join_timeout_seconds',1)
        for name,quota,load,trace in cases:
            vm=None
            try:
                vm=VM(name,f'/proc/{keeper.pid}/ns/net');vm.boot()
                vm.expect(vm.inspect('running')['hardened_runtime'],'all running threads retain hardened runtime')
                vm.wait_keyboard()
                if load:
                    os.write(vm.master,b'while :; do :; done &\nwhile :; do :; done &\n')
                    start=time.monotonic()
                    while time.monotonic()-start<3:vm.capture(.05)
                    os.write(vm.master,b'kill %1 %2\n')
                    # Historical command: deliberately no wait/ack. Reported as such.
                if load and report['matrix']=='host-load':
                    helper="import threading,json; event=threading.Event(); threads=[]; blocked=False\ntry:\n for i in range(48):\n  t=threading.Thread(target=event.wait);t.start();threads.append(t)\nexcept RuntimeError:\n blocked=True\nfinally:\n event.set()\n for t in threads:t.join()\nprint(json.dumps({'created_threads':len(threads),'blocked':blocked}))"
                    def enter_leaf():
                        (vm.cg/'cgroup.procs').write_text(str(os.getpid()));os.setgroups([]);os.setgid(GID);os.setuid(UID)
                    probe=subprocess.run([sys.executable,'-c',helper],preexec_fn=enter_leaf,capture_output=True,text=True,timeout=10,check=True)
                    vm.report['pids_probe']=json.loads(probe.stdout)
                    vm.expect(vm.report['pids_probe']['blocked'],'trusted PID probe encountered VM task ceiling')
                    vm.expect(vm.inspect('after-probe')['hardened_runtime'],'runtime protection retained after PID probe')
                vm.report['guest_load']=load;vm.report['worker_stop_ack']=False if load else None
                (vm.cg/'cpu.max').write_text(quota)
                if trace is None:
                    shutdown(vm,quota)
                    if report['matrix'] in ('deadline','host-load'):check(vm.exit_code==0,'unexpected shutdown failure')
                else:trace_stop(vm,trace)
                vm.report['panic']='Timed out waiting for vCPU thread' in normalize_console(vm.raw)
                vm.report['status']='observed'
            except Exception as exc:
                if vm is None:raise
                vm.report['failure']=type(exc).__name__+': '+str(exc)
                vm.report['panic']='Timed out waiting for vCPU thread' in normalize_console(vm.raw)
            finally:
                if vm:
                    vm.close();report['scenarios'].append(vm.report)
            if 'failure' in vm.report:report['findings'].append({'scenario':name,'failure':vm.report['failure']})
        report['status']='failed' if report['findings'] else 'passed'
    except Exception as exc:report['failure']=type(exc).__name__+': '+str(exc)
    finally:
        keeper.terminate();keeper.wait(timeout=5)
        for leaf in (CG/'vms').iterdir():
            if leaf.is_dir():
                if (leaf/'cgroup.procs').read_text().strip():(leaf/'cgroup.kill').write_text('1');time.sleep(.2)
                leaf.rmdir()
        (CG/'vms/cgroup.subtree_control').write_text('-cpu -memory -pids');(CG/'vms').rmdir()
        (CG/'cgroup.subtree_control').write_text('-cpu -memory -pids');(CG/'cgroup.procs').write_text(str(os.getpid()));(CG/'supervisor').rmdir()
        report['original_sha256_after']={n:digest(INPUT/n) for n in report['original_sha256_before']}
        report['originals_unchanged']=report['original_sha256_before']==report['original_sha256_after']
        report['outer_memory_events_local']=kv(CG/'memory.events.local')
        report['host_cpu_pressure_after']=Path('/proc/pressure/cpu').read_text()
        report['cgroups_cleaned']=True
        if not report['originals_unchanged'] or report['outer_memory_events_local']['oom_kill']!=0:report['status']='failed'
        (OUTPUT/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
