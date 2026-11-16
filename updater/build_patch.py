"""Build deterministic sequential patch ZIPs from deployed PyInstaller folders."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .manifest import (
    MAX_FILE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_TOTAL_PAYLOAD_BYTES,
    _RESERVED_FILES,
    _RESERVED_ROOTS,
    normalize_version,
    sha256_bytes,
    is_protected_data_file,
)
from .signatures import sign_manifest

APP_ID = "BrNavi Fandom JP"
EXCLUDED_ROOTS = _RESERVED_ROOTS | {
    "cache", "exports", "backups", "logs", "user_data", "config", "settings",
    "__pycache__", ".git", ".venv", "build", "dist",
}
EXCLUDED_FILES = _RESERVED_FILES | {"updater.exe"}
ZIP_TIME = (2020, 1, 1, 0, 0, 0)


class PatchBuildError(ValueError):
    pass


def _read_version(folder: Path) -> str:
    version_file = folder / "VERSION.txt"
    if not version_file.is_file():
        raise PatchBuildError(f"VERSION.txt not found: {folder}")
    value = version_file.read_text(encoding="utf-8-sig").strip()
    if value.startswith("v"):
        value = value[1:]
    normalize_version(value)
    return value


def _iter_release_files(root: Path) -> dict[str, Path]:
    root = root.resolve()
    if not root.is_dir():
        raise PatchBuildError(f"Release directory does not exist: {root}")
    result: dict[str, Path] = {}
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs = []
        for dirname in dirs:
            candidate = current_path / dirname
            rel = candidate.relative_to(root).as_posix()
            if rel.split("/")[0].casefold() in {name.casefold() for name in EXCLUDED_ROOTS}:
                continue
            if candidate.is_symlink() or _is_reparse_point(candidate):
                raise PatchBuildError(f"Release contains a symlink/junction: {rel}")
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for filename in files:
            path = current_path / filename
            relative = path.relative_to(root).as_posix()
            pure = PurePosixPath(relative)
            if pure.parts[0].casefold() in {name.casefold() for name in EXCLUDED_ROOTS}:
                continue
            if (relative.casefold() in {name.casefold() for name in EXCLUDED_FILES}
                    or is_protected_data_file(relative)):
                continue
            if path.is_symlink() or _is_reparse_point(path):
                raise PatchBuildError(f"Release contains a symlink/junction: {relative}")
            if not path.is_file():
                continue
            key = relative.casefold()
            if key in result:
                raise PatchBuildError(f"Case-insensitive path collision: {relative}")
            result[key] = path
    return result


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _zip_write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def build_patch(
    from_dir: Path,
    to_dir: Path,
    output: Path,
    bootstrap_updater: Path | None = None,
    signing_key: Path | None = None,
) -> dict:
    from_dir, to_dir, output = from_dir.resolve(), to_dir.resolve(), output.resolve()
    from_version, to_version = _read_version(from_dir), _read_version(to_dir)
    if normalize_version(to_version) <= normalize_version(from_version):
        raise PatchBuildError("Target release must have a higher version than source release.")
    for label, folder in (("source", from_dir), ("target", to_dir)):
        if not (folder / "BrNavi-GUI.exe").is_file():
            raise PatchBuildError(f"{label} release is missing BrNavi-GUI.exe: {folder}")
    if output.is_relative_to(from_dir) or output.is_relative_to(to_dir):
        raise PatchBuildError("Output ZIP must be outside both release folders.")

    old_files = _iter_release_files(from_dir)
    new_files = _iter_release_files(to_dir)
    operations: list[dict] = []
    payloads: dict[str, bytes] = {}

    for key in sorted(new_files):
        relative = new_files[key].relative_to(to_dir).as_posix()
        new_data = new_files[key].read_bytes()
        old_path = old_files.get(key)
        if old_path is None:
            action = "add"
        elif old_path.read_bytes() == new_data:
            continue
        else:
            action = "replace"
        digest = sha256_bytes(new_data)
        operations.append({"path": relative, "action": action, "sha256": digest})
        payloads[f"payload/{relative}"] = new_data

    for key in sorted(old_files):
        if key not in new_files:
            relative = old_files[key].relative_to(from_dir).as_posix()
            operations.append({"path": relative, "action": "delete"})

    if bootstrap_updater is not None:
        bootstrap_updater = bootstrap_updater.resolve()
        if not bootstrap_updater.is_file() or bootstrap_updater.name.casefold() != "updater.exe":
            raise PatchBuildError("--bootstrap-updater must point to a built Updater.exe.")
        if "updater.exe" in old_files:
            raise PatchBuildError("Source release already contains Updater.exe; bootstrap is only for legacy installs.")
        if any(path.name.casefold() == "updater.exe" for path in from_dir.iterdir()):
            raise PatchBuildError("Source release already contains Updater.exe; bootstrap is only for legacy installs.")
        data = bootstrap_updater.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise PatchBuildError("Updater.exe exceeds the maximum supported patch file size.")
        operations.append({
            "path": "Updater.exe",
            "action": "add",
            "sha256": sha256_bytes(data),
        })
        payloads["payload/Updater.exe"] = data

    if not operations:
        raise PatchBuildError("The two releases contain no patchable differences.")

    # Stable operation order makes repeated builds byte-for-byte reproducible.
    operations.sort(key=lambda item: item["path"].casefold())
    manifest = {
        "patch_format": 1,
        "application": APP_ID,
        "from_version": from_version,
        "to_version": to_version,
        "files": operations,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise PatchBuildError("Generated patch manifest exceeds the supported size limit.")
    payload_total = 0
    for name, data in payloads.items():
        if len(data) > MAX_FILE_BYTES:
            raise PatchBuildError(f"Patch payload exceeds the per-file size limit: {name}")
        payload_total += len(data)
        if payload_total > MAX_TOTAL_PAYLOAD_BYTES:
            raise PatchBuildError("Generated patch payload exceeds the total size limit.")
    signature_bytes = sign_manifest(manifest_bytes, signing_key) if signing_key is not None else None
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(temp_name, "w") as archive:
            _zip_write(archive, "patch_manifest.json", manifest_bytes)
            if signature_bytes is not None:
                _zip_write(archive, "patch_manifest.sig", signature_bytes)
            for name in sorted(payloads, key=str.casefold):
                _zip_write(archive, name, payloads[name])
        os.replace(temp_name, output)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return {
        "from_version": from_version,
        "to_version": to_version,
        "operation_count": len(operations),
        "payload_bytes": sum(len(value) for value in payloads.values()),
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a BrNavi sequential patch ZIP.")
    parser.add_argument("--from-dir", required=True, type=Path, help="Previous complete deployed release folder")
    parser.add_argument("--to-dir", required=True, type=Path, help="New complete deployed release folder")
    parser.add_argument("--output", required=True, type=Path, help="Output patch ZIP path")
    parser.add_argument(
        "--bootstrap-updater", type=Path,
        help="Optional Updater.exe to add to a legacy installation that has no updater",
    )
    parser.add_argument(
        "--signing-key", required=True, type=Path,
        help="Offline Ed25519 PEM private key; keep it outside the repository",
    )
    args = parser.parse_args()
    try:
        result = build_patch(
            args.from_dir, args.to_dir, args.output,
            args.bootstrap_updater, args.signing_key,
        )
    except (OSError, PatchBuildError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"Patch created: v{result['from_version']} -> v{result['to_version']}\n"
        f"Operations: {result['operation_count']}\n"
        f"Payload: {result['payload_bytes']:,} bytes\n"
        f"Output: {result['output']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
