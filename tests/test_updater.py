from __future__ import annotations
import hashlib, json, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import updater.patch_applier as patch_applier
from updater.manifest import PatchValidationError, load_manifest
from updater.patch_applier import PatchApplyError, apply_patch

def make_patch(path: Path, from_version="1.0.0", to_version="1.0.1", operations=None):
    operations = operations or [("modules/sample.txt", "replace", b"new contents")]
    files, payloads = [], {}
    for relative, action, data in operations:
        entry = {"path": relative, "action": action}
        if action != "delete":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            payloads[f"payload/{relative}"] = data
        files.append(entry)
    manifest = {"patch_format": 1, "application": "BrNavi Fandom JP",
                "from_version": from_version, "to_version": to_version, "files": files}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("patch_manifest.json", json.dumps(manifest))
        for name, data in payloads.items():
            archive.writestr(name, data)

class ManifestTests(unittest.TestCase):
    def test_valid_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "patch.zip"
            make_patch(path)
            with zipfile.ZipFile(path) as archive:
                manifest = load_manifest(archive)
            self.assertEqual(manifest.to_version, "1.0.1")

    def test_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "patch.zip"
            make_patch(path, operations=[("../outside.txt", "delete", None)])
            with zipfile.ZipFile(path) as archive:
                with self.assertRaises(PatchValidationError):
                    load_manifest(archive)

    def test_rejects_user_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "patch.zip"
            make_patch(path, operations=[("database/app.db", "delete", None)])
            with zipfile.ZipFile(path) as archive:
                with self.assertRaises(PatchValidationError):
                    load_manifest(archive)

    def test_rejects_corrupt_payload_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "patch.zip"
            make_patch(path)
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("payload/modules/sample.txt", b"tampered")
            with zipfile.ZipFile(path) as archive:
                with self.assertRaises(PatchValidationError):
                    load_manifest(archive)

class ApplyTests(unittest.TestCase):
    def test_replace_and_preserve_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION.txt").write_text("v1.0.0", encoding="utf-8")
            (root / "modules").mkdir()
            (root / "modules/sample.txt").write_text("old", encoding="utf-8")
            (root / "database").mkdir()
            (root / "database/app.db").write_bytes(b"valuable")
            patch = root / "patch.zip"
            make_patch(patch)
            version, backup = apply_patch(root, patch, require_signature=False)
            self.assertEqual(version, "1.0.1")
            self.assertEqual((root / "VERSION.txt").read_text(encoding="utf-8"), "v1.0.1\\n")
            self.assertEqual((root / "modules/sample.txt").read_text(), "new contents")
            self.assertEqual((root / "database/app.db").read_bytes(), b"valuable")
            self.assertEqual((backup / "modules/sample.txt").read_text(), "old")

    def test_rejects_wrong_source_version_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION.txt").write_text("v1.0.0", encoding="utf-8")
            (root / "modules").mkdir()
            (root / "modules/sample.txt").write_text("old", encoding="utf-8")
            patch = root / "patch.zip"
            make_patch(patch, from_version="1.0.2", to_version="1.0.3")
            with self.assertRaises(PatchApplyError):
                apply_patch(root, patch, require_signature=False)
            self.assertEqual((root / "modules/sample.txt").read_text(), "old")

    def test_add_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION.txt").write_text("v1.0.0", encoding="utf-8")
            (root / "old.txt").write_text("remove", encoding="utf-8")
            patch = root / "patch.zip"
            make_patch(patch, operations=[
                ("old.txt", "delete", None),
                ("new.txt", "add", b"added"),
            ])
            apply_patch(root, patch, require_signature=False)
            self.assertFalse((root / "old.txt").exists())
            self.assertEqual((root / "new.txt").read_bytes(), b"added")

    def test_failed_write_rolls_back_files_and_keeps_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION.txt").write_text("v1.0.0", encoding="utf-8")
            (root / "modules").mkdir()
            target = root / "modules/sample.txt"
            target.write_text("original", encoding="utf-8")
            patch_zip = root / "patch.zip"
            make_patch(patch_zip)
            with patch.object(patch_applier, "_write_atomic", side_effect=OSError("simulated disk error")):
                with self.assertRaises(PatchApplyError):
                    apply_patch(root, patch_zip, require_signature=False)
            self.assertEqual(target.read_text(encoding="utf-8"), "original")
            self.assertEqual((root / "VERSION.txt").read_text(encoding="utf-8"), "v1.0.0")

if __name__ == "__main__":
    unittest.main()
