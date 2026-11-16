# Updater design

## Goals
- Sequential patches only: one exact source version and one target version.
- A standalone Updater.exe, separate from BrNavi-GUI.exe.
- Preserve the installed database and all user-generated Wiki data.
- Human downloads and selects a patch ZIP; no automatic download or silent application.
- No dependency on the BrNavi runtime or third-party Python packages at runtime.

## Repository layout
- updater/: updater source and patch validation/application.
- scripts/build_updater.bat: independent Updater.exe build.
- docs/updater_design.md: architecture and safety boundaries.
- Generated executables and patch ZIPs are not committed; release assets belong on GitHub Releases.

## Installed layout
Updater.exe is placed beside BrNavi-GUI.exe. Its installation root is the directory containing the executable. A patch ZIP contains patch_manifest.json and payload/<relative-path> entries.

## Application sequence
1. User closes BrNavi and launches Updater.exe.
2. User selects a downloaded patch ZIP.
3. Updater validates format, application ID, versions, paths, unique operations, payload presence, and SHA-256.
4. Installed VERSION.txt must exactly match the patch from_version.
5. Every touched file is backed up under update_backups/<timestamp>_<from>_to_<to>/.
6. Add/replace/delete operations are applied; replacement writes use a temporary file and atomic replace.
7. VERSION.txt is updated only after all payload operations succeed.
8. On failure, the updater attempts to restore touched files.

## Data protection
The manifest validator rejects paths rooted at database/, downloaded_fandom_html/, extracted_fandom_levels/, downloaded_images/, index_html/, parsed_index/, level_lists/, update_backups/, and user_data/. Absolute paths and traversal are rejected. SQLite files are never opened or migrated by the updater.

## Before public distribution
- Implement detached digital signature verification; SHA-256 alone is not publisher authentication.
- Implement deterministic patch creation between consecutive built release folders, including the first Updater.exe bootstrap.
- Test traversal, protected data paths, corrupt hashes, wrong source versions, add/replace/delete, and rollback.
- Verify the actual PyInstaller onedir layout and test on clean Windows 10/11 installations.
