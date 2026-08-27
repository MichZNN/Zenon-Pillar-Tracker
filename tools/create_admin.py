"""Create an administrator account without putting a password in config files."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.auth_service import (  # noqa: E402
    hash_password,
    password_is_acceptable,
    username_is_acceptable,
)
from services.settings_service import (  # noqa: E402
    DEFAULT_DATABASE_PATH,
    resolve_path,
)
from models.database import Database  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a Zenon tracker administrator")
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="SQLite database path",
    )
    parser.add_argument("--username", help="Administrator username")
    parser.add_argument("--display-name", default="")
    args = parser.parse_args(argv)

    username = (args.username or input("Admin username: ")).strip()
    if not username_is_acceptable(username):
        parser.error(
            "username must be 3-80 characters and contain only letters, numbers, ., _ or -"
        )
    password = getpass.getpass("Admin password (12+ characters): ")
    confirmation = getpass.getpass("Repeat admin password: ")
    if password != confirmation:
        parser.error("Passwords do not match")
    if not password_is_acceptable(password):
        parser.error("Password must contain at least 12 characters")

    database = Database(resolve_path(args.database))
    try:
        user = database.create_user(
            username=username,
            display_name=args.display_name,
            password_hash=hash_password(password),
            role="admin",
        )
    except Exception as exc:
        print(f"Could not create administrator: {exc}", file=sys.stderr)
        return 1
    print(f"Administrator created: {user['username']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
