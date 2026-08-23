"""
Persistent, encrypted-at-rest signing key storage -- the production-
appropriate alternative to generating a fresh in-memory keypair on
every process start, which invalidated every token issued before a
restart. This is not a full KMS/HSM/Vault integration -- that needs
cloud infrastructure this project doesn't have, and is genuinely days
of separate work, not something to fake. This is the honest middle
ground for a single-node service: the private key is serialized to
disk, encrypted with a passphrase supplied via an environment
variable rather than stored in plaintext, and reused across restarts.
"""

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class KeyStoreError(RuntimeError):
    """Raised when the passphrase is missing, or the key on disk can't be decrypted with the one given."""


def _passphrase():
    passphrase = os.environ.get("WARRANT_KEY_PASSPHRASE")
    if not passphrase:
        raise KeyStoreError(
            "WARRANT_KEY_PASSPHRASE is not set -- required to encrypt/decrypt the persistent signing key"
        )
    return passphrase.encode("utf-8")


def load_or_create_keypair(key_path):
    """Loads the persisted, encrypted private key at `key_path` if it
    exists; otherwise generates a new Ed25519 keypair and persists it
    encrypted. Returns `(private_key, public_key)`. Restarting the
    service reuses the same key, so tokens issued before a restart
    stay verifiable after one -- the whole point of this module."""
    key_path = Path(key_path)
    passphrase = _passphrase()

    if key_path.exists():
        try:
            private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=passphrase)
        except (TypeError, ValueError) as exc:
            raise KeyStoreError(f"could not decrypt the key at {key_path} -- wrong passphrase?") from exc
    else:
        private_key = Ed25519PrivateKey.generate()
        pem_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
        )
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(pem_bytes)
        try:
            os.chmod(key_path, 0o600)  # best-effort -- a no-op on filesystems that don't support Unix permissions
        except OSError:
            pass

    return private_key, private_key.public_key()
