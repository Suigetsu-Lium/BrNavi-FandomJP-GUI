# BrNavi Fandom JP Updater

Standalone Windows updater for sequential ZIP patches. It is built separately from the GUI application and must run after BrNavi has been closed.

## Build

Run scripts\build_updater.bat from the repository root. The output is dist\Updater\Updater.exe. PyInstaller and a Python installation with Tkinter are required.

## Patch archive format (v1)

A patch ZIP contains patch_manifest.json and payload/<relative-path>. Example manifest:

~~~json
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
~~~

For add and replace, the archive must contain payload/<path>. SHA-256 covers the exact payload bytes. Delete operations have no payload or hash.

The updater validates the application ID, format, version sequence, unique safe paths, protected data directories, payload presence, size limits, and hashes. It requires installed VERSION.txt to match from_version. Backups are stored under update_backups/. A failed operation triggers best-effort rollback. The bootstrap patch may add Updater.exe once; the running updater cannot replace itself. To bootstrap an existing v1.0.0 installation, extract the bootstrap Updater.exe to a temporary folder and run it with --install-dir pointing to the BrNavi-GUI.exe folder. The patch then adds Updater.exe beside the main application.

## Security and limitations

- SQLite databases, Wiki HTML/images, indexes, CSVs, settings, and credentials are excluded from patch paths.
- SHA-256 detects corruption but does not authenticate the publisher. Do not distribute production patches until detached signature verification is implemented.
- Patch generation, bootstrap packaging, automated tests, and Windows end-to-end validation are follow-up tasks.
- The patch builder must compare complete deployed application folders, not only Python source files, because PyInstaller onedir updates may include executables and DLLs.
