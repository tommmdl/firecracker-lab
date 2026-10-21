from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from startup_pid import read_startup_pid

class StartupPidTests(unittest.TestCase):
 def test_created_empty_then_published_file(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'firecracker.pid'
   self.assertIsNone(read_startup_pid(path))
   path.touch();self.assertIsNone(read_startup_pid(path))
   path.write_text('343\n');self.assertEqual(read_startup_pid(path),343)
 def test_malformed_identity_is_not_silently_retried(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'firecracker.pid'
   for value in ('0','-1','343 junk'):
    path.write_text(value)
    with self.assertRaisesRegex(ValueError,'Malformed'):read_startup_pid(path)

if __name__=='__main__':unittest.main()
