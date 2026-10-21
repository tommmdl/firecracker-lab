"""Pure PSI deltas and symbol-only review of owned kernel stall traces."""
import re


def pressure_deltas(rows):
    rows=sorted(rows,key=lambda r:r['monotonic'])
    intervals=[];phase='campaign-start'
    for before,after in zip(rows,rows[1:]):
        if before['phase']!='periodic':phase=before['phase']
        a=before.get('outer',{}).get('memory.pressure',{})
        b=after.get('outer',{}).get('memory.pressure',{})
        if not a or not b:continue
        delta={kind:b[kind]['total']-a[kind]['total'] for kind in ('some','full')}
        if min(delta.values())<0:raise ValueError('PSI counter decreased')
        if not any(delta.values()):continue
        row=dict(begin=before['monotonic'],end=after['monotonic'],phase=phase,previous_sample_phase=before['phase'],next_sample_phase=after['phase'],memory_delta_us=delta,scopes={})
        for scope in ('outer','supervisor','vms'):
            left=before.get(scope);right=after.get(scope)
            if not left or not right:continue
            data={'pressure_delta_us':{},'memory_stat_delta':{},'events_local_delta':{}}
            for resource in ('memory','io','cpu'):
                key=resource+'.pressure'
                if key in left and key in right:
                    data['pressure_delta_us'][resource]={kind:right[key][kind]['total']-left[key][kind]['total'] for kind in left[key] if kind in right[key]}
            for key in ('pgfault','pgmajfault','pgscan','pgscan_direct','pgsteal','workingset_refault_file','workingset_activate_file'):
                if key in left.get('memory.stat',{}) and key in right.get('memory.stat',{}):data['memory_stat_delta'][key]=right['memory.stat'][key]-left['memory.stat'][key]
            data['events_local_delta']={key:value-left.get('memory.events.local',{}).get(key,0) for key,value in right.get('memory.events.local',{}).items()}
            row['scopes'][scope]=data
        intervals.append(row)
    return intervals


def parse_trace(text):
    """Do not expose runtime addresses or treat probe calls as PSI duration."""
    events=[];current=None
    for line in text.splitlines():
        match=re.search(r'-(\d+)\s+(?:\(\s*\d+\)\s+)?\[\d+\].*?\s(\d+\.\d+):\s+(enter|leave):',line)
        if match:
            current=dict(tid=int(match[1]),monotonic=float(match[2]),event=match[3],stack=[])
            events.append(current)
        elif current and '=>' in line:
            frame=line.split('=>',1)[1].strip()
            frame=re.sub(r'\[<[0-9a-fA-F]+>\]\s*','',frame)
            # Stack entries with an unresolved raw address remain explicitly unknown.
            frame=re.sub(r'0x[0-9a-fA-F]{8,}','<address>',frame)
            if re.fullmatch(r'[0-9a-fA-F]{8,}',frame):frame='<unresolved>'
            current['stack'].append(frame)
    return events


def correlate(events, intervals):
    # PSI counters are read sequentially and asynchronous accounting can lag.
    # Membership means timestamp overlap, never proof that this call caused a delta.
    return [{**interval,'overlapping_probe_events':[event for event in events if interval['begin']<=event['monotonic']<=interval['end']]} for interval in intervals]
