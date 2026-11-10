import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from build import FILES, SOURCE, source_hash, verify_base


class PublicCanaryBuildTests(unittest.TestCase):
    def test_published_manifest_and_local_config_identity_must_both_match(self):
        with tempfile.TemporaryDirectory(prefix=".test-builder-", dir=SOURCE) as root:
            layout = Path(root)
            blobs = layout / "blobs" / "sha256"
            blobs.mkdir(parents=True)
            config = b'{"architecture":"amd64","os":"linux","config":{"User":"10001"},"rootfs":{"type":"layers","diff_ids":["sha256:public-fixture"]}}'
            config_id = "sha256:" + hashlib.sha256(config).hexdigest()
            (blobs / config_id[7:]).write_bytes(config)
            manifest = json.dumps({"config": {"digest": config_id}, "layers": []}).encode()
            pinned = "sha256:" + hashlib.sha256(manifest).hexdigest()
            (blobs / pinned[7:]).write_bytes(manifest)
            image = {"Id": "sha256:daemon-manifest", "Architecture": "amd64", "Os": "linux",
                     "Config": {"User": "10001"}, "RootFS": {"Layers": ["sha256:public-fixture"]}}
            self.assertEqual(verify_base(layout, pinned, image), config_id)
            with self.assertRaises(ValueError):
                verify_base(layout, pinned, {**image, "RootFS": {"Layers": ["sha256:other"]}})
            with self.assertRaises(ValueError):
                verify_base(layout, pinned, {**image, "Config": {"User": "root"}})
            with self.assertRaises(ValueError):
                verify_base(layout, pinned, {**image, "Config": {"User": "10001", "Cmd": ["different"]}})
            (blobs / pinned[7:]).write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                verify_base(layout, pinned, image)

    def test_source_fingerprint_changes_when_shipped_worker_changes(self):
        with tempfile.TemporaryDirectory(prefix=".test-builder-", dir=SOURCE) as root:
            source = Path(root)
            for name in FILES:
                (source / name).write_bytes((SOURCE / name).read_bytes())
            before = source_hash(source)
            (source / "probe-worker.mjs").write_text("different public worker")
            self.assertNotEqual(before, source_hash(source))


if __name__ == "__main__":
    unittest.main()
