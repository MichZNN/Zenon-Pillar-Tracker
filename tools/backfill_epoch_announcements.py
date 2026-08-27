"""Recover live Telegram announcement timestamps from the SQLite outbox."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.database import Database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill epoch announcement times from sent Telegram notifications."
    )
    parser.add_argument(
        "--database",
        default="data_store/pillar_tracker.sqlite3",
        help="SQLite path, relative to the project root by default.",
    )
    args = parser.parse_args()

    database_path = Path(args.database)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path

    database = Database(database_path)
    updated = database.backfill_announcement_times_from_notifications()
    print(f"Backfilled announcement times for {updated} epoch records.")
    print("Existing epoch start times and historical announcement data were preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
