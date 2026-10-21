#!/usr/bin/env python3
"""Same protocol matrix with cgroup memory attribution and stopped-clone A/B."""
import json
import time

import vcpu_protocol  # installs the existing protocol capture and shutdown
import vcpu_scheduling as scheduling
from jailer_checks import CG,OUTPUT,PROFILE,VM as BaseVM,digest
from helper_memory import MemoryObserver

observer=MemoryObserver(CG,OUTPUT/'memory-observations.json')
policy=PROFILE.get('helper_cache_policy','retain')


class ObservedVM(BaseVM):
    def __init__(self,name,*args,**kwargs):
        observer.mark(name+':before-prepare')
        super().__init__(name,*args,**kwargs)
        observer.mark(name+':after-prepare')
    def boot(self):
        observer.mark(self.name+':before-boot');super().boot();observer.mark(self.name+':after-boot')
    def wait_keyboard(self):
        super().wait_keyboard();observer.mark(self.name+':keyboard-ready')
    def close(self):
        observer.mark(self.name+':before-close')
        super().close()

    def after_stop(self):
        observer.mark(self.name+':after-close-before-advice')
        super().after_stop()
        observer.mark(self.name+':after-cache-policy')


original_shutdown=scheduling.shutdown

def observed_shutdown(vm,quota):
    observer.mark(vm.name+':before-shutdown')
    try:return original_shutdown(vm,quota)
    finally:observer.mark(vm.name+':after-shutdown')


def main():
    if policy not in ('retain','release-stopped-clones'):raise ValueError('Unknown helper cache policy')
    scheduling.VM=ObservedVM;scheduling.shutdown=observed_shutdown
    observer.start()
    try:return scheduling.main()
    finally:
        observer.close()
        target=OUTPUT/'result.json'
        if target.exists():
            report=json.loads(target.read_text());report['helper_memory']={'policy':policy,'observations_sha256':digest(observer.output),'samples':len(observer.rows),'sample_interval_target_seconds':.1,'limits_unchanged':True}
            target.write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':raise SystemExit(main())
