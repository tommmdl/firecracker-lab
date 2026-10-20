#!/usr/bin/env python3
"""Export reviewed fields; never copy disks, cores or complete process memory."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from boot_check import normalize_console


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize_case(directory,row):
    result=directory/row['scenario']/'result.json'
    console=directory/row['scenario']/'console.log'
    if sha(console)!=row['console_sha256']:raise ValueError('console digest mismatch')
    keys=['scenario','status','failure','checks','cleanup_forced_stop','exit_code','console_sha256','panic','expected_result_matched','guest','keyboard_readiness','guest_load','worker_stop_ack','pids_probe','shutdown_quota','shutdown_observation','injected_vcpu_tid','outside_canary_unchanged','final_cgroup_events','console_findings']
    out={k:row[k] for k in keys if k in row}
    if 'shutdown_observation' in out:
        observation=dict(out['shutdown_observation'])
        samples=observation.pop('samples',[])
        if samples:
            indices=sorted({0,len(samples)-1,*range(0,len(samples),max(1,len(samples)//12))})
            observation['sample_count']=len(samples);observation['selected_samples']=[samples[i] for i in indices]
        out['shutdown_observation']=observation
    out['raw_result_sha256']=sha(result)
    runtime=row.get('runtime',{})
    out['isolation']={k:runtime[k] for k in ['hardened_runtime','threads','resource_files','network_devices','namespaces'] if k in runtime}
    if 'trace' in row:
        trace=row['trace'];events=trace['events']
        selected=[]
        for event in events:
            if event.get('signal') not in (5,None) or 'action' in event or 'held_vcpu' in event or 'injected_quota' in event or any(word in event.get('write','') for word in ['Killing vCPU','panicked','Timed out','exiting successfully']):
                selected.append({k:event[k] for k in ['t','signal','syscall','rip','instruction_hex','siginfo_hex','write','action','held_vcpu','hold_seconds','injected_quota','tid','errno','wait_status'] if k in event})
        # Keep all signal events except repeated stops at the same faulting instruction.
        compact=[];previous=None
        for event in selected:
            signature=(event.get('signal'),event.get('rip'),event.get('instruction_hex'))
            if event.get('signal')==11 and signature==previous:continue
            compact.append(event);previous=signature
        out['trace']={'injection_requested':trace['injection_requested'],'http':trace['http'],'event_count':len(events),'selected_events':compact}
        samples=trace.get('samples',[])
        if samples:
            # Evenly spaced raw samples, retaining thread state and scheduler counters.
            indices=sorted({0,len(samples)-1,*range(0,len(samples),max(1,len(samples)//12))})
            out['trace']['selected_samples']=[samples[i] for i in indices]
            out['trace']['sample_count']=len(samples)
    text=normalize_console(console.read_bytes())
    out['shutdown_excerpt']=text[text.rfind('reboot: Restarting system'):][-2400:] if 'reboot: Restarting system' in text else text[-1200:]
    return out


def summarize_run(directory):
    report=json.loads((directory/'result.json').read_text())
    launch=json.loads((directory/'launch.json').read_text())
    profile=json.loads((directory/'profile.json').read_text())
    out={k:report[k] for k in ['schema','host_kernel','started_at','status','failure','findings','matrix','diagnostic_join_timeout_seconds','affinity','host_cpu_pressure_before','host_cpu_pressure_after','original_sha256_before','original_sha256_after','originals_unchanged','outer_memory_events_local','cgroups_cleaned'] if k in report}
    out.update({'local_run':directory.name,'raw_report_sha256':sha(directory/'result.json'),'source_sha256':launch['source_sha256'],'profile':profile,'scenarios':[summarize_case(directory,row) for row in report['scenarios']]})
    if (directory/'binary-preflight.json').exists():out['binary_preflight']=json.loads((directory/'binary-preflight.json').read_text())
    if (directory/'host-load.json').exists():
        load=json.loads((directory/'host-load.json').read_text())
        before=json.loads((directory/'host-load-inspect-before.json').read_text())[0]
        after=json.loads((directory/'host-load-inspect-after.json').read_text())[0]
        logs=(directory/'host-load.log').read_text()
        out['host_load']={'same_host_cpus':load['same_host_cpus'],'workers':load['workers'],'maximum_seconds':load['maximum_seconds'],'startup_log':logs,'running_before':before['State']['Running'],'running_after':after['State']['Running'],'oom_killed':after['State']['OOMKilled'],'user':after['Config']['User'],'image_id':after['Image'],'mounts':after['Mounts'],'host_config':{k:after['HostConfig'][k] for k in ['Privileged','CapDrop','Memory','MemorySwap','PidsLimit','NanoCpus','CpusetCpus','SecurityOpt','NetworkMode']},'stats':(directory/'host-load-stats.json').read_text().strip()}
        if not out['host_load']['running_before'] or not out['host_load']['running_after'] or out['host_load']['oom_killed']:raise ValueError('host load was not alive throughout the matrix')
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',nargs='+',required=True,type=Path)
    parser.add_argument('--builds',nargs='*',default=[],type=Path)
    parser.add_argument('--checks',nargs='*',default=[],type=Path)
    parser.add_argument('--release-dir',type=Path,help='Reviewed official binary and matching debug symbols')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    evidence={'schema':1,'scope':'Local WSL2/KVM investigation, not CI or production certification','historical_case':{'run':'jailer-eajvuzjb','exit_code':-11,'console_sha256':'5c37a9358663dc88eeaf2b4c4a52602761f5d01a641ebbb40102148cdd0fd3dc','cause_of_original_vcpu_delay':'unresolved; no historical per-thread capture'},'runs':[summarize_run(p) for p in args.runs],'builds':[]}
    for path in args.builds:
        data=json.loads((path/'build-results.json').read_text())
        row={'local_build':path.name,'report_sha256':sha(path/'build-results.json'),'result':data}
        if (path/'static-validation.json').exists():row['artifact_validation']=json.loads((path/'static-validation.json').read_text())
        if (path/'build-script.py').exists():row['builder_script_sha256']=sha(path/'build-script.py')
        row['logs']={str(p.relative_to(path)):{'sha256':sha(p),'first_lines':p.read_text(errors='replace').splitlines()[:18],'last_lines':p.read_text(errors='replace').splitlines()[-18:]} for p in path.glob('*/build.log')}
        validation=row.get('artifact_validation',{})
        rejected=validation.get('status')=='failed' or any(isinstance(v,dict) and (v.get('has_pt_interp') or v.get('has_dt_needed')) for v in validation.values())
        row['acceptance']='failed' if rejected or data['status']!='passed' else 'passed'
        evidence['builds'].append(row)
    evidence['additional_checks']=[{'file':p.name,'sha256':sha(p),'result':json.loads(p.read_text())} for p in args.checks]
    if args.release_dir:
        binary=args.release_dir/'firecracker'
        debug=args.release_dir/'release-v1.17.0-x86_64/firecracker-v1.17.0-x86_64.debug'
        if sha(binary)!='99ad0f5cd0514a88aad0e9ae8cfdb3cc3b4ab9d190e1194602406c786b5de7a5':raise ValueError('unexpected official release binary')
        ids=[re.search(r'Build ID: (\w+)',subprocess.check_output(['readelf','-n',str(path)],text=True)).group(1) for path in [binary,debug]]
        if ids[0]!=ids[1]:raise ValueError('debug build ID mismatch')
        evidence['official_release_symbolization']={'binary_sha256':sha(binary),'debug_sha256':sha(debug),'matching_build_id':ids[0],'fault_relative_pc':'0x26cae6','addr2line':subprocess.check_output(['addr2line','-f','-e',str(debug),'0x26cae6'],text=True).strip(),'disassembly':subprocess.check_output(['objdump','-d','--start-address=0x26cae6','--stop-address=0x26cafb',str(binary)],text=True).replace(str(binary),'official-firecracker').strip()}
    details=args.output.parent/'shutdown-runs';details.mkdir(exist_ok=True)
    index=[]
    for run in evidence['runs']:
        target=details/(run['local_run']+'.json')
        target.write_text(json.dumps(run,indent=2,ensure_ascii=False)+'\n')
        row={k:run[k] for k in ['local_run','status','matrix','failure','findings','diagnostic_join_timeout_seconds','originals_unchanged','cgroups_cleaned'] if k in run}
        row['reviewed_details']='shutdown-runs/'+target.name
        row['details_sha256']=sha(target)
        row['scenarios']=[{k:case[k] for k in ['scenario','status','exit_code','panic','failure','expected_result_matched','cleanup_forced_stop'] if k in case} for case in run['scenarios']]
        index.append(row)
    evidence['runs']=index
    evidence['layout']='Reviewed per-run details are linked from runs[].reviewed_details; raw logs and disks remain local.'
    args.output.write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'output':str(args.output),'runs':len(evidence['runs']),'builds':len(evidence['builds'])}))

if __name__=='__main__':main()
