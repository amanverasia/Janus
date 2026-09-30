from __future__ import annotations

import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

ENCRYPTED_PREFIX = "enc:v1:"
CURRENT_KEY_ENV = "INVENTORY_ENCRYPTION_KEY"
PREVIOUS_KEY_ENV = "INVENTORY_ENCRYPTION_PREVIOUS_KEY"
INSECURE_DEV_KEY_OPT_IN_ENV = "JANUS_ALLOW_INSECURE_DEV_KEY"

logger = logging.getLogger(__name__)


class CredentialEncryptionError(RuntimeError):
    """Raised when credential encryption configuration cannot be used."""


class CredentialDecryptionError(CredentialEncryptionError):
    """Raised when an encrypted stored credential cannot be decrypted."""


def hash_upstream_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def encryption_enabled() -> bool:
    return bool(os.environ.get(CURRENT_KEY_ENV, "").strip())


def insecure_dev_key_allowed() -> bool:
    return os.environ.get(INSECURE_DEV_KEY_OPT_IN_ENV, "").strip() == "1"


def is_encrypted_value(stored: str) -> bool:
    return stored.startswith(ENCRYPTED_PREFIX)


def _fernet() -> Fernet | None:
    raw = os.environ.get(CURRENT_KEY_ENV, "").strip()
    if not raw:
        return None
    return Fernet(raw.encode())


def previous_key_configured() -> bool:
    return bool(os.environ.get(PREVIOUS_KEY_ENV, "").strip())


def _previous_fernet() -> Fernet | None:
    raw = os.environ.get(PREVIOUS_KEY_ENV, "").strip()
    if not raw:
        return None
    return Fernet(raw.encode())


def credential_is_decryptable(stored: str) -> bool:
    if not is_encrypted_value(stored):
        return True
    try:
        fernet = _fernet()
    except ValueError:
        return False
    if fernet is None:
        return False
    try:
        fernet.decrypt(stored[len(ENCRYPTED_PREFIX) :].encode())
    except InvalidToken:
        return False
    return True


def decrypt_with_previous_key(stored: str) -> str | None:
    if not is_encrypted_value(stored):
        return None
    try:
        fernet = _previous_fernet()
    except ValueError:
        logger.error("%s is invalid; expected a Fernet key", PREVIOUS_KEY_ENV)
        return None
    if fernet is None:
        return None
    try:
        return fernet.decrypt(stored[len(ENCRYPTED_PREFIX) :].encode()).decode()
    except InvalidToken:
        return None


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
