from __future__ import annotations
import hashlib, json, os, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import updater.patch_applier as patch_applier
from updater.manifest import PatchValidationError, load_manifest
from updater.patch_applier import (\n    PatchApplyError, apply_patch, read_current_version, recover_interrupted_updates, validate_installation,\n)

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
            (root / "BrNavi-GUI.exe").write_bytes(b"test executable")
            (root / "VERSION.txt").write_text("v1.0.0", encoding="utf-8")
            (root / "modules").mkdir()
            (root / "modules/sample.txt").write_text("old", encoding="utf-8")
            (root / "database").mkdir()
            (root / "database/app.db").write_bytes(b"valuable")
            patch = root / "patch.zip"
            make_patch(patch)
            version, backup = apply_patch(root, patch, require_signature=False)
            self.assertEqual(version, "1.0.1")
            self.assertEqual((root / "VERSION.txt").read_text(encoding="utf-8"), "v1.0.1\n")
            self.assertEqual((root / "modules/sample.txt").read_text(), "new contents")
            self.assertEqual((root / "database/app.db").read_bytes(), b"valuable")
            self.assertEqual((backup / "modules/sample.txt").read_text(), "old")

    def test_rejects_wrong_source_version_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BrNavi-GUI.exe").write_bytes(b"test executable")
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
            (root / "BrNavi-GUI.exe").write_bytes(b"test executable")
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
            (root / "BrNavi-GUI.exe").write_bytes(b"test executable")
            (root / "VERSION.txt").write_text("v1.0.0", encoding="utf-8")
            (root / "modules").mkdir()
            target = root / "modules/sample.txt"
            target.write_text("original", encoding="utf-8")
            patch_zip = root / "patch.zip"
            make_patch(patch_zip)
            original_write_atomic = patch_applier._write_atomic
            failed_target_write = False

            def fail_once_on_target(path, data):
                nonlocal failed_target_write
                if path == target and not failed_target_write:
                    failed_target_write = True
                    raise OSError("simulated disk error")
                return original_write_atomic(path, data)

            with patch.object(patch_applier, "_write_atomic", side_effect=fail_once_on_target):
                with self.assertRaises(PatchApplyError):
                    apply_patch(root, patch_zip, require_signature=False)
            self.assertEqual(target.read_text(encoding="utf-8"), "original")
            self.assertEqual((root / "VERSION.txt").read_text(encoding="utf-8"), "v1.0.0")


    def test_recovers_interrupted_apply_and_removes_added_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BrNavi-GUI.exe").write_bytes(b"test executable")
            (root / "VERSION.txt").write_text("v1.0.1\n", encoding="utf-8")
            (root / "modules").mkdir()
            target = root / "modules/sample.txt"
            target.write_bytes(b"new contents")
            added = root / "modules/added.txt"
            added.write_bytes(b"new file")

            backup_root = root / "update_backups" / "interrupted"
            backup_file = backup_root / "files/modules/sample.txt"
            version_backup = backup_root / "files/VERSION.txt"
            backup_file.parent.mkdir(parents=True)
            (backup_root / patch_applier.TRANSACTION_MARKER_NAME).write_bytes(b"BRNAVI-UPDATE-TRANSACTION-1")
            backup_file.write_bytes(b"original")
            version_backup.write_bytes(b"v1.0.0\n")
            journal = {
                "schema": patch_applier.JOURNAL_SCHEMA,
                "state": "applying",
                "from_version": "1.0.0",
                "to_version": "1.0.1",
                "entries": [
                    {"path": "modules/sample.txt", "existed": True,
                     "sha256": hashlib.sha256(b"original").hexdigest()},
                    {"path": "modules/added.txt", "existed": False, "sha256": None},
                    {"path": "VERSION.txt", "existed": True,
                     "sha256": hashlib.sha256(b"v1.0.0\n").hexdigest()},
                ],
            }
            patch_applier._write_journal(backup_root, journal)

            recovered = recover_interrupted_updates(root)

            self.assertEqual(recovered, [backup_root])
            self.assertEqual(target.read_bytes(), b"original")
            self.assertFalse(added.exists())
            self.assertEqual((root / "VERSION.txt").read_bytes(), b"v1.0.0\n")
            self.assertEqual(
                json.loads((backup_root / patch_applier.JOURNAL_NAME).read_text())["state"],
                "rolled_back",
            )

    def test_preparing_transaction_does_not_change_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "modules/sample.txt"
            target.parent.mkdir()
            target.write_bytes(b"original")
            backup_root = root / "update_backups" / "preparing"
            backup_root.mkdir(parents=True)
            (backup_root / patch_applier.TRANSACTION_MARKER_NAME).write_bytes(b"BRNAVI-UPDATE-TRANSACTION-1")
            journal = {
                "schema": patch_applier.JOURNAL_SCHEMA,
                "state": "preparing",
                "from_version": "1.0.0",
                "to_version": "1.0.1",
                "entries": [{"path": "modules/sample.txt", "existed": True, "sha256": None}],
            }
            patch_applier._write_journal(backup_root, journal)

            self.assertEqual(recover_interrupted_updates(root), [])
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(
                json.loads((backup_root / patch_applier.JOURNAL_NAME).read_text())["state"],
                "rolled_back",
            )

    def test_recovery_fails_closed_when_backup_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "modules/sample.txt"
            target.parent.mkdir()
            target.write_bytes(b"possibly updated")
            backup_root = root / "update_backups" / "incomplete"
            backup_root.mkdir(parents=True)
            (backup_root / patch_applier.TRANSACTION_MARKER_NAME).write_bytes(b"BRNAVI-UPDATE-TRANSACTION-1")
            journal = {
                "schema": patch_applier.JOURNAL_SCHEMA,
                "state": "applying",
                "from_version": "1.0.0",
                "to_version": "1.0.1",
                "entries": [
                    {"path": "modules/sample.txt", "existed": True, "sha256": hashlib.sha256(b"original").hexdigest()}
                ],
            }
            patch_applier._write_journal(backup_root, journal)

            with self.assertRaises(PatchApplyError):
                recover_interrupted_updates(root)
            self.assertEqual(target.read_bytes(), b"possibly updated")

    def test_refuses_non_directory_backup_location_without_modifying_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BrNavi-GUI.exe").write_bytes(b"test executable")
            (root / "VERSION.txt").write_text("v1.0.0", encoding="utf-8")
            (root / "modules").mkdir()
            target = root / "modules/sample.txt"
            target.write_text("original", encoding="utf-8")
            (root / "update_backups").write_text("not a directory", encoding="utf-8")
            patch_zip = root / "patch.zip"
            make_patch(patch_zip)
            with self.assertRaises(PatchApplyError):
                apply_patch(root, patch_zip, require_signature=False)
            self.assertEqual(target.read_text(encoding="utf-8"), "original")
            self.assertEqual((root / "VERSION.txt").read_text(encoding="utf-8"), "v1.0.0")

    def test_rejects_redirected_version_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root.parent / f"{root.name}-external-version.txt"
            external.write_text("v1.0.0", encoding="utf-8")
            try:
                (root / "VERSION.txt").symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this runner.")
            try:
                with self.assertRaises(PatchApplyError):
                    read_current_version(root)
            finally:
                external.unlink(missing_ok=True)

    def test_rejects_folder_without_brnavi_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION.txt").write_text("v1.0.0", encoding="utf-8")
            with self.assertRaises(PatchApplyError):
                validate_installation(root)

if __name__ == "__main__":
    unittest.main()
