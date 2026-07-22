from __future__ import annotations

import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

ENCRYPTED_PREFIX = "enc:v1:"


class CredentialEncryptionError(RuntimeError):
    """Raised when credential encryption configuration cannot be used."""


class CredentialDecryptionError(CredentialEncryptionError):
    """Raised when an encrypted stored credential cannot be decrypted."""


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
    try:
        fernet = _fernet()
    except ValueError as exc:
        raise CredentialEncryptionError(
            "INVENTORY_ENCRYPTION_KEY is invalid; expected a Fernet key"
        ) from exc
    if fernet is None:
        return plaintext
    token = fernet.encrypt(plaintext.encode()).decode()
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_key_value(stored: str) -> str:
    if not is_encrypted_value(stored):
        return stored
    try:
        fernet = _fernet()
    except ValueError as exc:
        raise CredentialDecryptionError(
            "INVENTORY_ENCRYPTION_KEY is invalid; expected a Fernet key"
        ) from exc
    if fernet is None:
        raise CredentialDecryptionError(
            "INVENTORY_ENCRYPTION_KEY is required to decrypt stored credentials"
        )
    try:
        return fernet.decrypt(stored[len(ENCRYPTED_PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise CredentialDecryptionError(
            "Failed to decrypt stored credential; check INVENTORY_ENCRYPTION_KEY"
        ) from exc


def generate_encryption_key() -> str:
    return Fernet.generate_key().decode()
