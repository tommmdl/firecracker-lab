#!/usr/bin/env python3
"""Publish protocol/stack observations with raw digests and explicit failures."""
import argparse,hashlib,json,statistics
from pathlib import Path
from export_scheduling_evidence import analyze
from protocol_analysis import analyze_protocol,symbolize_stack

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',nargs='+',required=True);p.add_argument('--output',required=True);a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
 index={'schema':1,'method':'Official ELF uprobes plus live procfs kernel stacks; no ptrace, no source patch, no injected vCPU stall','limitations':['Uprobes trap and sampling consumes CPU: durations include observer effects.','Kernel stack/status/wchan reads are sequential, not one atomic task snapshot.','Empty stacks and task-exit read errors are retained, not interpreted as no kernel activity.','Source and disassembly identify protocol stages; probes do not decode Rust channel payloads.','Capturing clean shutdowns cannot establish the historical panic cause.'],'runs':[]}
 for arg in a.runs:
  run=Path(arg);complete=(run/'result.json').exists();raw=json.loads((run/'result.json').read_text()) if complete else {}
  cases=raw.get('scenarios',[json.loads(f.read_text()) for f in sorted(run.glob('*/result.json'))])
  report={'run':run.name,'complete':complete,'raw_status':raw.get('status','interrupted'),'raw_result_sha256':sha(run/'result.json') if complete else None,'profile':json.loads((run/'profile.json').read_text()),'source_sha256':json.loads((run/'launch.json').read_text())['source_sha256'],'started_at':raw.get('started_at'),'host_kernel':raw.get('host_kernel'),'originals_unchanged':raw.get('originals_unchanged'),'cgroups_cleaned':raw.get('cgroups_cleaned'),'outer_memory_events_local':raw.get('outer_memory_events_local'),'findings':raw.get('findings',[]),'scenarios':[]}
  if (run/'operator-stop.json').exists():report['operator_stop']=json.loads((run/'operator-stop.json').read_text());report['wrapper_exit_code']=137
  if not complete:report['partial_console_sha256']={str(f.relative_to(run)):sha(f) for f in run.glob('*/console.log')}
  for when in ('before','after'):
   f=run/('host-load-inspect-'+when+'.json')
   if f.exists():
    data=json.loads(f.read_text())[0];report['load_'+when]={'running':data['State']['Running'],'oom_killed':data['State']['OOMKilled'],'user':data['Config']['User'],'affinity':data['HostConfig']['CpusetCpus'],'memory':data['HostConfig']['Memory'],'pids_limit':data['HostConfig']['PidsLimit']}
  valid=True
  for case in cases:
   row={k:case.get(k) for k in ('scenario','status','failure','capture_failure','exit_code','panic','cleanup_forced_stop','traced','pids_probe','keyboard_readiness','runtime','checks','console_sha256','console_findings','outside_canary_unchanged','final_cgroup_events','shutdown_quota','kernel_stack_samples')}
   row['shutdown']={k:v for k,v in case.get('shutdown_observation',{}).items() if k!='samples'}
   cap=case.get('scheduler_capture');detail={}
   if cap:
    row['capture_metadata']=cap;row['stack_trigger_installed']='stack_trigger_readback' in cap.get('protocol',{})
    trace=run/case['scenario']/'scheduler/events.trace'
    if trace.exists():
     protocol,base=analyze_protocol(trace.read_text(),cap['threads']);sched=analyze(base,cap['threads'])
     detail['protocol']=protocol;detail['scheduler']={k:v for k,v in sched.items() if k!='events'};detail['raw_trace_sha256']=sha(trace)
     row['protocol_valid']=protocol['valid'];row['scheduler_valid']=sched['capture_valid']
    else:row['protocol_valid']=False;row['scheduler_valid']=False
    stackfile=run/case['scenario']/'kernel-stack-samples.json'
    if stackfile.exists():
     samples=json.loads(stackfile.read_text());stacks={};reviewed=[];classified={'response_wait':0,'join':0,'outside_probed_wait':0};nonempty=0
     intervals=detail.get('protocol',{}).get('intervals',{})
     for sample in samples:
      record={k:v for k,v in sample.items() if k not in ('stack','helper_tid')}
      if 'stack' in sample:
       frames=symbolize_stack(sample['stack']);key=hashlib.sha256('\n'.join(frames).encode()).hexdigest()[:16];stacks[key]=frames;record['stack_id']=key;nonempty+=bool(frames)
       phase='outside_probed_wait';begin=sample.get('read_begin_monotonic');end=sample.get('read_end_monotonic')
       if sample.get('name')=='firecracker' and begin is not None and end is not None:
        for label,target in [('wait','response_wait'),('join','join')]:
         if any(i['start']<=begin<=end<=i['end'] for i in intervals.get(label,[])):phase=target
       record['phase']=phase
       if frames:classified[phase]+=1
      reviewed.append(record)
     detail['stacks']={'raw_sha256':sha(stackfile),'unique_stacks':stacks,'samples':reviewed,'nonempty':nonempty,'nonempty_by_phase':classified,'errors':sum('error' in s for s in samples),'empty':sum('stack' in s and not s['stack'].strip() for s in samples)}
     row['stacks_nonempty']=nonempty;row['stack_phases']=classified
    else:row['stacks_nonempty']=0
    valid=valid and row['protocol_valid'] and row['scheduler_valid'] and row['stacks_nonempty']>0
    target=out/(run.name+'-'+case['scenario']+'.json');target.write_text(json.dumps(detail,indent=2)+'\n');row['detail_file']=target.name;row['detail_sha256']=sha(target)
   report['scenarios'].append(row)
  report['reviewed_status']='passed' if complete and raw['status']=='passed' and valid else 'failed'
  target=out/(run.name+'.json');target.write_text(json.dumps(report,indent=2)+'\n')
  summary={'run':run.name,'file':target.name,'sha256':sha(target),'complete':complete,'reviewed_status':report['reviewed_status'],'scenarios':len(cases),'primary':bool(complete and report['profile'].get('diagnostic_repetitions')==5 and report['reviewed_status']=='passed')}
  groups={}
  for case in report['scenarios']:
   if 'seconds' in case['shutdown']:groups.setdefault(case['scenario'].rsplit('-',1)[0],[]).append(case['shutdown']['seconds'])
  summary['shutdown_seconds']={key:{'count':len(v),'min':min(v),'median':statistics.median(v),'max':max(v)} for key,v in groups.items()}
  index['runs'].append(summary)
 (out/'index.json').write_text(json.dumps(index,indent=2)+'\n')
if __name__=='__main__':main()
