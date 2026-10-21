#!/usr/bin/env python3
"""Review memory attribution, protocol outcomes and persisted stopped clones."""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path

STAT_KEYS=('anon','file','kernel','kernel_stack','pagetables','slab','file_mapped','file_dirty','file_writeback','shmem','sock','pgscan','pgsteal','pgscan_direct','pgsteal_direct','pgscan_kswapd','workingset_refault_file')

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  while block:=stream.read(4*1024*1024):h.update(block)
 return h.hexdigest()

def compact(row):
 result={key:row[key] for key in ('monotonic','phase')}
 for scope in ('outer','supervisor'):
  if scope in row:
   source=row[scope];result[scope]={k:v for k,v in source.items() if k!='memory.stat'}
   result[scope]['memory.stat']={k:v for k,v in source.get('memory.stat',{}).items() if k in STAT_KEYS}
 return result

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runs',nargs='+',required=True);parser.add_argument('--output',required=True);args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
 subprocess.run([sys.executable,str(Path(__file__).with_name('export_protocol_evidence.py')),'--runs',*args.runs,'--output',str(out/'protocol')],check=True)
 index={'schema':1,'method':'Same capped protocol matrix; retain clone cache versus fsync + DONTNEED on two stopped clone files only','limits':{'outer_memory_bytes':805306368,'outer_swap_bytes':0,'outer_cpus':2,'outer_pids':256,'vm_memory_bytes':402653184,'vm_swap_bytes':0,'vm_pids':32},'limitations':['Cgroup counters/stat fields are read sequentially; samples are not atomic.','memory.peak covers unsampled peaks; 100ms polling and phase markers do not capture every transient.','DONTNEED is advisory, not a guaranteed eviction or a resident-memory cap.','Page-cache charges depend on first touch and shared input history; cohorts are local observations, not a universal benchmark.','fsync/advice and optional cache-integrity checks run after VM exit, outside measured shutdown.'],'runs':[]}
 for arg in args.runs:
  run=Path(arg);report=json.loads((run/'result.json').read_text());rows=sorted(json.loads((run/'memory-observations.json').read_text()),key=lambda x:x['monotonic']);first,last=rows[0]['outer'],rows[-1]['outer'];events={k:last['memory.events.local'][k]-first['memory.events.local'].get(k,0) for k in last['memory.events.local']}
  phase=[compact(r) for r in rows if r['phase']!='periodic']
  selected=[]
  for key in ('memory.current','memory.peak'):
   selected.append(compact(max(rows,key=lambda r:r['outer'].get(key,0))))
  for key in ('file','anon','file_dirty','file_writeback'):
   selected.append(compact(max(rows,key=lambda r:r['outer']['memory.stat'].get(key,0))))
  for i,row in enumerate(rows):
   if i and row['outer']['memory.events.local']['max']>rows[i-1]['outer']['memory.events.local']['max']:
    selected.append(compact(row));break
  changes=[];integrity=[]
  for case in report['scenarios']:
   name=case['scenario'];before=next(r for r in rows if r['phase']==name+':after-close-before-advice');after=next(r for r in rows if r['phase']==name+':after-cache-policy')
   changes.append({'scenario':name,'outer_before':compact(before)['outer'],'outer_after':compact(after)['outer'],'cache_release':case.get('cache_release')})
   for record in case.get('cache_release',{}).get('files',[]):
    path=run/name/'jails/firecracker'/name/'root'/record['file']
    if path.is_symlink():raise ValueError('Unexpected clone symlink')
    actual=sha(path)
    expected=record['sha256_before_advice'];same=actual==expected
    integrity.append({'scenario':name,'file':record['file'],'bytes':path.stat().st_size,'sha256_before_advice':expected,'sha256_after_campaign':actual,'identical':same})
    if not same:raise ValueError('Persisted clone differs: '+str(path))
  psi={kind:last['memory.pressure'][kind]['total']-first['memory.pressure'][kind]['total'] for kind in ('some','full')}
  duration=rows[-1]['monotonic']-rows[0]['monotonic']
  summary={'run':run.name,'policy':report['helper_memory']['policy'],'raw_status':report['status'],'case_count':len(report['scenarios']),'trace_count':sum(c['traced'] for c in report['scenarios']),'observer_seconds':round(duration,6),'outer_memory_peak_bytes':last['memory.peak'],'sampled_current_max_bytes':max(r['outer']['memory.current'] for r in rows),'end_current_bytes':last['memory.current'],'end_file_bytes':last['memory.stat']['file'],'end_anon_bytes':last['memory.stat']['anon'],'event_delta':events,'pressure_total_delta_us':psi,'pressure_wall_fraction':{kind:round(psi[kind]/(duration*1e6),6) for kind in psi},'pgscan_delta':last['memory.stat'].get('pgscan',0)-first['memory.stat'].get('pgscan',0),'pgscan_direct_delta':last['memory.stat'].get('pgscan_direct',0)-first['memory.stat'].get('pgscan_direct',0),'originals_unchanged':report['originals_unchanged'],'raw_observations_sha256':sha(run/'memory-observations.json'),'raw_result_sha256':sha(run/'result.json')}
  data={'summary':summary,'raw_sample_count':len(rows),'phase_samples':phase,'extreme_and_first_pressure_samples':selected,'per_case_cache_effect':changes,'clone_integrity':integrity,'source_sha256':json.loads((run/'launch.json').read_text())['source_sha256']}
  path=out/(run.name+'.json');path.write_text(json.dumps(data,indent=2)+'\n');index['runs'].append({**summary,'file':path.name,'sha256':sha(path)})
 (out/'index.json').write_text(json.dumps(index,indent=2)+'\n')
if __name__=='__main__':main()
