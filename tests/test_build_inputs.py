import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('build_artifacts', Path(__file__).resolve().parents[1] / 'scripts/build_artifacts.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildInputsTests(unittest.TestCase):
    def test_changed_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.tar'
            path.write_bytes(b'verified source')
            expected = build.sha256(path)
            self.assertEqual(build.require_hash(path, expected), expected)
            path.write_bytes(b'verified source with a change')
            with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                build.require_hash(path, expected)

    def test_missing_driver_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / '.config'
            fragment = Path(directory) / 'required.fragment'
            fragment.write_text('CONFIG_VIRTIO_BLK=y\nCONFIG_NET=n\n')
            config.write_text('# CONFIG_VIRTIO_BLK is not set\n# CONFIG_NET is not set\n')
            with self.assertRaisesRegex(ValueError, 'VIRTIO_BLK'):
                build.require_config(config, fragment)
            config.write_text('CONFIG_VIRTIO_BLK=y\n# CONFIG_NET is not set\n')
            build.require_config(config, fragment)


if __name__ == '__main__':
    unittest.main()
