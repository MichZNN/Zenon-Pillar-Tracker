"""Import the old JSON cache into the tracker SQLite database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import Database, utc_now  # noqa: E402


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    return value if isinstance(value, dict) else None


def as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import an old JSON cache into a new SQLite database."
    )
    parser.add_argument(
        "--data-store",
        default="data_store",
        help="Directory containing the old JSON files.",
    )
    parser.add_argument(
        "--database",
        default="data_store/pillar_tracker.sqlite3",
        help="Target SQLite database.",
    )
    args = parser.parse_args()

    data_store = Path(args.data_store)
    if not data_store.is_absolute():
        data_store = PROJECT_ROOT / data_store
    database_path = Path(args.database)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path

    pillar_data = read_json(data_store / "pillar_data.json")
    epoch_data = read_json(data_store / "epoch_data.json")
    node_status = read_json(data_store / "node_status_data.json") or {}
    momentum_status = read_json(data_store / "momentum_status_data.json") or {}
    if not pillar_data or not pillar_data.get("pillars"):
        print(f"No legacy pillar_data.json found in {data_store}")
        return 2
    if not epoch_data or as_int(epoch_data.get("epoch")) is None:
        print(f"No usable legacy epoch_data.json found in {data_store}")
        return 2

    database = Database(database_path)
    if database.get_overview().get("last_snapshot_at") is not None:
        print(
            "Refusing to import: the target database already contains snapshots. "
            "Use a new database path for a separate import."
        )
        return 3

    observed_at = str(
        pillar_data.get("timestamp") or epoch_data.get("timestamp") or utc_now()
    )
    height = as_int(node_status.get("height"), 0) or 0
    pillars = {}
    for owner_address, pillar in pillar_data["pillars"].items():
        current = dict(pillar)
        current["ownerAddress"] = current.get("ownerAddress", owner_address)
        current["raw"] = current.get("raw", pillar)
        pillars[owner_address] = current

    poll_run_id = database.begin_poll(
        started_at=observed_at,
        momentum={"height": height, "hash": "legacy-json"},
    )
    database.record_observation(
        poll_run_id=poll_run_id,
        observed_at=observed_at,
        momentum={
            "height": height,
            "hash": "legacy-json",
            "timestamp": None,
        },
        epoch_data={
            "epoch": as_int(epoch_data["epoch"]),
            "znn_reward": as_int(epoch_data.get("reward"), 0) or 0,
            "qsr_reward": 0,
            "source_address": "legacy-json",
            "source": "legacy_json",
        },
        pillars=pillars,
        notification_channels=(),
    )
    legacy_statuses = momentum_status.get("data")
    status_count = 0
    if isinstance(legacy_statuses, dict):
        status_count = database.apply_legacy_statuses(
            legacy_statuses,
            observed_at=observed_at,
        )
    database.update_node_state(
        height=height,
        momentum_hash="legacy-json",
        momentum_timestamp=None,
        health="healthy",
        stale_count=0,
        last_success_at=observed_at,
    )
    database.finish_poll(poll_run_id, "success")
    print(
        f"Imported {len(pillars)} pillars, epoch {epoch_data['epoch']} "
        f"and {status_count} legacy status records into {database_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
