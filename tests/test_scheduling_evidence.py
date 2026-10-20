import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from export_scheduling_evidence import analyze


def event(t, name, detail, actor=11):
    return f'worker-{actor} [006] d..1. {t:.6f}: {name}: {detail}'


class SchedulingEvidenceTests(unittest.TestCase):
    def test_markers_alone_are_not_a_valid_capture(self):
        result=analyze(event(1,'tracing_mark_write','begin'),[{'trace_tid':11,'name':'fc_vcpu 0'},{'trace_tid':12,'name':'fc_vcpu 1'}])
        self.assertFalse(result['capture_valid'])

    def test_sleep_is_not_counted_as_runnable_delay(self):
        lines=[event(1,'sched_switch','prev_comm=fc_vcpu 0 prev_pid=11 prev_prio=120 prev_state=S ==> next_comm=other next_pid=99 next_prio=120'),
               event(9,'sched_wakeup','comm=fc_vcpu 0 pid=11 prio=120 target_cpu=006',99),
               event(9.002,'sched_switch','prev_comm=other prev_pid=99 prev_prio=120 prev_state=R ==> next_comm=fc_vcpu 0 next_pid=11 next_prio=120',99)]
        result=analyze('\n'.join(lines),[{'trace_tid':11,'name':'fc_vcpu 0'}])
        self.assertEqual(result['vcpus'][0]['longest_runnable_wait_us'],2000)
        self.assertNotIn('next_pid',str(result['events']))

    def test_preemption_counts_until_next_switch_in(self):
        lines=[event(2,'sched_switch','prev_comm=fc_vcpu 0 prev_pid=11 prev_prio=120 prev_state=R+ ==> next_comm=other next_pid=99 next_prio=120'),
               event(2.1,'sched_switch','prev_comm=other prev_pid=99 prev_prio=120 prev_state=R ==> next_comm=fc_vcpu 0 next_pid=11 next_prio=120',99)]
        self.assertEqual(analyze('\n'.join(lines),[{'trace_tid':11,'name':'fc_vcpu 0'}])['vcpus'][0]['longest_runnable_wait_us'],100000)

if __name__=='__main__': unittest.main()
