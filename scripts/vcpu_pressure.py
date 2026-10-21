#!/usr/bin/env python3
"""Existing shutdown matrix with opt-in PSI, I/O and kernel-stack attribution."""
import vcpu_memory
from helper_memory import MemoryObserver
from jailer_checks import CG, OUTPUT, PROFILE
from pressure_trace import PressureTrace


def main():
    capture=PressureTrace(OUTPUT) if PROFILE.get('memory_stall_trace',True) else None
    vcpu_memory.observer=MemoryObserver(CG,OUTPUT/'memory-observations.json',diagnostics=True)
    try:
        if capture:capture.start()
        return vcpu_memory.main()
    finally:
        if capture:capture.close()


if __name__=='__main__':raise SystemExit(main())
