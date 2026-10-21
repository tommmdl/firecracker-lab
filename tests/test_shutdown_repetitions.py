import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from export_shutdown_repetitions import summarize

class RepetitionSummaryTests(unittest.TestCase):
    def fixture(self, root, failure=None):
        (root/'protocol').mkdir()
        (root/'index.json').write_text(json.dumps({'runs':[{'file':'run.json','run':'run','outer_memory_peak_bytes':123}]}))
        (root/'run.json').write_text(json.dumps({'clone_integrity':[{'identical':True}]}))
        cases=[dict(scenario='half-control-01', exit_code=0, failure=failure, traced=False, shutdown={'seconds':.2}), dict(scenario='half-trace-01', exit_code=0, traced=True, shutdown={'seconds':.3}, protocol_valid=True, scheduler_valid=True, stacks_nonempty=1, detail_file='detail.json')]
        (root/'protocol/run.json').write_text(json.dumps({'scenarios':cases}))
        (root/'protocol/detail.json').write_text(json.dumps({'protocol':{'intervals':{'wait':[{'microseconds':500}], 'join':[{'microseconds':20}]}}, 'stacks':{'empty':2,'errors':1,'nonempty':1}}))
    def test_pools_cases_and_converts_protocol_microseconds(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self.fixture(root); result=summarize(root)
            self.assertEqual((result['case_count'],result['passed'],result['traced']),(2,2,1))
            self.assertEqual(result['response_wait_max_seconds'],.0005)
            self.assertEqual(result['stack_counts']['errors'],1)
    def test_zero_exit_does_not_hide_failed_check(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self.fixture(root, 'isolation check failed'); result=summarize(root)
            self.assertEqual(result['failed'],1)
            self.assertEqual(result['cases'][0]['failure'],'isolation check failed')
    def test_integrity_mismatch_rejects_summary(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self.fixture(root)
            (root/'run.json').write_text(json.dumps({'clone_integrity':[{'identical':False}]}))
            with self.assertRaisesRegex(ValueError,'integrity'): summarize(root)
