"""Generate an offline Ed25519 update-signing key pair.

Keep the private key outside the repository and never distribute it.
Copy the printed public-key Base64 value into updater/trusted_public_key.py.
"""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate BrNavi update-signing keys.")
    parser.add_argument("--private-key", required=True, type=Path, help="Private PEM output path outside the repository")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    private_path = args.private_key.expanduser().resolve()
    if private_path == repo_root or repo_root in private_path.parents:
        parser.error("Private key must be stored outside the repository.")
    if private_path.exists():
        parser.error(f"Refusing to overwrite existing key: {private_path}")

    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    try:
        with private_path.open("xb") as stream:
            stream.write(private_pem)
        if os.name != "nt":
            private_path.chmod(0o600)
    except Exception:
        try:
            private_path.unlink()
        except OSError:
            pass
        raise

    print("Private key created. Keep it offline and do not commit or distribute it.")
    print(f"Private key: {private_path}")
    print("Public key (Base64) — copy this into updater/trusted_public_key.py:")
    print(base64.b64encode(public_raw).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
