import json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from export_pressure_evidence import export

class PressureExportTests(unittest.TestCase):
 def fixture(self, root, trace):
  run=root/'run';run.mkdir();output=root/'out';output.mkdir()
  outer={'memory.pressure':{'some':{'total':0},'full':{'total':0}},'memory.peak':123,'memory.stat':{},'memory.events.local':{}}
  rows=[{'monotonic':1,'phase':'campaign-start','outer':outer,'supervisor':{'read_errors':{'io.stat':'unavailable'}}},{'monotonic':2,'phase':'campaign-end','outer':outer}]
  data={'memory-observations.json':rows,'result.json':{'status':'failed','failure':'startup rejected','scenarios':[],'originals_unchanged':True,'cgroups_cleaned':True},'profile.json':{'diagnostic_repetitions':3,'memory_stall_trace':trace},'launch.json':{'source_sha256':{}}}
  for name,value in data.items():(run/name).write_text(json.dumps(value))
  return run,output
 def test_failed_suite_and_unavailable_counter_remain_visible(self):
  with tempfile.TemporaryDirectory() as d:
   run,out=self.fixture(Path(d),False);result=export(run,out)
   self.assertEqual(result['raw_status'],'failed');self.assertEqual(result['suite_failure'],'startup rejected')
   self.assertEqual(result['planned_cases'],12);self.assertEqual(result['case_count'],0)
   self.assertEqual(result['diagnostic_read_errors'],{'supervisor:io.stat:unavailable':1})
 def test_missing_trace_is_invalid_even_when_psi_is_zero(self):
  with tempfile.TemporaryDirectory() as d:
   run,out=self.fixture(Path(d),True);result=export(run,out)
   self.assertFalse(result['capture_valid']);self.assertTrue(result['capture_errors'])

if __name__=='__main__':unittest.main()
