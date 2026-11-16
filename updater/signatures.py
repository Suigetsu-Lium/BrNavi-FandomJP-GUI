"""Ed25519 signature helpers for patch manifest authenticity."""
from __future__ import annotations

import base64
import binascii
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .trusted_public_key import TRUSTED_PUBLIC_KEY_B64


class SignatureError(ValueError):
    """Raised when a patch signature cannot be trusted."""


def trusted_public_key_bytes() -> bytes:
    if not TRUSTED_PUBLIC_KEY_B64.strip():
        raise SignatureError(
            "No trusted update-signing public key is configured in this Updater build."
        )
    try:
        raw = base64.b64decode(TRUSTED_PUBLIC_KEY_B64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError("The embedded update-signing public key is invalid.") from exc
    if len(raw) != 32:
        raise SignatureError("The embedded Ed25519 public key must be exactly 32 bytes.")
    return raw


def verify_manifest_signature(signature: bytes, manifest_bytes: bytes, public_key_bytes: bytes) -> None:
    if len(signature) != 64:
        raise SignatureError("The patch manifest signature has an invalid length.")
    if len(public_key_bytes) != 32:
        raise SignatureError("The trusted Ed25519 public key has an invalid length.")
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, manifest_bytes)
    except InvalidSignature as exc:
        raise SignatureError("Patch signature verification failed. The patch may be modified or untrusted.") from exc
    except ValueError as exc:
        raise SignatureError("The trusted Ed25519 public key is invalid.") from exc


def sign_manifest(manifest_bytes: bytes, private_key_path: Path) -> bytes:
    try:
        key_data = private_key_path.read_bytes()
        key = serialization.load_pem_private_key(key_data, password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise SignatureError(f"Could not load Ed25519 signing key: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise SignatureError("Signing key must be an Ed25519 PEM private key.")
    return key.sign(manifest_bytes)
