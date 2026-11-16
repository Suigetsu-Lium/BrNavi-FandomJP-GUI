"""Validation helpers for BrNavi sequential patch manifests."""
from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

PATCH_FORMAT = 1
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_PAYLOAD_BYTES = 1024 * 1024 * 1024
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_RESERVED_ROOTS = {
    "database", "downloaded_fandom_html", "extracted_fandom_levels",
    "downloaded_images", "index_html", "parsed_index", "level_lists",
    "update_backups", "user_data", "config", "settings", "logs",
    "cache", "exports", "backups",
}
_RESERVED_FILES = {
    "version.txt", ".env", "config.json", "settings.json",
    "api_keys.json", "credentials.json", "secrets.json",
}
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class PatchValidationError(ValueError):
    """Raised when a patch archive is invalid or unsafe."""


@dataclass(frozen=True)
class PatchFile:
    path: str
    action: str
    sha256: str | None


@dataclass(frozen=True)
class PatchManifest:
    from_version: str
    to_version: str
    files: tuple[PatchFile, ...]
    raw: dict[str, Any]


def normalize_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(str(value).strip())
    if not match:
        raise PatchValidationError(f"Unsupported version format: {value!r}")
    return tuple(int(part) for part in match.groups())


def _safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PatchValidationError("Invalid patch file path.")
    raw_parts = value.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        raise PatchValidationError(f"Unsafe patch path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise PatchValidationError(f"Unsafe patch path: {value!r}")
    if not path.parts or path.parts[0].lower() in _RESERVED_ROOTS:
        raise PatchValidationError(f"Patch is not allowed to modify user data: {value!r}")
    if path.as_posix().lower() in _RESERVED_FILES:
        raise PatchValidationError(f"Patch targets a protected application file: {value!r}")
    for part in path.parts:
        stem = part.rstrip(" .").split(".")[0].lower()
        if (part.endswith((" ", ".")) or any(ord(ch) < 32 or ch in '<>:"|?*' for ch in part)
                or stem in _WINDOWS_RESERVED):
            raise PatchValidationError(f"Invalid Windows path component: {part!r}")
    if path.parts[0].lower() in {".git", ".venv", "build", "dist"}:
        raise PatchValidationError(f"Patch targets a protected path: {value!r}")
    return path.as_posix()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PatchValidationError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_manifest(archive: zipfile.ZipFile) -> PatchManifest:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise PatchValidationError("Patch archive contains duplicate ZIP entries.")
    folded_names = [name.casefold() for name in names]
    if len(folded_names) != len(set(folded_names)):
        raise PatchValidationError("Patch archive contains case-insensitive duplicate entries.")
    if any(info.is_dir() or _zip_entry_is_symlink(info) for info in infos):
        raise PatchValidationError("Patch archive contains an unsupported directory or link entry.")
    try:
        manifest_info = archive.getinfo("patch_manifest.json")
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise PatchValidationError("Patch manifest is too large.")
        raw = json.loads(
            archive.read(manifest_info).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PatchValidationError("patch_manifest.json is missing or invalid.") from exc
    if (not isinstance(raw, dict)
            or type(raw.get("patch_format")) is not int
            or raw.get("patch_format") != PATCH_FORMAT):
        raise PatchValidationError("Unsupported patch format.")
    if raw.get("application") != "BrNavi Fandom JP":
        raise PatchValidationError("This patch is not for BrNavi Fandom JP.")

    from_version = str(raw.get("from_version", "")).strip()
    to_version = str(raw.get("to_version", "")).strip()
    from_tuple, to_tuple = normalize_version(from_version), normalize_version(to_version)
    if to_tuple <= from_tuple:
        raise PatchValidationError("Target version must be newer than source version.")

    entries = raw.get("files")
    if not isinstance(entries, list) or not entries:
        raise PatchValidationError("Patch contains no file operations.")

    files: list[PatchFile] = []
    seen: dict[str, str] = {}
    payload_names: set[str] = set()
    payload_total = 0
    archive_names = set(names)

    for entry in entries:
        if not isinstance(entry, dict):
            raise PatchValidationError("Invalid file operation.")
        path = _safe_relative_path(entry.get("path"))
        path_key = path.casefold()
        if path_key in seen:
            raise PatchValidationError(f"Duplicate patch path: {path}")
        # A file cannot also be an ancestor directory of another patched file.
        for existing_key, existing_path in seen.items():
            if path_key.startswith(existing_key + "/") or existing_key.startswith(path_key + "/"):
                raise PatchValidationError(
                    f"Conflicting file and directory paths: {existing_path!r}, {path!r}"
                )
        seen[path_key] = path
        action, digest = entry.get("action"), entry.get("sha256")
        if action not in ("add", "replace", "delete"):
            raise PatchValidationError(f"Unsupported action for {path}.")
        if path.casefold() == "updater.exe" and action != "add":
            raise PatchValidationError(
                "Updater.exe may only be installed by the bootstrap patch; it cannot update itself."
            )
        if action == "delete":
            if digest not in (None, ""):
                raise PatchValidationError("Delete operations must not specify sha256.")
        else:
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise PatchValidationError(f"Invalid SHA-256 for {path}.")
            payload_name = f"payload/{path}"
            if payload_name not in archive_names:
                raise PatchValidationError(f"Missing payload file: {payload_name}")
            payload_info = archive.getinfo(payload_name)
            if payload_info.file_size > MAX_FILE_BYTES:
                raise PatchValidationError(f"Payload file is too large: {path}")
            payload_total += payload_info.file_size
            if payload_total > MAX_TOTAL_PAYLOAD_BYTES:
                raise PatchValidationError("Total patch payload is too large.")
            if sha256_bytes(archive.read(payload_info)) != digest:
                raise PatchValidationError(f"Payload hash mismatch: {path}")
            payload_names.add(payload_name)
        files.append(PatchFile(path, action, digest or None))

    expected_entries = {"patch_manifest.json"} | payload_names
    unexpected = archive_names - expected_entries
    missing = expected_entries - archive_names
    if unexpected or missing:
        raise PatchValidationError(
            f"Patch archive contents do not match the manifest (unexpected={sorted(unexpected)}, missing={sorted(missing)})."
        )
    return PatchManifest(from_version, to_version, tuple(files), raw)
