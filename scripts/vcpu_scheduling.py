#!/usr/bin/env python3
"""Non-ptrace scheduler/signal capture in a private tracefs instance.

The trusted helper owns tracing; the jailed VMM gets no tracing access.
"""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from jailer_checks import CG, INPUT, OUTPUT, PROFILE, UID, GID, VM, check, digest, kv, new_netns, normalize_console
from shutdown_investigation import shutdown


class Capture:
    def __init__(self, vm):
        self.vm = vm
        self.path = None
        self.directory = vm.directory / 'scheduler'
        self.directory.mkdir()
        self.meta = {'status':'starting', 'ptrace':False, 'events':{}, 'cleanup':False}
        vm.report['scheduler_capture'] = self.meta

    def start(self):
        self.path = Path('/trace/instances') / ('fc-lab-'+uuid.uuid4().hex)
        self.path.mkdir()
        self.write('tracing_on', '0')
        self.write('trace_clock', 'mono')
        self.write('buffer_size_kb', '1024')
        # Discover tracepoint TIDs by the two distinct vCPU names, then narrow
        # every shutdown event to those numeric IDs. Reject ambiguous discovery.
        switch = self.path/'events/sched/sched_switch'
        discovery_filter = 'prev_comm ~ "fc_vcpu*" || next_comm ~ "fc_vcpu*"'
        (switch/'filter').write_text(discovery_filter)
        (switch/'enable').write_text('1')
        self.meta['events']['sched/sched_switch'] = discovery_filter
        self.write('tracing_on', '1')
        os.write(self.vm.master, b'while :; do :; done &\nwhile :; do :; done &\n')
        start=time.monotonic()
        while time.monotonic()-start<2:
            self.vm.capture(.02)
        self.write('tracing_on', '0')
        discovery = (self.path/'trace').read_text()
        (self.directory/'discovery.trace').write_text(discovery)
        names = {}
        for name, tid in re.findall(r'(?:prev|next)_comm=(fc_vcpu [01]) (?:prev|next)_pid=(\d+)', discovery):
            names.setdefault(name,set()).add(int(tid))
        check(set(names)=={'fc_vcpu 0','fc_vcpu 1'} and all(len(v)==1 for v in names.values()), 'ambiguous or missing vCPU discovery')
        self.meta['threads'] = [{'trace_tid':next(iter(v)), 'name':n} for n,v in sorted(names.items())]
        self.meta['discovery_sha256'] = digest(self.directory/'discovery.trace')
        ids = [t['trace_tid'] for t in self.meta['threads']]
        os.write(self.vm.master,b'kill %1 %2\n')
        def filt(field):
            return '('+' || '.join(f'{field} == {pid}' for pid in ids)+')'
        events = {'sched/sched_switch':filt('prev_pid')+' || '+filt('next_pid'),
                  'sched/sched_wakeup':filt('pid'),
                  'sched/sched_waking':filt('pid'),
                  'sched/sched_process_exit':filt('pid'),
                  'signal/signal_generate':filt('pid'),
                  'signal/signal_deliver':filt('common_pid'),
                  'kvm/kvm_entry':filt('common_pid'),
                  'kvm/kvm_exit':filt('common_pid')}
        for event, expression in events.items():
            path = self.path/'events'/event
            check(path.exists(), 'missing required tracepoint '+event)
            (self.directory/(event.replace('/','-')+'.format')).write_text((path/'format').read_text())
            (path/'filter').write_text(expression)
            actual=(path/'filter').read_text().strip()
            check(actual == expression, 'trace filter readback mismatch: '+event)
            (path/'enable').write_text('1')
            self.meta['events'][event] = actual
        self.write('trace', '')
        self.meta['clock'] = (self.path/'trace_clock').read_text().strip()
        self.meta['status'] = 'configured'

    def begin_shutdown(self):
        self.meta['start_monotonic'] = time.monotonic()
        self.write('tracing_on', '1')
        self.write('trace_marker', 'shutdown-request-begin')
        self.meta['status'] = 'capturing'

    def write(self, name, value):
        (self.path/name).write_text(value)

    def close(self):
        if self.path is None:
            return
        try:
            if (self.path/'tracing_on').read_text().strip()=='1':
                self.write('trace_marker', 'shutdown-observation-end')
            self.write('tracing_on', '0')
            raw = (self.path/'trace').read_bytes()
            (self.directory/'events.trace').write_bytes(raw)
            self.meta['raw_sha256'] = digest(self.directory/'events.trace')
            self.meta['raw_bytes'] = len(raw)
            self.meta['buffer_stats'] = {p.parent.name:p.read_text() for p in (self.path/'per_cpu').glob('cpu*/stats')}
            self.meta['stop_monotonic'] = time.monotonic()
            check(b'sched_switch:' in raw and b'signal_generate:' in raw and b'sched_process_exit:' in raw, 'capture lacks required scheduler/signal/exit events')
            text = raw.decode()
            for thread in self.meta['threads']:
                tid = thread['trace_tid']
                check(re.search(r'signal_generate: sig=35 .* pid='+str(tid)+r' ', text), 'missing vCPU kick')
                check(re.search(r'-'+str(tid)+r'\s+\[.*signal_deliver: sig=35 ', text), 'missing vCPU kick delivery')
                check(re.search(r'sched_process_exit: .* pid='+str(tid)+r' ', text), 'missing vCPU exit')
            for stats in self.meta['buffer_stats'].values():
                for line in stats.splitlines():
                    key, _, value = line.partition(':')
                    if key.strip() in ('overrun', 'commit overrun', 'dropped events'):
                        check(int(value)==0, 'trace buffer lost events')
            self.meta['status'] = 'validated'
        except Exception:
            self.meta['status'] = 'invalid'
            raise
        finally:
            self.write('tracing_on', '0')
            for event in self.meta['events']:
                self.write('events/'+event+'/enable', '0')
            self.path.rmdir()
            self.meta['cleanup'] = True


