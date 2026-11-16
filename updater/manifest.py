"""Validation helpers for BrNavi sequential patch manifests."""
from __future__ import annotations
import hashlib, json, re, zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

PATCH_FORMAT = 1
_VERSION_RE = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
_RESERVED_ROOTS = {"database", "downloaded_fandom_html", "extracted_fandom_levels",
    "downloaded_images", "index_html", "parsed_index", "level_lists", "update_backups", "user_data"}

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
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise PatchValidationError(f"Unsafe patch path: {value!r}")
    if not path.parts or path.parts[0].lower() in _RESERVED_ROOTS:
        raise PatchValidationError(f"Patch is not allowed to modify user data: {value!r}")
    if path.parts[0].lower() in {".git", ".venv", "build", "dist"}:
        raise PatchValidationError(f"Patch targets a protected path: {value!r}")
    return path.as_posix()

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def load_manifest(archive: zipfile.ZipFile) -> PatchManifest:
    try:
        raw = json.loads(archive.read("patch_manifest.json").decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PatchValidationError("patch_manifest.json is missing or invalid.") from exc
    if not isinstance(raw, dict) or raw.get("patch_format") != PATCH_FORMAT:
        raise PatchValidationError("Unsupported patch format.")
    if raw.get("application") != "BrNavi Fandom JP":
        raise PatchValidationError("This patch is not for BrNavi Fandom JP.")
    from_version, to_version = str(raw.get("from_version", "")), str(raw.get("to_version", ""))
    normalize_version(from_version); normalize_version(to_version)
    if normalize_version(to_version) <= normalize_version(from_version):
        raise PatchValidationError("Target version must be newer than source version.")
    entries = raw.get("files")
    if not isinstance(entries, list) or not entries:
        raise PatchValidationError("Patch contains no file operations.")
    files, seen = [], set()
    archive_names = set(archive.namelist())
    for entry in entries:
        if not isinstance(entry, dict):
            raise PatchValidationError("Invalid file operation.")
        path = _safe_relative_path(entry.get("path"))
        if path in seen:
            raise PatchValidationError(f"Duplicate patch path: {path}")
        seen.add(path)
        action, digest = entry.get("action"), entry.get("sha256")
        if action not in ("add", "replace", "delete"):
            raise PatchValidationError(f"Unsupported action for {path}.")
        if action == "delete":
            if digest not in (None, ""):
                raise PatchValidationError("Delete operations must not specify sha256.")
        else:
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise PatchValidationError(f"Invalid SHA-256 for {path}.")
            payload_name = f"payload/{path}"
            if payload_name not in archive_names:
                raise PatchValidationError(f"Missing payload file: {payload_name}")
            if sha256_bytes(archive.read(payload_name)) != digest:
                raise PatchValidationError(f"Payload hash mismatch: {path}")
        files.append(PatchFile(path, action, digest or None))
    return PatchManifest(from_version, to_version, tuple(files), raw)
