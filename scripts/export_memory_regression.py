#!/usr/bin/env python3
"""Retain the original jailer regression, including rejected startup snapshots."""
import argparse,json
from pathlib import Path
from export_helper_memory import sha


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',nargs='+',required=True);p.add_argument('--output',required=True);a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
 for arg in a.runs:
  run=Path(arg);report=json.loads((run/'result.json').read_text());checks=[]
  for case in report['scenarios']:
   for item in case.get('cache_release',{}).get('files',[]):
    path=run/case['scenario']/'jails/firecracker'/case['scenario']/'root'/item['file'];actual=sha(path)
    if actual!=item['sha256_before_advice']:raise ValueError('Stopped clone integrity failure')
    checks.append({'scenario':case['scenario'],'file':item['file'],'sha256_before_advice':item['sha256_before_advice'],'sha256_after_campaign':actual,'identical':True})
  reviewed={'run':run.name,'raw_result_sha256':sha(run/'result.json'),'profile':json.loads((run/'profile.json').read_text()),'source_sha256':json.loads((run/'launch.json').read_text())['source_sha256'],'report':report,'clone_integrity':checks,'interpretation':'Negative controls intentionally reject a profile, exhaust resources, or kill a VMM; passed means expected detector/control behavior, not a healthy guest.'}
  (out/(run.name+'.json')).write_text(json.dumps(reviewed,indent=2)+'\n')
if __name__=='__main__':main()
