"""Small, dependency-free authentication helpers for the local web service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 310_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,80}$")


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("Password is required")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(PASSWORD_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        iterations = int(iterations_text)
        if scheme != PASSWORD_SCHEME or iterations < 100_000:
            return False
        padding = "=" * (-len(salt_text) % 4)
        salt = base64.urlsafe_b64decode((salt_text + padding).encode("ascii"))
        padding = "=" * (-len(digest_text) % 4)
        expected = base64.urlsafe_b64decode(
            (digest_text + padding).encode("ascii")
        )
    except (ValueError, TypeError, UnicodeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def password_is_acceptable(password: str) -> bool:
    """Return whether a password meets the UI/API minimum."""
    return isinstance(password, str) and 12 <= len(password) <= 1024


def username_is_acceptable(username: str) -> bool:
    return isinstance(username, str) and bool(USERNAME_PATTERN.fullmatch(username))


# ---------------------------------------------------------------------------
# Two-factor authentication (TOTP, RFC 6238) without extra dependencies.
# Compatible with common authenticator apps: SHA-1, 6 digits, 30 seconds.
# ---------------------------------------------------------------------------

TOTP_ISSUER = "Zenon Pillar Tracker"
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
# Accept the previous and next 30-second step to allow for clock drift.
TOTP_WINDOW_STEPS = 1
RECOVERY_CODE_COUNT = 10


def generate_totp_secret() -> str:
    """Return a new base32 secret (160 bits) without padding."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_key(secret: str) -> bytes:
    cleaned = re.sub(r"\s+", "", str(secret)).upper()
    padding = "=" * (-len(cleaned) % 8)
    return base64.b32decode(cleaned + padding)


def totp_code(secret: str, step: int) -> str:
    """Return the code for one 30-second time step."""
    digest = hmac.new(
        _totp_key(secret),
        int(step).to_bytes(8, "big"),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(value % (10 ** TOTP_DIGITS)).zfill(TOTP_DIGITS)


def normalise_two_factor_code(code: object) -> str:
    """Strip spaces and dashes users may type or paste."""
    return re.sub(r"[\s-]+", "", str(code or "")).lower()


def verify_totp(
    secret: str,
    code: object,
    *,
    now: float | None = None,
    last_step: int | None = None,
) -> int | None:
    """Return the matching time step, or None.

    A step that was already used (``last_step``) or an older one is rejected,
    so a code cannot be replayed.
    """
    import time

    value = normalise_two_factor_code(code)
    if not secret or len(value) != TOTP_DIGITS or not value.isdigit():
        return None
    current = int((time.time() if now is None else now) // TOTP_PERIOD_SECONDS)
    for step in range(current - TOTP_WINDOW_STEPS, current + TOTP_WINDOW_STEPS + 1):
        if last_step is not None and step <= int(last_step):
            continue
        if hmac.compare_digest(totp_code(secret, step), value):
            return step
    return None


def totp_uri(secret: str, username: str, issuer: str = TOTP_ISSUER) -> str:
    """Return the otpauth:// URI shown as a QR code."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{username}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Return one-time recovery codes like ``k3m9q-2xw7p`` (50 bits each)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery_code(code: object) -> str:
    return hash_token(normalise_two_factor_code(code))
