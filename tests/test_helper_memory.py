import hashlib
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from helper_memory import release_stopped_clones

class CacheReleaseTests(unittest.TestCase):
    def test_active_vm_is_rejected_before_file_access(self):
        with self.assertRaisesRegex(ValueError,'alive'):
            release_stopped_clones('/does-not-exist',stopped=False)
    def test_advice_preserves_disk_and_kernel_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in ('rootfs.ext4','vmlinux'):(root/name).write_bytes(bytes(range(256))*64)
            before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
            result=release_stopped_clones(root,stopped=True)
            self.assertEqual(len(result),2)
            self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()})
    def test_symlink_cannot_redirect_advice_to_input(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);root=base/'clone';root.mkdir();original=base/'input';original.write_bytes(b'original')
            (root/'rootfs.ext4').symlink_to(original)
            with self.assertRaisesRegex(ValueError,'non-owned'):
                release_stopped_clones(root,stopped=True)
            self.assertEqual(original.read_bytes(),b'original')

if __name__=='__main__':unittest.main()
