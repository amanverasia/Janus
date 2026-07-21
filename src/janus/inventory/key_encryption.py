from __future__ import annotations

import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

ENCRYPTED_PREFIX = "enc:v1:"


def hash_upstream_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def encryption_enabled() -> bool:
    return bool(os.environ.get("INVENTORY_ENCRYPTION_KEY", "").strip())


def is_encrypted_value(stored: str) -> bool:
    return stored.startswith(ENCRYPTED_PREFIX)


def _fernet() -> Fernet | None:
    raw = os.environ.get("INVENTORY_ENCRYPTION_KEY", "").strip()
    if not raw:
        return None
    return Fernet(raw.encode())


def encrypt_key_value(plaintext: str) -> str:
    fernet = _fernet()
    if fernet is None:
        return plaintext
    token = fernet.encrypt(plaintext.encode()).decode()
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_key_value(stored: str) -> str:
    if not is_encrypted_value(stored):
        return stored
    fernet = _fernet()
    if fernet is None:
        raise RuntimeError("INVENTORY_ENCRYPTION_KEY is required to decrypt stored credentials")
    try:
        return fernet.decrypt(stored[len(ENCRYPTED_PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Failed to decrypt stored credential; check INVENTORY_ENCRYPTION_KEY"
        ) from exc


def generate_encryption_key() -> str:
    return Fernet.generate_key().decode()
