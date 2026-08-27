from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.settings_service import DEFAULT_SETTINGS  # noqa: E402
from models.database import Database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or update the Zenon Pillar Tracker SQLite database."
    )
    parser.add_argument(
        "--database",
        default="data_store/pillar_tracker.sqlite3",
        help="SQLite file path (default: data_store/pillar_tracker.sqlite3)",
    )
    args = parser.parse_args()

    database_path = Path(args.database)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    database_path = database_path.resolve()

    database = Database(database_path)
    database.ensure_settings(DEFAULT_SETTINGS)
    print(f"SQLite database ready: {database_path}")
    print("Tables and indexes have been created or verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
