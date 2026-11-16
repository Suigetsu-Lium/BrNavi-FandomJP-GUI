"""Sequential patch application with local backup and rollback."""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .manifest import PatchValidationError, load_manifest


class PatchApplyError(RuntimeError):
    pass


def read_current_version(install_dir: Path) -> str:
    version_file = install_dir / "VERSION.txt"
    if _is_reparse_point(version_file):
        raise PatchApplyError("VERSION.txt must not be a symbolic link or junction.")
    if not version_file.is_file():
        raise PatchApplyError("VERSION.txt was not found. This installation cannot be patched.")
    value = version_file.read_text(encoding="utf-8-sig").strip()
    if value.startswith("v"):
        value = value[1:]
    if not value:
        raise PatchApplyError("VERSION.txt is empty.")
    return value


def validate_installation(install_dir: Path) -> str:
    """Validate that the target is a deployed BrNavi GUI installation."""
    install_dir = install_dir.resolve()
    executable = install_dir / "BrNavi-GUI.exe"
    if _is_reparse_point(executable):
        raise PatchApplyError("BrNavi-GUI.exe must not be a symbolic link or junction.")
    if not executable.is_file():
        raise PatchApplyError("BrNavi-GUI.exe was not found. Select the deployed application folder.")
    return read_current_version(install_dir)


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _target_path(root: Path, relative: str) -> Path:
    target = root.joinpath(*relative.split("/"))
    # Do not follow symlinks/junctions in any existing component. This prevents
    # a seemingly in-tree path from redirecting writes into user data/outside.
    current = root
    for component in relative.split("/"):
        current = current / component
        if _is_reparse_point(current):
            raise PatchValidationError(f"Patch path traverses a symbolic link or junction: {relative}")
    resolved_root, resolved_target = root.resolve(), target.resolve(strict=False)
    try:
        if os.path.commonpath((str(resolved_root), str(resolved_target))) != str(resolved_root):
            raise PatchValidationError(f"Patch path escapes installation directory: {relative}")
    except ValueError as exc:
        raise PatchValidationError(f"Invalid patch target: {relative}") from exc
    return target


def _backup_parent_path(root: Path) -> Path:
    # update_backups is intentionally excluded from patch manifests, but is
    # the updater's own trusted rollback location.
    backup_parent = root / "update_backups"
    if _is_reparse_point(backup_parent):
        raise PatchApplyError("update_backups must not be a symbolic link or junction.")
    resolved_root = root.resolve()
    resolved_backup = backup_parent.resolve(strict=False)
    try:
        if os.path.commonpath((str(resolved_root), str(resolved_backup))) != str(resolved_root):
            raise PatchApplyError("update_backups resolves outside the installation directory.")
    except ValueError as exc:
        raise PatchApplyError("Invalid update_backups directory.") from exc
    if backup_parent.exists() and not backup_parent.is_dir():
        raise PatchApplyError("update_backups exists but is not a directory.")
    return backup_parent


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _preflight(install_dir: Path, manifest, archive: zipfile.ZipFile) -> list[tuple[object, Path]]:
    planned: list[tuple[object, Path]] = []
    normalized_targets: dict[str, str] = {}
    for item in manifest.files:
        target = _target_path(install_dir, item.path)
        key = item.path.casefold()
        if key in normalized_targets:
            raise PatchApplyError(f"Duplicate target path: {item.path}")
        normalized_targets[key] = item.path
        if target.exists() and target.is_dir():
            raise PatchApplyError(f"Patch target is a directory: {item.path}")
        exists = target.is_file()
        if item.action == "add" and exists:
            raise PatchApplyError(f"Cannot add an existing file: {item.path}")
        if item.action in ("replace", "delete") and not exists:
            raise PatchApplyError(f"Patch target does not exist: {item.path}")
        # Validate payload access before making any filesystem changes.
        if item.action != "delete":
            archive.getinfo(f"payload/{item.path}")
        planned.append((item, target))
    return planned


def apply_patch(
    install_dir: Path,
    patch_path: Path,
    *,
    require_signature: bool = True,
    public_key_bytes: bytes | None = None,
) -> tuple[str, Path]:
    install_dir = install_dir.resolve()
    patch_path = patch_path.resolve()
    if not patch_path.is_file():
        raise PatchApplyError("Patch ZIP file was not found.")
    if not install_dir.is_dir():
        raise PatchApplyError("BrNavi installation directory was not found.")

    current_version = validate_installation(install_dir)
    with zipfile.ZipFile(patch_path, "r") as archive:
        manifest = load_manifest(
            archive,
            require_signature=require_signature,
            public_key_bytes=public_key_bytes,
        )
        if current_version != manifest.from_version:
            raise PatchApplyError(
                f"This patch requires v{manifest.from_version}; installed version is v{current_version}."
            )
        planned = _preflight(install_dir, manifest, archive)
        backup_parent = _backup_parent_path(install_dir)
        backup_root = backup_parent / (
            f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{manifest.from_version}_to_{manifest.to_version}"
        )
        backup_root.mkdir(parents=True, exist_ok=False)
        touched: list[tuple[Path, Path | None]] = []
        try:
            for item, target in planned:
                backup = None
                if target.is_file():
                    backup = backup_root / item.path
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                # Record before mutation so an interrupted write is included in rollback.
                touched.append((target, backup))
                if item.action == "delete":
                    target.unlink()
                else:
                    _write_atomic(target, archive.read(f"payload/{item.path}"))

            version_target = install_dir / "VERSION.txt"
            version_backup = backup_root / "VERSION.txt"
            shutil.copy2(version_target, version_backup)
            touched.append((version_target, version_backup))
            _write_atomic(version_target, f"v{manifest.to_version}\n".encode("utf-8"))
        except Exception as exc:
            rollback_errors = []
            for target, backup in reversed(touched):
                try:
                    if backup is not None and backup.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup, target)
                    elif target.is_file():
                        target.unlink()
                except OSError as rollback_exc:
                    rollback_errors.append(f"{target}: {rollback_exc}")
            detail = f"Patch failed; rollback was attempted. Details: {exc}"
            if rollback_errors:
                detail += " Rollback errors: " + "; ".join(rollback_errors)
            raise PatchApplyError(detail) from exc

    return manifest.to_version, backup_root
