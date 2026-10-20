import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('boot_check', Path(__file__).resolve().parents[1] / 'scripts/boot_check.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


class ConsoleTests(unittest.TestCase):
    token = 'testnonce'
    lines = ['FC_BEGIN_testnonce', 'uid=0', 'kernel=6.18.54+', 'rootfs=/dev/vda ext4 rw,relatime', 'FC_END_testnonce']

    def sample(self, ending):
        return (ending.join(self.lines) + ending).encode()

    def test_line_endings_and_ansi(self):
        for ending in ('\n', '\r\n', '\r\r\n', '\r\r\r\n', '\r'):
            with self.subTest(ending=repr(ending)):
                self.assertEqual(boot.guest_evidence(b'\x1b[32m' + self.sample(ending) + b'\x1b[0m', self.token)['uid'], '0')

    def test_fragmented_reads_wait_for_whole_end_marker(self):
        data = self.sample('\r\r\n')
        end = data.index(b'FC_END_') + len(b'FC_END_')
        for position in range(end, end + len(self.token)):
            self.assertIsNone(boot.guest_evidence(data[:position], self.token))
        self.assertIsNotNone(boot.guest_evidence(data, self.token))

    def test_echoed_command_does_not_pass(self):
        echo = b"root@guest:~# printf '\\nFC_BEGIN_testnonce\\n'; printf 'uid=0\\nkernel=6.18.54+\\nrootfs=/dev/vda ext4 rw,relatime\\n'; printf 'FC_END_testnonce\\n'\r\r\n"
        self.assertIsNone(boot.guest_evidence(echo, self.token))

    def test_wrong_nonce_does_not_pass(self):
        self.assertIsNone(boot.guest_evidence(self.sample('\n'), 'anothernonce'))

    def test_missing_end_does_not_pass(self):
        self.assertIsNone(boot.guest_evidence(b'FC_BEGIN_testnonce\nuid=0\n', self.token))

    def test_invalid_guest_evidence_fails(self):
        for old, new in [('uid=0', 'uid=1000'), ('kernel=6.18.54+', 'kernel='), ('ext4 rw,', 'ext4 ro,'), ('/dev/vda', '/dev/vdb')]:
            with self.subTest(new=new), self.assertRaises(ValueError):
                boot.guest_evidence(self.sample('\n').replace(old.encode(), new.encode()), self.token)

    def test_historical_framing(self):
        raw = (Path(__file__).parent / 'fixtures/historical-framing.txt').read_bytes()
        normalized = boot.normalize_console(raw)
        self.assertIn('\nAUDIT_GUEST_END\n', normalized)
        self.assertNotIn('\nAUDIT_GUEST_END\n', raw.decode())
        self.assertIn('root (automatic login)', normalized)


if __name__ == '__main__':
    unittest.main()
