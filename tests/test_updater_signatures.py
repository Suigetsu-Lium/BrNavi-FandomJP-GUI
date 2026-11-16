from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from updater.manifest import PatchValidationError, load_manifest
from updater.signatures import SignatureError, verify_manifest_signature


class SignatureTests(unittest.TestCase):
    def test_signed_manifest_is_accepted(self):
        private_key = Ed25519PrivateKey.generate()
        public_bytes = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        payload = b"new module"
        manifest = {
            "patch_format": 1,
            "application": "BrNavi Fandom JP",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "files": [{
                "path": "modules/new.py",
                "action": "add",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }],
        }
        manifest_bytes = (json.dumps(manifest, separators=(",", ":")) + "\n").encode("utf-8")
        signature = private_key.sign(manifest_bytes)
        with tempfile.TemporaryDirectory() as tmp:
            patch = Path(tmp) / "signed.zip"
            with zipfile.ZipFile(patch, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("patch_manifest.json", manifest_bytes)
                archive.writestr("patch_manifest.sig", signature)
                archive.writestr("payload/modules/new.py", payload)
            with zipfile.ZipFile(patch) as archive:
                loaded = load_manifest(
                    archive,
                    require_signature=True,
                    public_key_bytes=public_bytes,
                )
            self.assertEqual(loaded.to_version, "1.0.1")

    def test_tampered_manifest_signature_is_rejected(self):
        private_key = Ed25519PrivateKey.generate()
        public_bytes = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        signature = private_key.sign(b"original manifest")
        with self.assertRaises(SignatureError):
            verify_manifest_signature(signature, b"modified manifest", public_bytes)

    def test_unsigned_patch_is_rejected_in_production_mode(self):
        manifest = {
            "patch_format": 1,
            "application": "BrNavi Fandom JP",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "files": [{"path": "module.py", "action": "delete"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            patch = Path(tmp) / "unsigned.zip"
            with zipfile.ZipFile(patch, "w") as archive:
                archive.writestr("patch_manifest.json", json.dumps(manifest))
            with zipfile.ZipFile(patch) as archive:
                with self.assertRaises(SignatureError):
                    load_manifest(archive, require_signature=True)


if __name__ == "__main__":
    unittest.main()
