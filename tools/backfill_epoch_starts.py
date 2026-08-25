from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import Database  # noqa: E402
from epoch_schedule import (  # noqa: E402
    DEFAULT_EPOCH_DURATION_SECONDS,
    DEFAULT_EPOCH_REFERENCE_EPOCH,
    DEFAULT_EPOCH_REFERENCE_START_AT,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill epoch start times in SQLite, using observed on-chain "
            "transitions when available."
        )
    )
    parser.add_argument(
        "--database",
        default="data_store/pillar_tracker.sqlite3",
        help="SQLite database path.",
    )
    parser.add_argument(
        "--reference-epoch",
        type=int,
        default=DEFAULT_EPOCH_REFERENCE_EPOCH,
        help=f"Reference epoch (default: {DEFAULT_EPOCH_REFERENCE_EPOCH}).",
    )
    parser.add_argument(
        "--reference-start-at",
        default=DEFAULT_EPOCH_REFERENCE_START_AT,
        help=(
            "Reference epoch start in UTC "
            f"(default: {DEFAULT_EPOCH_REFERENCE_START_AT})."
        ),
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=DEFAULT_EPOCH_DURATION_SECONDS,
        help=f"Epoch duration (default: {DEFAULT_EPOCH_DURATION_SECONDS}).",
    )
    args = parser.parse_args()

    database_path = Path(args.database)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path

    database = Database(database_path)
    updated = database.backfill_epoch_start_times(
        reference_epoch=args.reference_epoch,
        reference_start_at=args.reference_start_at,
        duration_seconds=args.duration_seconds,
    )
    observed = database.backfill_observed_epoch_start_times()
    print(f"Backfilled epoch start times for {updated} epoch records.")
    print(f"Applied {observed} observed on-chain epoch transition times.")
    print("Announcement timestamps and other historical data were preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
