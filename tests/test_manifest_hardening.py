from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from updater.manifest import PatchValidationError, load_manifest
from tests.test_updater import make_patch


class ManifestHardeningTests(unittest.TestCase):
    def _assert_invalid(self, operations, from_version="1.0.0", to_version="1.0.1"):
        with tempfile.TemporaryDirectory() as tmp:
            patch = Path(tmp) / "patch.zip"
            make_patch(patch, from_version, to_version, operations)
            with zipfile.ZipFile(patch) as archive:
                with self.assertRaises(PatchValidationError):
                    load_manifest(archive)

    def test_rejects_noncanonical_version(self):
        self._assert_invalid(
            [("module.py", "add", b"x")],
            from_version="v1.0.0",
        )

    def test_rejects_file_directory_prefix_collision(self):
        self._assert_invalid([
            ("a", "add", b"file"),
            ("a/b", "add", b"child"),
        ])

    def test_rejects_empty_path_segment(self):
        self._assert_invalid([("modules//sample.py", "delete", None)])

    def test_rejects_unreferenced_zip_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            patch = Path(tmp) / "patch.zip"
            make_patch(patch)
            with zipfile.ZipFile(patch, "a") as archive:
                archive.writestr("payload/unused.bin", b"unused")
            with zipfile.ZipFile(patch) as archive:
                with self.assertRaises(PatchValidationError):
                    load_manifest(archive)


if __name__ == "__main__":
    unittest.main()
