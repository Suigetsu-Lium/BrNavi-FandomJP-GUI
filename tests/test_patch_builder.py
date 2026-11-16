from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from updater.build_patch import PatchBuildError, build_patch
from updater.manifest import load_manifest


class PatchBuilderTests(unittest.TestCase):
    def _release(self, root: Path, version: str, files: dict[str, bytes]) -> Path:
        root.mkdir()
        (root / "VERSION.txt").write_text(f"v{version}\n", encoding="utf-8")
        for relative, data in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return root

    def test_builds_add_replace_delete_and_excludes_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = self._release(base / "old", "1.0.0", {
                "BrNavi-GUI.exe": b"old exe",
                "modules/changed.py": b"old",
                "modules/removed.py": b"remove",
                "database/app.db": b"private database",
                "downloaded_images/photo.png": b"private image",
            })
            new = self._release(base / "new", "1.0.1", {
                "BrNavi-GUI.exe": b"new exe",
                "modules/changed.py": b"new",
                "modules/added.py": b"added",
                "database/app.db": b"should never be packaged",
            })
            patch = base / "update.zip"
            result = build_patch(old, new, patch)
            self.assertEqual(result["operation_count"], 4)
            with zipfile.ZipFile(patch) as archive:
                manifest = load_manifest(archive)
                paths = {item.path: item.action for item in manifest.files}
                self.assertEqual(paths, {
                    "BrNavi-GUI.exe": "replace",
                    "modules/added.py": "add",
                    "modules/changed.py": "replace",
                    "modules/removed.py": "delete",
                })
                names = archive.namelist()
                self.assertFalse(any("database" in name or "downloaded_images" in name for name in names))

    def test_bootstrap_adds_updater_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = self._release(base / "old", "1.0.0", {"BrNavi-GUI.exe": b"same"})
            new = self._release(base / "new", "1.0.1", {"BrNavi-GUI.exe": b"same"})
            updater = base / "Updater.exe"
            updater.write_bytes(b"updater binary")
            patch = base / "bootstrap.zip"
            build_patch(old, new, patch, updater)
            with zipfile.ZipFile(patch) as archive:
                manifest = load_manifest(archive)
                updater_op = [item for item in manifest.files if item.path == "Updater.exe"]
                self.assertEqual(len(updater_op), 1)
                self.assertEqual(updater_op[0].action, "add")

    def test_rejects_non_sequential_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = self._release(base / "old", "1.0.1", {"BrNavi-GUI.exe": b"a"})
            new = self._release(base / "new", "1.0.0", {"BrNavi-GUI.exe": b"b"})
            with self.assertRaises(PatchBuildError):
                build_patch(old, new, base / "bad.zip")

    def test_reproducible_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = self._release(base / "old", "1.0.0", {"a.bin": b"old"})
            new = self._release(base / "new", "1.0.1", {"a.bin": b"new"})
            first, second = base / "first.zip", base / "second.zip"
            build_patch(old, new, first)
            build_patch(old, new, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
