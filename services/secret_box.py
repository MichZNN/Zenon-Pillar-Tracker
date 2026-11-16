"""Encryption at rest for two-factor secrets.

The authenticator secret must be readable by the server to check codes, so
it cannot be hashed. When ``TOTP_ENCRYPTION_KEY`` is set in the environment
(or the project .env file), secrets are stored encrypted with Fernet
(AES-128-CBC with an HMAC-SHA256 integrity check). The key lives outside the
database, so a copied database alone does not reveal the secrets.

Without the key, secrets are stored as before, so a deployment keeps working
until the key is added. Stored values carry a prefix, which lets plain and
encrypted secrets coexist while existing ones are being encrypted.

Key rotation: ``TOTP_ENCRYPTION_KEY`` may hold several comma-separated keys.
New values are encrypted with the first key; any key can decrypt, and the
web app re-encrypts older values with the first key at startup.
"""

from __future__ import annotations

from threading import Lock

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from utils.env_loader import get_env_value

TOTP_KEY_ENV = "TOTP_ENCRYPTION_KEY"
ENCRYPTED_PREFIX = "enc:v1:"

_cache_lock = Lock()
_cache: tuple[str, MultiFernet | None] | None = None


class SecretKeyError(RuntimeError):
    """The key is invalid, missing, or does not decrypt a stored secret."""


def generate_key() -> str:
    """Return a new random key for ``TOTP_ENCRYPTION_KEY``."""
    return Fernet.generate_key().decode("ascii")


def _configured_keys() -> list[str]:
    try:
        value = get_env_value(TOTP_KEY_ENV)
    except ValueError as exc:
        # A malformed .env line; do not echo it back to a request.
        raise SecretKeyError("The .env file could not be read") from exc
    return [key.strip() for key in value.split(",") if key.strip()]


def _fernet() -> MultiFernet | None:
    """Return the configured keys as one MultiFernet, or None without a key."""
    global _cache
    keys = _configured_keys()
    cache_key = ",".join(keys)
    with _cache_lock:
        if _cache is not None and _cache[0] == cache_key:
            return _cache[1]
    if not keys:
        fernet = None
    else:
        try:
            fernet = MultiFernet([Fernet(key.encode("ascii")) for key in keys])
        except (ValueError, UnicodeEncodeError) as exc:
            raise SecretKeyError(
                f"{TOTP_KEY_ENV} is not a valid key; create one with "
                "tools/generate_totp_key.py"
            ) from exc
    with _cache_lock:
        _cache = (cache_key, fernet)
    return fernet


def encryption_enabled() -> bool:
    return _fernet() is not None


def key_count() -> int:
    return len(_configured_keys())


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
        return fernet.decrypt(stored[len(ENCRYPTED_PREFIX):]).decode("utf-8")
    except (InvalidToken, UnicodeError, TypeError, ValueError) as exc:
        raise SecretKeyError(
            f"A two-factor secret cannot be decrypted with {TOTP_KEY_ENV}; "
            "was the key changed?"
        ) from exc


def opens_with_primary_key(stored: str) -> bool:
    """Whether an encrypted value already uses the first configured key."""
    keys = _configured_keys()
    if not keys or not is_encrypted(stored):
        return False
    try:
        Fernet(keys[0].encode("ascii")).decrypt(stored[len(ENCRYPTED_PREFIX):])
        return True
    except (InvalidToken, UnicodeError, TypeError, ValueError):
        return False


def reseal_secret(stored: str) -> str:
    """Return ``stored`` encrypted with the first key (plain or older key)."""
    fernet = _fernet()
    if fernet is None:
        raise SecretKeyError(f"{TOTP_KEY_ENV} is not set")
    if not is_encrypted(stored):
        return seal_secret(stored) or stored
    try:
        token = fernet.rotate(stored[len(ENCRYPTED_PREFIX):])
    except (InvalidToken, UnicodeError, TypeError, ValueError) as exc:
        raise SecretKeyError(
            f"A two-factor secret cannot be decrypted with {TOTP_KEY_ENV}; "
            "was the key changed?"
        ) from exc
    return ENCRYPTED_PREFIX + token.decode("ascii")
