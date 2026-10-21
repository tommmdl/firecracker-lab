import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from protocol_analysis import analyze_protocol,symbolize_stack
from protocol_points import verify_binary

class ProtocolTests(unittest.TestCase):
    def timeline(self):
        events=[(10,'send_enter'),(10,'send_return'),(10,'send_enter'),(10,'send_return')]
        events += [(10,'drop_enter'),(10,'wait_enter'),(11,'run_return'),(12,'run_return'),(10,'wait_return'),(10,'disconnected'),(10,'join_enter'),(10,'join_return'),(10,'drop_return')]
        events += [(10,'drop_enter'),(10,'wait_enter'),(10,'wait_return'),(10,'disconnected'),(10,'join_enter'),(10,'join_return'),(10,'drop_return')]
        return '\n'.join(f'worker-{tid} [006] DBZff {1+i*.001:.6f}: {event}: (0x1234)' for i,(tid,event) in enumerate(events))
    def test_interleaved_vcpu_returns_do_not_break_main_protocol(self):
        result,_=analyze_protocol(self.timeline(),[{'trace_tid':11,'name':'fc_vcpu 0'},{'trace_tid':12,'name':'fc_vcpu 1'}])
        self.assertTrue(result['valid'],result['errors'])
        self.assertEqual(len(result['intervals']['drop']),2)
    def test_missing_join_is_not_success(self):
        result,_=analyze_protocol(self.timeline().replace('join_return:','unrelated:'),[{'trace_tid':11,'name':'fc_vcpu 0'},{'trace_tid':12,'name':'fc_vcpu 1'}])
        self.assertFalse(result['valid'])
    def test_address_is_removed_but_kernel_symbol_is_retained(self):
        self.assertEqual(symbolize_stack('[<ffff88801234>] futex_wait+0x1/0x2\n'),['futex_wait+0x1/0x2'])
    def test_unreviewed_binary_rejected(self):
        self.assertRaises(ValueError,verify_binary,Path(__file__))