def main():
    report={'schema':1, 'host_kernel':os.uname().release, 'started_at':datetime.now(timezone.utc).isoformat(), 'status':'failed', 'scenarios':[], 'findings':[], 'original_sha256_before':{n:digest(INPUT/n) for n in ['firecracker','jailer','vmlinux','rootfs.ext4']}}
    check(report['original_sha256_before']['firecracker']==PROFILE['firecracker_sha256'], 'unexpected VMM')
    check(report['original_sha256_before']['jailer']==PROFILE['jailer_sha256'], 'unexpected jailer')
    check(Path('/proc/self/cgroup').read_text().strip()=='0::/', 'private cgroup required')
    for name, expected in [('memory.max','805306368'),('pids.max','256'),('cpu.max','200000 100000')]:
        check((CG/name).read_text().strip()==expected, 'outer limit missing: '+name)
    trace_root=Path('/trace'); trace_root.mkdir()
    subprocess.run(['mount','-t','tracefs','tracefs',str(trace_root)], check=True)
    (CG/'supervisor').mkdir()
    for pid in (CG/'cgroup.procs').read_text().split():
        (CG/'supervisor/cgroup.procs').write_text(pid)
    (CG/'cgroup.subtree_control').write_text('+cpu +memory +pids'); (CG/'vms').mkdir()
    keeper=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)'],preexec_fn=new_netns)
    try:
        count=int(PROFILE.get('diagnostic_repetitions',5))
        check(1<=count<=6, 'repetitions must be 1..6')
        report['affinity']=sorted(os.sched_getaffinity(0))
        for repeat in range(count):
            arms=[('half','50000 100000',False),('half','50000 100000',True),('boost','200000 100000',False),('boost','200000 100000',True)]
            if repeat%2: arms.reverse()
            for label, quota, traced in arms:
                name=f'{label}-'+('trace' if traced else 'control')+f'-{repeat+1:02d}'
                vm=None; capture=None
                try:
                    vm=VM(name,f'/proc/{keeper.pid}/ns/net'); vm.boot()
                    vm.expect(vm.inspect('running')['hardened_runtime'], 'all runtime threads hardened')
                    vm.wait_keyboard()
                    if not traced:
                        os.write(vm.master,b'while :; do :; done &\nwhile :; do :; done &\n')
                        start=time.monotonic()
                        while time.monotonic()-start<2: vm.capture(.05)
                        os.write(vm.master,b'kill %1 %2\n')
                    vm.report.update(guest_load=True, worker_stop_ack=False, traced=traced)
                    if traced:
                        capture=Capture(vm); capture.start()
                    if PROFILE.get('diagnostic_pid_probe',False):
                        helper="import threading,json; event=threading.Event(); threads=[]; blocked=False\ntry:\n for i in range(48):\n  t=threading.Thread(target=event.wait);t.start();threads.append(t)\nexcept RuntimeError:\n blocked=True\nfinally:\n event.set()\n for t in threads:t.join()\nprint(json.dumps({'created_threads':len(threads),'blocked':blocked}))"
                        def enter_leaf():
                            (vm.cg/'cgroup.procs').write_text(str(os.getpid()))
                            os.setgroups([]); os.setgid(GID); os.setuid(UID)
                        probe=subprocess.run([sys.executable,'-c',helper],preexec_fn=enter_leaf,capture_output=True,text=True,timeout=10,check=True)
                        vm.report['pids_probe']=json.loads(probe.stdout)
                        vm.expect(vm.report['pids_probe']['blocked'], 'PID ceiling encountered')
                        vm.expect(vm.inspect('after-probe')['hardened_runtime'], 'runtime hardened after PID probe')
                    if capture: capture.begin_shutdown()
                    shutdown(vm,quota)
                    check(vm.exit_code==0, 'unexpected shutdown exit')
                    vm.report['status']='observed'
                except Exception as exc:
                    if vm is None: raise
                    vm.report['failure']=type(exc).__name__+': '+str(exc)
                finally:
                    if capture:
                        try: capture.close()
                        except Exception as exc:
                            vm.report['capture_failure']=repr(exc)
                            vm.report.setdefault('failure', 'capture: '+repr(exc))
                    if vm:
                        vm.report['panic']='Timed out waiting for vCPU thread' in normalize_console(vm.raw)
                        vm.close(); report['scenarios'].append(vm.report)
                if 'failure' in vm.report:
                    report['findings'].append({'scenario':name, 'failure':vm.report['failure']})
        report['status']='failed' if report['findings'] else 'passed'
    except Exception as exc:
        report['failure']=type(exc).__name__+': '+str(exc)
    finally:
        keeper.terminate(); keeper.wait(timeout=5)
        for leaf in (CG/'vms').iterdir():
            if leaf.is_dir():
                if (leaf/'cgroup.procs').read_text().strip():
                    (leaf/'cgroup.kill').write_text('1'); time.sleep(.2)
                leaf.rmdir()
        (CG/'vms/cgroup.subtree_control').write_text('-cpu -memory -pids'); (CG/'vms').rmdir()
        (CG/'cgroup.subtree_control').write_text('-cpu -memory -pids'); (CG/'cgroup.procs').write_text(str(os.getpid())); (CG/'supervisor').rmdir()
        subprocess.run(['umount',str(trace_root)],check=True)
        report['original_sha256_after']={n:digest(INPUT/n) for n in report['original_sha256_before']}
        report['originals_unchanged']=report['original_sha256_before']==report['original_sha256_after']
        report['outer_memory_events_local']=kv(CG/'memory.events.local')
        report['cgroups_cleaned']=True
        if not report['originals_unchanged'] or report['outer_memory_events_local']['oom_kill']!=0: report['status']='failed'
        (OUTPUT/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['status']=='passed' else 1

if __name__=='__main__': raise SystemExit(main())
