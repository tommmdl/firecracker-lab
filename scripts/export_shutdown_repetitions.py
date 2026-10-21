#!/usr/bin/env python3
"""Aggregate every case from repeated memory-corrected shutdown campaigns."""
import argparse
import json
import statistics
from pathlib import Path
from export_helper_memory import sha


def summarize(folder):
    index = json.loads((folder / 'index.json').read_text())
    groups, cases, waits, joins, peaks = {}, [], [], [], []
    clone_count = 0
    stack_counts = dict(empty=0, errors=0, nonempty=0)
    for entry in index['runs']:
        run = json.loads((folder / entry['file']).read_text())
        protocol = json.loads((folder / 'protocol' / entry['file']).read_text())
        peaks.append(entry['outer_memory_peak_bytes'])
        clone_count += len(run['clone_integrity'])
        for case in protocol['scenarios']:
            row = {key: case.get(key) for key in ('scenario', 'status', 'failure', 'capture_failure', 'exit_code', 'panic', 'cleanup_forced_stop', 'traced', 'protocol_valid', 'scheduler_valid')}
            row['run'] = entry['run']
            row['shutdown_seconds'] = case['shutdown'].get('seconds')
            row['passed'] = (case['exit_code'] == 0 and not row['failure'] and not row['capture_failure'] and not row['panic'] and not row['cleanup_forced_stop'] and (not case['traced'] or (case.get('protocol_valid') and case.get('scheduler_valid') and case.get('stacks_nonempty', 0) > 0)))
            if row['shutdown_seconds'] is not None:
                groups.setdefault(case['scenario'].rsplit('-', 1)[0], []).append(row['shutdown_seconds'])
            if case.get('detail_file'):
                detail = json.loads((folder / 'protocol' / case['detail_file']).read_text())
                waits.extend(i['microseconds']/1e6 for i in detail['protocol']['intervals']['wait'])
                joins.extend(i['microseconds']/1e6 for i in detail['protocol']['intervals']['join'])
                for key in stack_counts:
                    stack_counts[key] += detail['stacks'][key]
            cases.append(row)
        if not all(item['identical'] for item in run['clone_integrity']):
            raise ValueError('Clone integrity mismatch')
    return dict(schema=1, method='Three sequential cohorts; all use release-stopped-clones; no injected stall. Controls omit uprobes and stack sampling, but retain memory observation.', cases=cases, case_count=len(cases), passed=sum(c['passed'] for c in cases), failed=sum(not c['passed'] for c in cases), traced=sum(c['traced'] for c in cases), shutdown_seconds={key:dict(count=len(v), min=min(v), median=statistics.median(v), max=max(v)) for key,v in groups.items()}, helper_peak_bytes=peaks, verified_clone_files=clone_count, response_wait_max_seconds=max(waits, default=None), pthread_join_max_seconds=max(joins, default=None), stack_counts=stack_counts, stack_count_note='Empty and error counts can overlap; reads are sequential and observers affect timing.', evidence_index_sha256=sha(folder/'index.json'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args=parser.parse_args()
    result=summarize(args.evidence)
    (args.evidence/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}, indent=2))

if __name__=='__main__':
    main()
