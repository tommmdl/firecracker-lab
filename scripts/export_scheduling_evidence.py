#!/usr/bin/env python3
"""Review scheduler captures; publish target events without addresses/other comms."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import statistics


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def analyze(text, threads):
    ids={t['trace_tid']:t['name'] for t in threads if 'trace_tid' in t}
    parsed=[]; counts=collections.Counter(); ready={}; waits={tid:[] for tid in ids}; exits={}; gens={tid:[] for tid in ids}; deliveries={tid:[] for tid in ids}
    for line in text.splitlines():
        match=re.match(r'^.*?-(\d+)\s+\[(\d+)\]\s+\S+\s+(\d+\.\d+): (\w+): (.*)$',line)
        if not match: continue
        actor,cpu,t,event,detail=match.groups();actor=int(actor);t=float(t);counts[event]+=1
        actor_name=line.split('-'+str(actor),1)[0].strip()
        item={'time':t,'cpu':int(cpu),'event':event,'actor':ids.get(actor,actor_name if actor_name in ('firecracker','fc_api') else 'other')}
        if event=='sched_switch':
            m=re.fullmatch(r'prev_comm=.*? prev_pid=(\d+) prev_prio=\d+ prev_state=(\S+) ==> next_comm=.*? next_pid=(\d+) next_prio=\d+',detail)
            if not m: raise ValueError('unparsed sched_switch')
            prev,state,nxt=m.groups();prev=int(prev);nxt=int(nxt)
            item.update(previous=ids.get(prev,'other'), previous_state=state, next=ids.get(nxt,'other'))
            if prev in ids and prev not in exits:
                if state.startswith('R'): ready.setdefault(prev,t)
                else: ready.pop(prev,None)
            if nxt in ids and nxt not in exits and nxt in ready:
                start=ready.pop(nxt);waits[nxt].append({'start':start,'end':t,'microseconds':round((t-start)*1e6,3)})
        elif event in ('sched_wakeup','sched_waking','sched_process_exit','signal_generate'):
            target=int(re.search(r'\bpid=(\d+)',detail).group(1)); item['target']=ids.get(target,'other')
            if event=='sched_wakeup' and target not in exits: ready.setdefault(target,t)
            if event=='sched_process_exit': exits[target]=t;ready.pop(target,None)
            if event=='signal_generate':
                item.update({key:int(re.search(r'\b'+key+r'=(-?\d+)',detail).group(1)) for key in ('sig','code','res')})
                if target in ids and item['sig']==35: gens[target].append(t)
        elif event=='signal_deliver':
            item['sig']=int(re.search(r'\bsig=(\d+)',detail).group(1))
            if actor in ids and item['sig']==35: deliveries[actor].append(t)
        elif event in ('kvm_entry','kvm_exit'):
            item['vcpu']=int(re.search(r'vcpu (\d+)',detail).group(1))
            if event=='kvm_exit': item['reason']=re.search(r'reason (\S+)',detail).group(1)
        elif event=='tracing_mark_write': item['marker']=detail
        else: raise ValueError('unexpected event '+event)
        parsed.append(item)
    results=[]
    for tid,name in ids.items():
        intervals=waits[tid];last=gens[tid][-1] if gens[tid] else None
        delivery=next((t for t in deliveries[tid] if last is not None and t>=last),None)
        results.append({'name':name,'trace_tid':tid,'runnable_intervals':len(intervals),'longest_runnable_wait_us':max((i['microseconds'] for i in intervals),default=None),'longest_intervals':sorted(intervals,key=lambda x:x['microseconds'],reverse=True)[:5], 'signal35_generate':gens[tid],'signal35_deliver':deliveries[tid],'exit_time':exits.get(tid),'last_generate_to_next_delivery_us':round((delivery-last)*1e6,3) if delivery is not None else None,'last_generate_to_exit_us':round((exits[tid]-last)*1e6,3) if last is not None and tid in exits else None})
    valid=len(ids)==2 and all(gens[t] and deliveries[t] and t in exits for t in ids) and counts['sched_switch']>0
    return {'capture_valid':valid,'event_counts':dict(counts),'vcpus':results,'events':parsed}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',nargs='+',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args();output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    index={'schema':1,'method':'Per-instance tracefs, numeric vCPU filters after unique-name discovery; no ptrace or injected pause','limitations':['Local bounded sample, not a benchmark or proof of historical root cause','Runnable wait includes CPU contention and quota throttling; sampled cgroup counters cannot uniquely split their contribution','Signal generation and delivery are separate observations, not guaranteed one-to-one pairing','Initial/final censored runnable intervals are not counted; trace clock precision is one microsecond in text','Only vCPU events are captured; there is no full main-thread userspace protocol or kernel stack trace'],'runs':[]}
    for rawdir in args.runs:
        run=Path(rawdir)
        if not (run/'result.json').exists():
            review={'run':run.name,'reviewed_status':'failed','reason':'Interrupted; no complete suite report. No latency conclusion.', 'launch_sha256':sha(run/'launch.json'), 'source_sha256':json.loads((run/'launch.json').read_text())['source_sha256'], 'exit_code':137, 'console_sha256':{str(p.relative_to(run)):sha(p) for p in run.glob('*/console.log')}, 'operator_stop':json.loads((run/'operator-stop.json').read_text()), 'partial_cases':[{ 'file':str(p.relative_to(run)), 'sha256':sha(p)} for p in run.glob('*/result.json')]}
            dest=output/(run.name+'.json');dest.write_text(json.dumps(review,indent=2)+'\n')
            index['runs'].append({'run':run.name,'file':dest.name,'sha256':sha(dest),'reviewed_status':'failed'})
            continue
        report=json.loads((run/'result.json').read_text());launch=json.loads((run/'launch.json').read_text())
        review={'run':run.name,'raw_result_sha256':sha(run/'result.json'),'raw_status':report['status'],'host_kernel':report['host_kernel'],'started_at':report['started_at'],'source_sha256':launch['source_sha256'],'profile':json.loads((run/'profile.json').read_text()),'affinity':report['affinity'],'originals_unchanged':report['originals_unchanged'],'outer_memory_events_local':report['outer_memory_events_local'],'cgroups_cleaned':report['cgroups_cleaned'],'findings':report['findings'],'scenarios':[]}
        for name in ('before','after'):
            inspected=json.loads((run/f'host-load-inspect-{name}.json').read_text())[0]
            review['load_'+name]={'running':inspected['State']['Running'],'oom_killed':inspected['State']['OOMKilled'],'user':inspected['Config']['User'],'cpuset':inspected['HostConfig']['CpusetCpus'],'memory':inspected['HostConfig']['Memory'],'pids_limit':inspected['HostConfig']['PidsLimit']}
        for case in report['scenarios']:
            obs=case.get('shutdown_observation',{});row={key:case.get(key) for key in ('scenario','status','failure','capture_failure','traced','guest_load','worker_stop_ack','exit_code','panic','cleanup_forced_stop','keyboard_readiness','runtime','checks','console_sha256','console_findings','outside_canary_unchanged','final_cgroup_events','shutdown_quota','pids_probe')}
            row['shutdown']={k:obs.get(k) for k in ('http','seconds','cpu_before','cpu_after')}
            cap=case.get('scheduler_capture')
            if cap:
                row['capture_metadata']=cap
                row['tracepoint_format_sha256']={p.name:sha(p) for p in (run/case['scenario']/'scheduler').glob('*.format')}
                trace=run/case['scenario']/'scheduler/events.trace'
                if trace.exists():
                    analyzed=analyze(trace.read_text(),cap.get('threads',[]))
                    loss={cpu:{k.strip():int(v.strip()) for line in stats.splitlines() if ':' in line for k,v in [line.split(':',1)] if k.strip() in ('overrun','commit overrun','dropped events')} for cpu,stats in cap.get('buffer_stats',{}).items()}
                    analyzed['buffer_loss']=loss
                    analyzed['capture_valid']=analyzed['capture_valid'] and bool(loss) and all(v==0 for stats in loss.values() for v in stats.values())
                    eventpath=output/(run.name+'-'+case['scenario']+'.json')
                    payload={'raw_trace_sha256':sha(trace),'review':'Host addresses and unrelated task identities omitted; target event chronology preserved',**analyzed}
                    events=payload.pop('events')
                    eventpath.write_text(json.dumps(payload,indent=2)[:-2]+',\n  \"events\": [\n    '+',\n    '.join(json.dumps(e,separators=(',',':')) for e in events)+'\n  ]\n}\n')
                    row['event_review_sha256']=sha(eventpath)
                    row['analysis']={k:v for k,v in analyzed.items() if k!='events'}
                    row['event_file']=eventpath.name
            review['scenarios'].append(row)
        valid=all(not row.get('traced') or row.get('analysis',{}).get('capture_valid',False) for row in review['scenarios'])
        review['reviewed_status']='passed' if report['status']=='passed' and valid else 'failed'
        if not valid: review['review_note']='Raw suite reported success but scheduler capture was empty/invalid; excluded from latency conclusions.'
        if (run/'operator-note.json').exists():
            review['operator_note']=json.loads((run/'operator-note.json').read_text())
            review['reviewed_status']='excluded-load-overlap'
        dest=output/(run.name+'.json');dest.write_text(json.dumps(review,indent=2)+'\n')
        groups={}
        for row in review['scenarios']:
            key=row['scenario'].rsplit('-',1)[0];groups.setdefault(key,[]).append(row['shutdown']['seconds'])
        index['runs'].append({'run':run.name,'file':dest.name,'sha256':sha(dest),'reviewed_status':review['reviewed_status'],'shutdown_seconds':{key:{'count':len(values),'min':min(values),'median':statistics.median(values),'max':max(values)} for key,values in groups.items()}})
    (output/'index.json').write_text(json.dumps(index,indent=2)+'\n')

if __name__=='__main__': main()
