# BrNavi Fandom JP Updater

Standalone Windows updater for sequential ZIP patches. It is built separately from the GUI application and must run after BrNavi has been closed.

## Build the updater

Run `scripts\\build_updater.bat` from the repository root. The output is `dist\\Updater\\Updater.exe`. PyInstaller and a Python installation with Tkinter are required.

## Build a sequential patch

First build both complete application releases with `build_release.bat`. Pass the **inner deployed folders** (the folders containing `BrNavi-GUI.exe` and `VERSION.txt`), not the source tree or the outer staging folder.

Example for a normal update:

```bat
python -m updater.build_patch ^
  --from-dir "releases\\v1.0.0\\BrNavi-GUI" ^
  --to-dir "releases\\v1.0.1\\BrNavi-GUI" ^
  --output "artifacts\\BrNavi-v1.0.0-to-v1.0.1.zip"
```

For the initial v1.0.0 bootstrap, include the separately built updater:

```bat
python -m updater.build_patch ^
  --from-dir "releases\\v1.0.0\\BrNavi-GUI" ^
  --to-dir "releases\\v1.0.1\\BrNavi-GUI" ^
  --bootstrap-updater "dist\\Updater\\Updater.exe" ^
  --output "artifacts\\BrNavi-v1.0.0-to-v1.0.1-bootstrap.zip"
```

The builder compares the full deployed PyInstaller onedir folders, including the executable and runtime DLLs. It creates add/replace/delete operations, sorts entries deterministically, and writes a reproducible ZIP. User data roots (SQLite database, downloaded Wiki HTML/images, indexes, settings, caches, logs and backups) and `VERSION.txt` are excluded. `Updater.exe` is excluded from normal diffs; the bootstrap option can add it exactly once. Generated ZIPs and EXEs must be published as release assets, not committed to Git.

## Patch archive format (v1)

A patch ZIP contains `patch_manifest.json` and only the payload files referenced by that manifest:

```json
{
  "patch_format": 1,
  "application": "BrNavi Fandom JP",
  "from_version": "1.0.0",
  "to_version": "1.0.1",
  "files": [
    {"path": "modules/example.py", "action": "replace", "sha256": "<64 lowercase hex characters>"},
    {"path": "modules/new_module.py", "action": "add", "sha256": "<64 lowercase hex characters>"},
    {"path": "old_module.py", "action": "delete"}
  ]
}
```

For add and replace, the archive must contain `payload/<path>`. SHA-256 covers the exact payload bytes. Delete operations have no payload or hash.

The updater validates the application ID, strict numeric version sequence, unique safe Windows paths, path-prefix collisions, protected data directories, ZIP entry names, payload presence, size limits, and hashes. It rejects symbolic-link/junction traversal in the installation path. It requires installed `VERSION.txt` to match `from_version`. Backups are stored under `update_backups/`. The updater writes `VERSION.txt` only after all file operations succeed and attempts rollback if an operation fails.

The bootstrap patch may add `Updater.exe` once; the running updater cannot update itself. To bootstrap an existing v1.0.0 installation, extract the bootstrap `Updater.exe` to a temporary folder and run it with `--install-dir` pointing to the existing `BrNavi-GUI.exe` folder. The patch then adds `Updater.exe` beside the main application.

## Security status and limitations

- User databases, Wiki HTML/images, indexes, settings, credentials, and other runtime data are excluded by both the builder and the manifest validator.
- SHA-256 detects accidental corruption but does **not** authenticate the publisher. Do not distribute production patches until detached signature verification is implemented.
- Windows end-to-end testing is still required, including v1.0.0 bootstrap, sequential updates, process detection, and rollback.
