import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from pressure_analysis import pressure_deltas,parse_trace,correlate

def row(t,phase,some,full):
 return dict(monotonic=t,phase=phase,outer={'memory.pressure':{'some':{'total':some},'full':{'total':full}}})

class PressureAnalysisTests(unittest.TestCase):
 def test_total_deltas_keep_phase_and_full_only_increment(self):
  rows=[row(1,'x:before-prepare',0,0),row(2,'periodic',3,1),row(3,'periodic',3,2)]
  result=pressure_deltas(rows)
  self.assertEqual(len(result),2)
  self.assertEqual(result[1]['phase'],'x:before-prepare')
  self.assertEqual(result[1]['memory_delta_us'],dict(some=0,full=1))
 def test_counter_reset_is_not_reported_as_a_negative_stall(self):
  with self.assertRaisesRegex(ValueError,'decreased'):pressure_deltas([row(1,'start',3,2),row(2,'end',0,0)])
 def test_stack_parser_removes_addresses_and_retains_symbols(self):
  text='python-42 [006] ..... 12.123456: enter: (psi_memstall_enter+0x4/0x80)\npython-42 [006] ..... 12.123457: <stack trace>\n => [<ffffffff81234567>] filemap_read+0x1/0x20\n => 0xffffffff81234567\n'
  result=parse_trace(text)
  self.assertEqual(result[0]['tid'],42)
  self.assertEqual(result[0]['stack'],['filemap_read+0x1/0x20','<address>'])
 def test_overlap_does_not_invent_events_for_missing_capture(self):
  intervals=pressure_deltas([row(1,'start',0,0),row(2,'end',5,2)])
  self.assertEqual(correlate([],intervals)[0]['overlapping_probe_events'],[])

if __name__=='__main__':unittest.main()
