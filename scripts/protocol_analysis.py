"""Pure review of protocol events; never infer success from missing events."""
import re
from protocol_points import POINTS

EVENT=re.compile(r'^.*?-(\d+)\s+\[(\d+)\]\s+\S+\s+(\d+\.\d+): (\w+): (.*)$')

def analyze_protocol(text,threads):
    names={t['trace_tid']:t['name'] for t in threads}
    points={name for name,_,_ in POINTS};events=[];base=[];counts={name:0 for name in points};kernel_stack_events=0
    for line in text.splitlines():
        m=EVENT.match(line)
        if not m:
            continue
        tid,cpu,t,event,detail=m.groups();tid=int(tid);t=float(t)
        if event in points:
            counts[event]+=1
            events.append({'time':t,'cpu':int(cpu),'event':event,'thread':names.get(tid,'firecracker')})
        elif event in ('kernel_stack','stack'):
            kernel_stack_events+=1
        else:
            base.append(line)
    intervals={}; errors=[]
    for label in ('send','wait','join','drop'):
        pending=[];paired=[]
        for index,event in enumerate(events):
            if event['event']==label+'_enter':pending.append((index,event))
            if event['event']==label+'_return':
                if not pending:errors.append('unmatched '+label+' return');continue
                entry_index,start=pending.pop()
                paired.append({'entry_index':entry_index,'return_index':index,'start':start['time'],'end':event['time'],'microseconds':round((event['time']-start['time'])*1e6,3)})
        if pending:errors.append('unmatched '+label+' entry')
        intervals[label]=paired
    for point in points:
        if counts[point]!=2:errors.append(point+': expected 2, observed '+str(counts[point]))
    for name in ('fc_vcpu 0','fc_vcpu 1'):
        if len([e for e in events if e['event']=='run_return' and e['thread']==name])!=1:errors.append('missing unique run return '+name)
    # Each disconnected branch must lead to join completion in the same drop.
    for drop in intervals['drop']:
        contained=[e['event'] for e in events[drop['entry_index']:drop['return_index']+1] if e['thread']=='firecracker']
        required=['drop_enter','wait_enter','wait_return','disconnected','join_enter','join_return','drop_return']
        if contained!=required:errors.append('unexpected handle teardown order: '+repr(contained))
    return {'valid':not errors,'errors':errors,'counts':counts,'events':events,'intervals':intervals,'trigger_stack_events':kernel_stack_events},'\n'.join(base)


def symbolize_stack(raw):
    """Retain symbol names and offsets, omit absolute instruction addresses."""
    return [re.sub(r'^\[<[^>]+>\]\s*','',line).strip() for line in raw.splitlines() if line.strip()]
