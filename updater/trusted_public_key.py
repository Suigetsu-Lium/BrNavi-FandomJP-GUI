"""Pinned Ed25519 public key used to authenticate update manifests.

Generate the release key offline, then replace the empty value with the
base64-encoded 32-byte public key. Never put the matching private key here.
"""
TRUSTED_PUBLIC_KEY_B64 = "fD9k6ImxX9AdVsGA6rjw4CJyHqtlOOasQUM86mvhIqE="
