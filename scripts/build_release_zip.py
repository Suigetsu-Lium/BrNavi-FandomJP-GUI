"""Build the complete Windows GUI release ZIP from the staged onedir folder.

This is intentionally separate from updater.build_patch: the output is a full
installation archive, not a signed sequential update patch.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ZIP_TIME = (2020, 1, 1, 0, 0, 0)
ARCHIVE_ROOT = "BrNavi-GUI"

# These are runtime data locations created empty by build_release.bat. Keep
# their directory entries, but never descend into them or package their files.
EMPTY_DATA_ROOTS = {
    "database",
    "downloaded_fandom_html",
    "downloaded_images",
    "extracted_fandom_levels",
    "index_html",
    "level_lists",
    "parsed_index",
}
EMPTY_DATA_CHILDREN = {"database/ai"}

# Development/runtime directories are not part of a deployed installation.
EXCLUDED_DIRECTORIES = {
    ".git", ".ssh", ".venv", "__pycache__", "backups", "build", "cache", "caches",
    "config", "credentials", "database", "downloaded_fandom_html", "downloaded_images",
    "exports", "extracted_fandom_levels", "index_html", "keys", "level_lists", "logs",
    "parsed_index", "private", "private_keys", "secret", "secrets", "settings", "ssh",
    "tokens", "update_backups", "user_data",
}
EXCLUDED_SUFFIXES = {
    ".bak", ".csv", ".db", ".key", ".log", ".pem", ".p12", ".pfx", ".ppk",
    ".pyc", ".pyo", ".shm", ".sqlite", ".sqlite3", ".swp", ".tmp", ".wal",
}
EXCLUDED_EXACT_NAMES = {
    ".env", ".env.local", ".env.production", "app_settings.json",
    "credentials", "credentials.json", "secrets.json",
}
SENSITIVE_NAME_PATTERNS = (
    re.compile(r"(^|[._-])secret([._-]|$)", re.IGNORECASE),
    re.compile(r"(^|[._-])credential(s)?([._-]|$)", re.IGNORECASE),
    re.compile(r"(^|[._-])token([._-]|$)", re.IGNORECASE),
    re.compile(r"(^|[._-])api[_-]?key([._-]|$)", re.IGNORECASE),
    re.compile(r"private.*key|key.*private", re.IGNORECASE),
)


class ReleaseZipError(ValueError):
    """Raised when the staged release is unsafe or incomplete."""


def _is_reparse_point(path: Path) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _is_sensitive_file(relative: PurePosixPath) -> bool:
    name = relative.name.casefold()
    if name in EXCLUDED_EXACT_NAMES or name.startswith(".env.") or name.startswith(".env_"):
        return True
    suffix = relative.suffix.casefold()
    if suffix in EXCLUDED_SUFFIXES:
        return True
    if "settings" in name and suffix in {".json", ".ini", ".toml"}:
        return True
    if name.endswith(("-wal", "-shm", "-journal")):
        return True
    if name in {"id_rsa", "id_ed25519", "id_ecdsa"}:
        return True
    return any(pattern.search(relative.name) for pattern in SENSITIVE_NAME_PATTERNS)


def _zip_info(name: str, *, is_directory: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = ((stat.S_IFDIR | 0o755) if is_directory else (stat.S_IFREG | 0o644)) << 16
    if is_directory:
        info.external_attr |= 0x10
    return info


def _collect_entries(source: Path) -> list[tuple[str, Path | None]]:
    entries: list[tuple[str, Path | None]] = []
    seen: set[str] = set()
    empty_data_roots = {name.casefold() for name in EMPTY_DATA_ROOTS}
    empty_data_children = {name.casefold() for name in EMPTY_DATA_CHILDREN}
    excluded_dirs = {name.casefold() for name in EXCLUDED_DIRECTORIES}

    def add_directory(relative: str) -> None:
        archive_name = f"{ARCHIVE_ROOT}/{relative.strip('/')}/"
        key = archive_name.casefold()
        if key not in seen:
            seen.add(key)
            entries.append((archive_name, None))

    def add_file(relative: str, path: Path) -> None:
        archive_name = f"{ARCHIVE_ROOT}/{relative}"
        key = archive_name.casefold()
        if key in seen:
            raise ReleaseZipError(f"Case-insensitive archive path collision: {relative}")
        seen.add(key)
        entries.append((archive_name, path))

    # Preserve the expected empty runtime data layout. Do not inspect or walk
    # their contents: even if real user data exists, only these directory
    # entries are emitted.
    for name in sorted(EMPTY_DATA_ROOTS, key=str.casefold):
        candidate = source / name
        if candidate.exists():
            if not candidate.is_dir() or _is_reparse_point(candidate):
                raise ReleaseZipError(f"Expected a normal data directory: {candidate}")
        add_directory(name)
    database_ai = source / "database" / "ai"
    if database_ai.exists():
        if not database_ai.is_dir() or _is_reparse_point(database_ai):
            raise ReleaseZipError(f"Expected a normal data directory: {database_ai}")
    add_directory("database/ai")

    for current, dirs, files in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(source).as_posix()
        if relative_current == ".":
            relative_current = ""

        kept_dirs: list[str] = []
        for dirname in sorted(dirs, key=str.casefold):
            candidate = current_path / dirname
            if _is_reparse_point(candidate):
                rel = (PurePosixPath(relative_current) / dirname).as_posix()
                raise ReleaseZipError(f"Symbolic link/junction is not allowed: {rel}")
            rel = (PurePosixPath(relative_current) / dirname).as_posix()
            rel_key = rel.casefold()

            if rel_key in empty_data_roots or rel_key in empty_data_children:
                # Its directory entry was added above; never traverse content.
                continue
            if dirname.casefold() in excluded_dirs:
                continue
            if any(part.casefold() in excluded_dirs for part in PurePosixPath(rel).parts):
                continue
            kept_dirs.append(dirname)
            # Empty ordinary directories are added after walking.
        dirs[:] = kept_dirs

        for filename in sorted(files, key=str.casefold):
            path = current_path / filename
            rel = (PurePosixPath(relative_current) / filename).as_posix()
            if _is_reparse_point(path):
                raise ReleaseZipError(f"Symbolic link/junction is not allowed: {rel}")
            pure = PurePosixPath(rel)
            if any(part.casefold() in excluded_dirs for part in pure.parts):
                continue
            if _is_sensitive_file(pure):
                continue
            if not path.is_file():
                continue
            add_file(rel, path)

    # Include empty, non-excluded application directories. Data directories
    # already have explicit entries above.
    for current, dirs, files in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(source).as_posix()
        if relative_current == ".":
            relative_current = ""
        dirs[:] = [
            name for name in dirs
            if name.casefold() not in excluded_dirs
            and (PurePosixPath(relative_current) / name).as_posix().casefold() not in empty_data_roots
            and (PurePosixPath(relative_current) / name).as_posix().casefold() not in empty_data_children
        ]
        if not dirs and not files and relative_current:
            if not any(part.casefold() in excluded_dirs for part in PurePosixPath(relative_current).parts):
                add_directory(relative_current)

    entries.sort(key=lambda item: item[0].casefold())
    return entries


def build_release_zip(source: Path, output: Path, version: str) -> dict[str, int | str]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseZipError(f"Invalid application version: {version!r}")

    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ReleaseZipError(f"Release source is not a directory: {source}")
    output = output.resolve()
    if output.is_relative_to(source):
        raise ReleaseZipError("Output ZIP must be outside the staged release directory.")

    version_file = source / "VERSION.txt"
    if not version_file.is_file() or version_file.read_text(encoding="utf-8-sig").strip() != f"v{version}":
        raise ReleaseZipError(f"VERSION.txt must contain exactly v{version}.")
    for required in (
        "BrNavi-GUI.exe", "Updater.exe", "README.md", "LICENSE",
        "THIRD_PARTY_NOTICES.md", "docs/architecture.md", "docs/route-data-v2-design.md",
    ):
        if not (source / required).is_file():
            raise ReleaseZipError(f"Required release file is missing: {required}")

    entries = _collect_entries(source)
    included = {name.removeprefix(f"{ARCHIVE_ROOT}/") for name, path in entries if path is not None}
    required_files = {
        "BrNavi-GUI.exe", "Updater.exe", "VERSION.txt", "README.md", "LICENSE",
        "THIRD_PARTY_NOTICES.md", "docs/architecture.md", "docs/route-data-v2-design.md",
    }
    missing = sorted(required_files - included)
    if missing:
        raise ReleaseZipError("Required release files were excluded or missing: " + ", ".join(missing))
    required_directories = {f"{ARCHIVE_ROOT}/{name}/" for name in EMPTY_DATA_ROOTS}
    required_directories.add(f"{ARCHIVE_ROOT}/database/ai/")
    actual_names = {name for name, _ in entries}
    missing_directories = sorted(required_directories - actual_names)
    if missing_directories:
        raise ReleaseZipError("Required empty data directories are missing: " + ", ".join(missing_directories))

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
    )
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            # Explicit top-level folder entry makes the archive layout clear.
            archive.writestr(_zip_info(f"{ARCHIVE_ROOT}/", is_directory=True), b"")
            file_count = 0
            directory_count = 1
            for archive_name, path in entries:
                if path is None:
                    archive.writestr(_zip_info(archive_name, is_directory=True), b"")
                    directory_count += 1
                else:
                    archive.writestr(_zip_info(archive_name), path.read_bytes())
                    file_count += 1

        # Verify archive structure and CRCs before publishing it.
        with zipfile.ZipFile(temporary_name, "r") as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ReleaseZipError(f"ZIP integrity check failed at: {bad_member}")
            names = archive.namelist()
            if not names or any("\\" in name for name in names):
                raise ReleaseZipError("ZIP contains an invalid path separator or no entries.")
            if any(not name.startswith(f"{ARCHIVE_ROOT}/") for name in names):
                raise ReleaseZipError("ZIP contains an entry outside the expected top-level folder.")

        os.replace(temporary_name, output)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        try:
            output.unlink()
        except OSError:
            pass
        raise

    return {
        "output": str(output),
        "files": file_count,
        "directories": directory_count,
        "bytes": output.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the complete BrNavi GUI release ZIP.")
    parser.add_argument("--source", required=True, type=Path, help="Completed staged BrNavi-GUI folder")
    parser.add_argument("--output", required=True, type=Path, help="Output full-release ZIP")
    parser.add_argument("--version", required=True, help="Expected numeric application version, e.g. 1.2.0")
    args = parser.parse_args()
    try:
        result = build_release_zip(args.source, args.output, args.version)
    except (OSError, ReleaseZipError, ValueError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(
        "Release ZIP created successfully.\n"
        f"Output: {result['output']}\n"
        f"Files: {result['files']}\n"
        f"Directories: {result['directories']}\n"
        f"Size: {result['bytes']:,} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
