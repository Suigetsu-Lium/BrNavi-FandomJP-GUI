"""Sequential patch application with local backup and rollback."""
from __future__ import annotations
import os, shutil, tempfile, zipfile
from datetime import datetime
from pathlib import Path
from .manifest import PatchValidationError, load_manifest

class PatchApplyError(RuntimeError):
    pass

def read_current_version(install_dir: Path) -> str:
    version_file = install_dir / "VERSION.txt"
    if not version_file.is_file():
        raise PatchApplyError("VERSION.txt was not found. This installation cannot be patched.")
    value = version_file.read_text(encoding="utf-8-sig").strip()
    if value.startswith("v"): value = value[1:]
    if not value: raise PatchApplyError("VERSION.txt is empty.")
    return value

def _target_path(root: Path, relative: str) -> Path:
    target = root.joinpath(*relative.split("/"))
    resolved_root, resolved_target = root.resolve(), target.resolve(strict=False)
    if os.path.commonpath((str(resolved_root), str(resolved_target))) != str(resolved_root):
        raise PatchValidationError(f"Patch path escapes installation directory: {relative}")
    return target

def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except Exception:
        try: os.unlink(temp_name)
        except OSError: pass
        raise

def apply_patch(install_dir: Path, patch_path: Path) -> tuple[str, Path]:
    install_dir, patch_path = install_dir.resolve(), patch_path.resolve()
    if not patch_path.is_file(): raise PatchApplyError("Patch ZIP file was not found.")
    current_version = read_current_version(install_dir)
    with zipfile.ZipFile(patch_path, "r") as archive:
        manifest = load_manifest(archive)
        if current_version != manifest.from_version:
            raise PatchApplyError(f"This patch requires v{manifest.from_version}; installed version is v{current_version}.")
        backup_root = install_dir / "update_backups" / (
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{manifest.from_version}_to_{manifest.to_version}")
        backup_root.mkdir(parents=True, exist_ok=False)
        touched = []
        try:
            for item in manifest.files:
                target = _target_path(install_dir, item.path)
                if target.exists() and target.is_dir():
                    raise PatchApplyError(f"Patch target is a directory: {item.path}")
                backup = None
                if target.is_file():
                    backup = backup_root / item.path
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                if item.action == "add" and target.exists():
                    raise PatchApplyError(f"Cannot add an existing file: {item.path}")
                if item.action in ("replace", "delete") and not target.is_file():
                    raise PatchApplyError(f"Patch target does not exist: {item.path}")
                touched.append((target, backup))
                if item.action == "delete": target.unlink()
                else: _write_atomic(target, archive.read(f"payload/{item.path}"))
            version_target = install_dir / "VERSION.txt"
            version_backup = None
            if version_target.is_file():
                version_backup = backup_root / "VERSION.txt"
                shutil.copy2(version_target, version_backup)
            touched.append((version_target, version_backup))
            _write_atomic(version_target, f"v{manifest.to_version}\n".encode("utf-8"))
        except Exception as exc:
            for target, backup in reversed(touched):
                try:
                    if backup and backup.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(backup, target)
                    elif target.is_file(): target.unlink()
                except OSError: pass
            raise PatchApplyError(f"Patch failed; rollback was attempted. Details: {exc}") from exc
    return manifest.to_version, backup_root
