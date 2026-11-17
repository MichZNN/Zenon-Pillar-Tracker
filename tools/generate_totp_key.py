"""Print a new key for TOTP_ENCRYPTION_KEY.

Add the printed line to the project .env file and restart the web app; the
two-factor secrets already stored are then encrypted automatically. Keep the
key safe: without it the stored secrets cannot be read, and every user with
two-factor authentication would need a recovery code to log in.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.secret_box import TOTP_KEY_ENV, generate_key  # noqa: E402


def main() -> int:
    print(f"{TOTP_KEY_ENV}={generate_key()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
