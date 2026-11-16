"""Sequential patch application with durable crash recovery and rollback."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .manifest import PatchValidationError, _safe_relative_path, load_manifest, normalize_version

JOURNAL_NAME = ".brnavi-update-journal.json"
TRANSACTION_MARKER_NAME = ".brnavi-transaction"
JOURNAL_SCHEMA = 1
_TRANSACTION_STATES = {"preparing", "applying", "rolling_back", "rolled_back", "committed"}


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


def _write_journal(backup_root: Path, journal: dict) -> None:
    data = (json.dumps(journal, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    _write_atomic(backup_root / JOURNAL_NAME, data)


def _read_journal(backup_root: Path) -> dict:
    marker_path = backup_root / TRANSACTION_MARKER_NAME
    if _is_reparse_point(marker_path) or not marker_path.is_file():
        raise PatchApplyError(f"Update transaction marker is missing or redirected: {marker_path}")
    if marker_path.read_bytes() != b"BRNAVI-UPDATE-TRANSACTION-1":
        raise PatchApplyError(f"Update transaction marker is invalid: {marker_path}")
    journal_path = backup_root / JOURNAL_NAME
    if _is_reparse_point(journal_path) or not journal_path.is_file():
        raise PatchApplyError(f"Update recovery journal is missing or redirected: {journal_path}")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PatchApplyError(f"Update recovery journal is unreadable: {journal_path}") from exc
    if (not isinstance(journal, dict) or type(journal.get("schema")) is not int
            or journal.get("schema") != JOURNAL_SCHEMA
            or journal.get("state") not in _TRANSACTION_STATES
            or not isinstance(journal.get("entries"), list) or not journal["entries"]
            or not isinstance(journal.get("from_version"), str)
            or not isinstance(journal.get("to_version"), str)):
        raise PatchApplyError(f"Update recovery journal has an unsupported or invalid structure: {journal_path}")
    try:
        if normalize_version(journal["to_version"]) <= normalize_version(journal["from_version"]):
            raise PatchApplyError("Update recovery journal contains an invalid version transition.")
    except PatchValidationError as exc:
        raise PatchApplyError("Update recovery journal contains an invalid version.") from exc
    paths = []
    for entry in journal["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise PatchApplyError("Update recovery journal contains an invalid target entry.")
        paths.append(entry["path"].casefold())
    if len(paths) != len(set(paths)) or paths.count("version.txt") != 1:
        raise PatchApplyError("Update recovery journal has duplicate paths or no unique VERSION.txt entry.")
    return journal


def _entry_paths(install_dir: Path, backup_root: Path, entry: dict) -> tuple[Path, Path | None]:
    if not isinstance(entry, dict):
        raise PatchApplyError("Update recovery journal contains an invalid entry.")
    relative = entry.get("path")
    if relative == "VERSION.txt":
        safe_relative = relative
    else:
        try:
            safe_relative = _safe_relative_path(relative)
        except (PatchValidationError, TypeError) as exc:
            raise PatchApplyError("Update recovery journal contains an unsafe target path.") from exc
    if safe_relative != relative or not isinstance(entry.get("existed"), bool):
        raise PatchApplyError("Update recovery journal contains an invalid target record.")
    target = _target_path(install_dir, safe_relative)
    if not entry["existed"]:
        if entry.get("sha256") is not None:
            raise PatchApplyError("Invalid backup hash for a newly added file.")
        return target, None
    digest = entry.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise PatchApplyError("Update recovery journal contains an invalid backup hash.")
    backup = backup_root / "files" / safe_relative
    current = backup_root
    for component in ("files", *safe_relative.split("/")):
        current = current / component
        if _is_reparse_point(current):
            raise PatchApplyError("A recovery backup path is a symbolic link or junction.")
    resolved_root, resolved_backup = backup_root.resolve(), backup.resolve(strict=False)
    try:
        if os.path.commonpath((str(resolved_root), str(resolved_backup))) != str(resolved_root):
            raise PatchApplyError("A recovery backup path escapes its transaction directory.")
    except ValueError as exc:
        raise PatchApplyError("Invalid recovery backup path.") from exc
    return target, backup


def _rollback_transaction(install_dir: Path, backup_root: Path, journal: dict) -> None:
    journal["state"] = "rolling_back"
    _write_journal(backup_root, journal)

    # Validate every backup before changing any installation file.
    restore_plan = []
    seen = set()
    for entry in journal["entries"]:
        target, backup = _entry_paths(install_dir, backup_root, entry)
        key = str(target).casefold()
        if key in seen:
            raise PatchApplyError("Update recovery journal contains duplicate target paths.")
        seen.add(key)
        if backup is not None:
            if not backup.is_file():
                raise PatchApplyError(f"Required recovery backup is missing: {backup}")
            data = backup.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise PatchApplyError(f"Recovery backup hash mismatch: {backup}")
            restore_plan.append((target, data))
        else:
            restore_plan.append((target, None))

    for target, data in reversed(restore_plan):
        if data is None:
            if target.exists():
                if target.is_dir():
                    raise PatchApplyError(f"Cannot safely remove a directory created at update target: {target}")
                target.unlink()
        else:
            _write_atomic(target, data)

    journal["state"] = "rolled_back"
    _write_journal(backup_root, journal)


def recover_interrupted_updates(install_dir: Path) -> list[Path]:
    """Automatically roll back transactions interrupted after the applying state."""
    install_dir = install_dir.resolve()
    backup_parent = _backup_parent_path(install_dir)
    if not backup_parent.exists():
        return []
    recovered = []
    for backup_root in sorted(backup_parent.iterdir()):
        if _is_reparse_point(backup_root):
            raise PatchApplyError(f"An update backup entry is a symbolic link or junction: {backup_root}")
        if not backup_root.is_dir():
            continue
        marker_path = backup_root / TRANSACTION_MARKER_NAME
        journal_path = backup_root / JOURNAL_NAME
        has_marker = marker_path.exists() or _is_reparse_point(marker_path)
        has_journal = journal_path.exists() or _is_reparse_point(journal_path)
        if not has_marker and not has_journal:
            # Older backup folders and a crash immediately after mkdir have no transaction metadata.
            continue
        journal = _read_journal(backup_root)
        state = journal["state"]
        if state == "preparing":
            # No installation target is modified until the durable applying marker exists.
            journal["state"] = "rolled_back"
            _write_journal(backup_root, journal)
        elif state in ("applying", "rolling_back"):
            _rollback_transaction(install_dir, backup_root, journal)
            recovered.append(backup_root)
        # committed and rolled_back transactions are intentionally left untouched.
    return recovered


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

    # Never start a new transaction while an earlier one needs recovery.
    recover_interrupted_updates(install_dir)
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
        _write_atomic(
            backup_root / TRANSACTION_MARKER_NAME,
            f"BrNavi updater transaction schema {JOURNAL_SCHEMA}\\n".encode("ascii"),
        )

        entries = []
        for item, target in planned:
            entries.append({"path": item.path, "existed": target.is_file(), "sha256": None})
        entries.append({"path": "VERSION.txt", "existed": True, "sha256": None})
        journal = {
            "schema": JOURNAL_SCHEMA,
            "state": "preparing",
            "from_version": manifest.from_version,
            "to_version": manifest.to_version,
            "entries": entries,
        }
        _write_journal(backup_root, journal)

        try:
            # Back up every existing target before allowing any installation mutation.
            for entry in entries:
                if not entry["existed"]:
                    continue
                target = install_dir / entry["path"]
                backup = backup_root / "files" / entry["path"]
                data = target.read_bytes()
                _write_atomic(backup, data)
                entry["sha256"] = hashlib.sha256(data).hexdigest()
            _write_journal(backup_root, journal)
            journal["state"] = "applying"
            _write_journal(backup_root, journal)

            for item, target in planned:
                if item.action == "delete":
                    target.unlink()
                else:
                    _write_atomic(target, archive.read(f"payload/{item.path}"))

            version_target = install_dir / "VERSION.txt"
            _write_atomic(version_target, f"v{manifest.to_version}\n".encode("utf-8"))
            journal["state"] = "committed"
            _write_journal(backup_root, journal)
        except Exception as exc:
            rollback_error = None
            try:
                if journal["state"] in ("applying", "committed"):
                    _rollback_transaction(install_dir, backup_root, journal)
                else:
                    journal["state"] = "rolled_back"
                    _write_journal(backup_root, journal)
            except Exception as recovery_exc:
                rollback_error = recovery_exc
            detail = f"Patch failed; recovery state is stored in {backup_root}. Details: {exc}"
            if rollback_error is not None:
                detail += f" Automatic rollback could not complete: {rollback_error}"
            raise PatchApplyError(detail) from exc

    return manifest.to_version, backup_root
