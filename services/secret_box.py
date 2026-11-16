"""Encryption at rest for two-factor secrets.

The authenticator secret must be readable by the server to check codes, so
it cannot be hashed. When ``TOTP_ENCRYPTION_KEY`` is set in the environment
(or the project .env file), secrets are stored encrypted with Fernet
(AES-128-CBC with an HMAC-SHA256 integrity check). The key lives outside the
database, so a copied database alone does not reveal the secrets.

Without the key, secrets are stored as before, so a deployment keeps working
until the key is added. Stored values carry a prefix, which lets plain and
encrypted secrets coexist while existing ones are being encrypted.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from utils.env_loader import get_env_value

TOTP_KEY_ENV = "TOTP_ENCRYPTION_KEY"
ENCRYPTED_PREFIX = "enc:v1:"


class SecretKeyError(RuntimeError):
    """A stored secret cannot be decrypted with the configured key."""


def generate_key() -> str:
    """Return a new random key for ``TOTP_ENCRYPTION_KEY``."""
    return Fernet.generate_key().decode("ascii")


def _fernet() -> Fernet | None:
    key = get_env_value(TOTP_KEY_ENV)
    if not key:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise SecretKeyError(
            f"{TOTP_KEY_ENV} is not a valid key; create one with "
            "tools/generate_totp_key.py"
        ) from exc


def encryption_enabled() -> bool:
    return _fernet() is not None


def is_encrypted(stored: str | None) -> bool:
    return bool(stored) and str(stored).startswith(ENCRYPTED_PREFIX)


def seal_secret(plain: str | None) -> str | None:
    """Encrypt a secret for storage when a key is configured."""
    if not plain:
        return plain
    fernet = _fernet()
    if fernet is None:
        return plain
    return ENCRYPTED_PREFIX + fernet.encrypt(plain.encode("utf-8")).decode("ascii")


def open_secret(stored: str | None) -> str:
    """Return the plain secret from a stored value (encrypted or not)."""
    if not stored:
        return ""
    if not is_encrypted(stored):
        return str(stored)
    fernet = _fernet()
    if fernet is None:
        raise SecretKeyError(
            f"Two-factor secrets are encrypted but {TOTP_KEY_ENV} is not set"
        )
    try:
        return fernet.decrypt(stored[len(ENCRYPTED_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretKeyError(
            f"A two-factor secret cannot be decrypted with {TOTP_KEY_ENV}; "
            "was the key changed?"
        ) from exc
