# BrNavi Fandom JP Updater

Standalone Windows updater for sequential ZIP patches. It is built separately from the GUI application and must run after BrNavi has been closed.

## Build the updater

Run `scripts\\build_updater.bat` from the repository root. The output is `dist\\Updater\\Updater.exe`. PyInstaller and a Python installation with Tkinter are required.

## Build a sequential patch

First build both complete application releases with `build_release.bat`. Pass the **inner deployed folders** (the folders containing `BrNavi-GUI.exe` and `VERSION.txt`), not the source tree or the outer staging folder.

Example for a normal update:

```bat
python -m updater.build_patch ^
  --from-dir "releases\\v1.1.0\\BrNavi-GUI" ^
  --to-dir "releases\\v1.0.1\\BrNavi-GUI" ^
  --output "artifacts\\BrNavi-v1.0.1-to-v1.1.0.patch.zip" ^
  --signing-key "E:\\SecureKeys\\BrNavi-update-private.pem"
```

For the initial v1.0.0 bootstrap, include the separately built updater:

```bat
python -m updater.build_patch ^
  --from-dir "releases\\v1.0.0\\BrNavi-GUI" ^
  --to-dir "releases\\v1.0.1\\BrNavi-GUI" ^
  --bootstrap-updater "dist\\Updater\\Updater.exe" ^
  --output "artifacts\\BrNavi-v1.0.0-to-v1.0.1-bootstrap.zip" ^
  --signing-key "E:\\SecureKeys\\BrNavi-update-private.pem"
```

The builder compares the full deployed PyInstaller onedir folders, including the executable and runtime DLLs. It creates add/replace/delete operations, sorts entries deterministically, and writes a reproducible ZIP. The CLI requires an Ed25519 private signing key and adds a detached signature over the exact manifest bytes. User data roots (SQLite database, downloaded Wiki HTML/images, indexes, settings, caches, logs and backups) and `VERSION.txt` are excluded. Database, SQLite sidecar, log, credential, secret, key, and PEM file types are also excluded regardless of their directory. `Updater.exe` is excluded from normal diffs; the bootstrap option can add it exactly once. Generated ZIPs and EXEs must be published as release assets, not committed to Git.

## Patch archive format (v1)

A signed patch ZIP contains `patch_manifest.json`, the 64-byte raw Ed25519 signature `patch_manifest.sig`, and only the payload files referenced by the manifest. The signature covers the exact UTF-8 bytes of `patch_manifest.json`:

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

For add and replace, the archive must contain `payload/<path>`. SHA-256 covers the exact payload bytes. Delete operations have no payload or hash. The updater verifies the signature before parsing or applying the manifest.

The updater validates the application ID, strict numeric version sequence, unique safe Windows paths, path-prefix collisions, protected data directories, ZIP entry names, payload presence, size limits, and hashes. It rejects symbolic-link/junction traversal in the installation path, including redirected `VERSION.txt` and backup locations. It requires installed `VERSION.txt` to match `from_version`. Backups are stored under `update_backups/`. Before changing any installed file, the updater writes a transaction marker and journal, then backs up every existing target (including `VERSION.txt`) and records each backup SHA-256. Only after all backups and the `applying` journal state have been written does it begin file operations; `VERSION.txt` is written last, followed by the durable `committed` journal state. If an operation fails, rollback is attempted immediately. If the updater or Windows is interrupted while the transaction is `applying` or `rolling_back`, the next Updater launch automatically resumes rollback before showing the update UI. A `preparing` transaction is safe to mark abandoned because no installed target has been modified yet. Missing, malformed, redirected, or hash-mismatched transaction metadata/backups block further updates rather than guessing. Committed and rolled-back backup folders are retained; they are not automatically deleted. File contents and journal files are flushed with `fsync` and replaced atomically; portable directory-entry durability across sudden power loss is subject to Windows/filesystem behavior.

The bootstrap patch may add `Updater.exe` once; the running updater cannot update itself. To bootstrap an existing v1.0.0 installation, extract the bootstrap `Updater.exe` to a temporary folder and run it with `--install-dir` pointing to the existing `BrNavi-GUI.exe` folder. The patch then adds `Updater.exe` beside the main application.

## Update signing key setup

Generate the key pair once on a trusted offline machine:

```bat
python scripts\\generate_update_signing_key.py --private-key "E:\\SecureKeys\\BrNavi-update-private.pem"
```

The script prints a Base64 public key. Copy it into `updater/trusted_public_key.py` as `TRUSTED_PUBLIC_KEY_B64`, then build and distribute a new trusted `Updater.exe`. Keep the private PEM outside the repository, restrict access, and maintain a secure backup. The builder requires `--signing-key`; the updater rejects unsigned or invalidly signed patches. A public-key change requires distributing a newly built updater through a trusted channel.

## Security status and limitations

- User databases, Wiki HTML/images, indexes, settings, credentials, and other runtime data are excluded by both the builder and the manifest validator.
- SHA-256 checks payload integrity; Ed25519 authenticates the manifest and therefore the payload hashes and version transition.
- The Ed25519 public key is pinned in `updater/trusted_public_key.py`. Signed patches are accepted only when their manifest signature matches this key. Keep the matching private PEM offline and outside the repository.
- Windows end-to-end testing is still required, including v1.0.0 bootstrap, sequential updates, process detection, and rollback.
