#!/usr/bin/env python3
"""Retain every PSI increment and owned probe stack, including invalid captures."""
import argparse
import collections
import json
import re
from pathlib import Path
from export_helper_memory import sha
from pressure_analysis import pressure_deltas,parse_trace,correlate


def export(run, output, historical=False):
    rows=sorted(json.loads((run/'memory-observations.json').read_text()),key=lambda r:r['monotonic'])
    intervals=pressure_deltas(rows)
    first,last=rows[0]['outer'],rows[-1]['outer']
    raw=json.loads((run/'result.json').read_text())
    profile=json.loads((run/'profile.json').read_text())
    meta_path=run/'memory-stalls-meta.json';trace_path=run/'memory-stalls.trace'
    meta=json.loads(meta_path.read_text()) if meta_path.exists() else None
    events=parse_trace(trace_path.read_text()) if trace_path.exists() else []
    validity=None;capture_errors=[]
    if profile.get('memory_stall_trace',False) and not historical:
        if not meta or meta.get('status')!='collected' or not meta.get('cleanup'):capture_errors.append('Missing complete capture/cleanup metadata')
        if meta:
            for cpu,text in meta['buffer_stats'].items():
                for key in ('overrun','commit overrun','dropped events'):
                    match=re.search(r'^'+key+r':\s*(\d+)',text,re.M)
                    if not match or int(match[1]):capture_errors.append(cpu+': '+key+' missing/nonzero')
        if trace_path.exists():
            if meta and sha(trace_path)!=meta.get('raw_sha256'):capture_errors.append('Raw trace digest mismatch')
            expected=len(re.findall(r':\s+(?:enter|leave):',trace_path.read_text()))
            if expected!=len(events):capture_errors.append('Unparsed probe events')
        if any(not event['stack'] for event in events):capture_errors.append('Missing stack for probe event')
        validity=not capture_errors
    stack_table={};compact_events=[]
    for event in events:
        key=str(len(stack_table))
        frames=event['stack']
        existing=next((k for k,v in stack_table.items() if v==frames),None)
        if existing is None:stack_table[key]=frames
        else:key=existing
        compact_events.append({k:v for k,v in event.items() if k!='stack'}|{'stack_id':key,'helper_main':bool(meta and event['tid']==meta['helper_trace_pid'])})
    correlated=correlate(compact_events,intervals)
    phases=collections.Counter()
    for interval in intervals:phases[interval['phase']]+=interval['memory_delta_us']['some']
    stat_keys=('pgscan','pgscan_direct','pgscan_kswapd','pgsteal','pgmajfault','workingset_refault_file','workingset_activate_file')
    outer_delta={key:last['memory.stat'].get(key,0)-first['memory.stat'].get(key,0) for key in stat_keys}
    host_delta={key:rows[-1]['host']['vmstat'][key]-rows[0]['host']['vmstat'].get(key,0) for key in rows[-1].get('host',{}).get('vmstat',{})} if rows[0].get('host') else None
    window_end=rows[-1].get('read_end_monotonic',rows[-1]['monotonic'])
    window_events=[e for e in events if rows[0]['monotonic']<=e['monotonic']<=window_end]
    slow_inside=sum(e['event']=='enter' and any('alloc_pages_slowpath' in f for f in e['stack']) for e in window_events)
    read_errors=collections.Counter((scope+':'+key+':'+error) for row in rows for scope in ('outer','supervisor','vms') for key,error in row.get(scope,{}).get('read_errors',{}).items())
    summary=dict(probe_events_in_memory_window=len(window_events),enter_allocation_slowpath_in_memory_window=slow_inside,diagnostic_read_errors=dict(read_errors),run=run.name,historical=historical,raw_status=raw['status'],suite_failure=raw.get('failure'),planned_cases=4*profile.get('diagnostic_repetitions',5),case_count=len(raw['scenarios']),clean_shutdowns=sum(c.get('exit_code')==0 and not c.get('panic') and not c.get('failure') and not c.get('cleanup_forced_stop') for c in raw['scenarios']),memory_stall_trace=profile.get('memory_stall_trace'),capture_valid=validity,capture_errors=capture_errors,memory_peak_bytes=last['memory.peak'],memory_delta_us={kind:last['memory.pressure'][kind]['total']-first['memory.pressure'][kind]['total'] for kind in ('some','full')},io_delta_us={kind:last['io.pressure'][kind]['total']-first['io.pressure'][kind]['total'] for kind in ('some','full')} if 'io.pressure' in first else None,local_events_delta={k:v-first['memory.events.local'].get(k,0) for k,v in last['memory.events.local'].items()},outer_memory_stat_delta=outer_delta,host_vmstat_delta=host_delta,phase_memory_some_us=dict(phases),probe_events=len(events),enter_events=sum(e['event']=='enter' for e in events),enter_allocation_slowpath=sum(e['event']=='enter' and any('alloc_pages_slowpath' in f for f in e['stack']) for e in events),enter_readahead=sum(e['event']=='enter' and any('page_cache_ra_' in f for f in e['stack']) for e in events),originals_unchanged=raw['originals_unchanged'],cgroups_cleaned=raw['cgroups_cleaned'])
    sample_indices={0,len(rows)-1}
    sample_indices.update(i for i,row in enumerate(rows) if row['phase']!='periodic')
    for interval in intervals:
        sample_indices.update(i for i,row in enumerate(rows) if row['monotonic'] in (interval['begin'],interval['end']))
    reported={c['scenario'] for c in raw['scenarios']}
    partial=[{'scenario':p.parent.name,'console_sha256':sha(p),'console':p.read_text(errors='replace'),'pidfile_after_campaign':(p.parent/'jails/firecracker'/p.parent.name/'root/firecracker.pid').read_text() if (p.parent/'jails/firecracker'/p.parent.name/'root/firecracker.pid').exists() else None} for p in sorted(run.glob('*/console.log')) if p.parent.name not in reported]
    review=dict(summary=summary,unreported_startup_attempts=partial,profile=profile,raw_observations_sha256=sha(run/'memory-observations.json'),raw_result_sha256=sha(run/'result.json'),raw_trace_sha256=sha(trace_path) if trace_path.exists() else None,capture_metadata=meta,unique_stacks=stack_table,probe_events=compact_events,psi_intervals=correlated,phase_and_pressure_samples=[rows[i] for i in sorted(sample_indices)],source_sha256=json.loads((run/'launch.json').read_text())['source_sha256'],limitations=['Probe calls are not PSI duration; nested calls and observer effects remain possible.','Interval membership is temporal correlation, not causal attribution.','Counters are sequential reads and accounting can lag; some/full interval increments need not align.','Host counters include unrelated workloads. Historical samples lack I/O and host counters.','Trace scope begins after Python imports and ends after observer serialization; memory observation has a narrower window.'])
    path=output/('attribution-'+run.name+'.json');path.write_text(json.dumps(review,indent=2)+'\n')
    return summary|dict(file=path.name,sha256=sha(path))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',nargs='+',required=True);p.add_argument('--historical',nargs='*',default=[]);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    reports=[export(Path(run),a.output,historical=True) for run in a.historical]+[export(Path(run),a.output) for run in a.runs]
    result=dict(schema=1,method='Historical review plus pilot and sequential on/off/on campaigns; corrected clone cache, unchanged jailer ceilings',runs=reports)
    (a.output/'attribution-index.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(reports,indent=2))

if __name__=='__main__':main()
