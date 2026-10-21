#!/usr/bin/env python3
"""Official-binary shutdown probes and kernel stacks, retaining jailer policy."""
import json
import os
from pathlib import Path
import re
import time
import uuid

import vcpu_scheduling as scheduling
from jailer_checks import INPUT, OUTPUT, PROFILE, check, digest, kv, normalize_console, request
from protocol_points import POINTS, BUILD_ID, verify_binary
from protocol_analysis import analyze_protocol

def probe_command(command):
    # Python append-mode file opening seeks EOF, rejected by this control file.
    # Open without O_TRUNC: append/remove only our explicitly named definitions.
    fd=os.open('/trace/uprobe_events',os.O_WRONLY)
    try:os.write(fd,(command+'\n').encode())
    finally:os.close(fd)

BaseCapture=scheduling.Capture


class ProtocolCapture(BaseCapture):
    def __init__(self,vm):
        super().__init__(vm)
        self.group='fcproto_'+uuid.uuid4().hex
        self.registered=[];self.trigger=None
        self.meta['protocol']={'group':self.group,'points':[],'probes_removed':False,'stack_trigger_removed':False,'build_id':BUILD_ID}

    def start(self):
        super().start()
        exe=self.vm.root/'firecracker'
        check(os.stat(exe).st_ino==os.stat(f'/proc/{self.vm.pid}/exe').st_ino,'probe file must be the running jailed executable')
        points=verify_binary(exe)
        self.meta['protocol']['points']=points
        check(digest(exe)==digest(INPUT/'firecracker'),'jailed executable changed')
        for name,kind,offset in POINTS:
            definition=f'{kind}:{self.group}/{name} {exe}:{hex(offset)}'
            probe_command(definition)
            self.registered.append(name)
            event=self.group+'/'+name
            self.write('events/'+event+'/filter','common_pid > 0')
            self.write('events/'+event+'/enable','1')
            self.meta['events'][event]='common_pid > 0'
        self.meta['protocol']['scope']='Uprobes target this scenario executable inode; kernel stacks are sampled through procfs, no ptrace'

    def close(self):
        try:
            if self.path is not None and self.trigger:
                if (self.path/'tracing_on').read_text().strip()=='1':self.write('trace_marker','shutdown-observation-end')
                self.write('tracing_on','0')
                self.write('events/sched/sched_switch/trigger','!stacktrace')
                self.meta['protocol']['stack_trigger_removed']=True
            super().close()
            text=(self.directory/'events.trace').read_text()
            analysis,_=analyze_protocol(text,self.meta['threads'])
            self.meta['protocol']['validated']=analysis['valid']
            self.meta['protocol']['validation_errors']=analysis['errors']
            check(analysis['valid'], 'protocol evidence incomplete: '+repr(analysis['errors']))
        finally:
            errors=[]
            for name in reversed(self.registered):
                try:
                    probe_command(f'-:{self.group}/{name}')
                except OSError as exc:errors.append(str(exc))
            self.meta['protocol']['cleanup_errors']=errors
            self.meta['protocol']['probes_removed']=not errors
            if errors:raise RuntimeError('Owned uprobe cleanup failed: '+repr(errors))


def stack_snapshot(vm, elapsed):
    rows=[]
    for task in sorted(Path(f'/proc/{vm.pid}/task').glob('*')):
        item={'helper_tid':int(task.name),'t':round(elapsed,6),'read_begin_monotonic':time.monotonic()}
        try:
            item['name']=(task/'comm').read_text().strip()
            item['state']=next(s for s in (task/'status').read_text().splitlines() if s.startswith('State:'))
            item['stack']=(task/'stack').read_text()
            item['wchan']=(task/'wchan').read_text().strip()
        except (OSError,StopIteration) as exc:item['error']=repr(exc)
        item['read_end_monotonic']=time.monotonic()
        rows.append(item)
    return rows


def protocol_shutdown(vm,quota):
    if not vm.report.get('traced'):return original_shutdown(vm,quota)
    (vm.cg/'cpu.max').write_text(quota);vm.report['shutdown_quota']=quota
    before=kv(vm.cg/'cpu.stat');start=time.monotonic();snapshots=[]
    snapshots.extend(stack_snapshot(vm,0))
    code,_=request(vm.socket,'PUT','/actions',{'action_type':'SendCtrlAltDel'})
    next_sample=0
    while vm.alive() and time.monotonic()-start<PROFILE['shutdown_timeout']:
        vm.capture(.005)
        elapsed=time.monotonic()-start
        if elapsed>=next_sample:
            snapshots.extend(stack_snapshot(vm,elapsed));next_sample=elapsed+.005
    vm.capture(0)
    vm.report['shutdown_observation']={'http':code,'seconds':round(time.monotonic()-start,6),'cpu_before':before,'cpu_after':kv(vm.cg/'cpu.stat'),'samples':[]}
    path=vm.directory/'kernel-stack-samples.json';path.write_text(json.dumps(snapshots,indent=2)+'\n')
    vm.report['kernel_stack_samples']={'start_monotonic':start,'sha256':digest(path),'records':len(snapshots),'errors':sum('error' in x for x in snapshots),'nonempty':sum(bool(x.get('stack','').strip()) for x in snapshots),'empty':sum('stack' in x and not x['stack'].strip() for x in snapshots),'interval_target_seconds':.005}
    check(not vm.alive(),'shutdown deadline exceeded');check(code==204,'shutdown API did not return 204')
    check(' ro.' in normalize_console(vm.raw),'guest did not remount root read-only')
    check(any(s.get('stack','').strip() for s in snapshots), 'no readable kernel stacks')
    # Protocol event completeness is checked after Capture.close saves the trace.

original_shutdown=scheduling.shutdown
scheduling.Capture=ProtocolCapture
scheduling.shutdown=protocol_shutdown

if __name__=='__main__':raise SystemExit(scheduling.main())
