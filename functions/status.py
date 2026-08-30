from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping


def collector_liveness_timeout(poll_interval_seconds: Any = 60) -> int:
    """Return a forgiving timeout for the collector heartbeat.

    The collector already updates ``node_state.updated_at`` after each poll.
    The dashboard can use that timestamp without making another node request.
    Two polling intervals are enough to detect a stopped process while the
    two-minute floor avoids false alarms for a slow but healthy installation.
    """
    try:
        interval = float(poll_interval_seconds)
    except (TypeError, ValueError):
        interval = 60.0
    if not isfinite(interval) or interval <= 0:
        interval = 60.0
    return max(120, int(interval * 2))


def collector_status(
    node: Mapping[str, Any] | None,
    *,
    last_run: Mapping[str, Any] | None = None,
    poll_interval_seconds: Any = 60,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Translate the stored collector state into a simple user-facing status.

    This is intentionally a timestamp calculation, not a network health
    check. ``updated_at`` is the collector heartbeat and is written for every
    completed or failed poll. A stopped process therefore becomes offline
    after the liveness timeout without adding any work to the collector or the
    dashboard's request path.
    """
    timeout_seconds = collector_liveness_timeout(poll_interval_seconds)
    node = node or {}
    run = last_run or {}
    health = str(node.get("health") or "unknown").lower()
    run_status = str(run.get("status") or "").lower() or None
    run_error = str(run.get("error") or "").strip() or None
    last_attempt_at = run.get("completed_at") or run.get("started_at")
    diagnostic_fields = {
        "node_health": health,
        "last_success_at": node.get("last_success_at"),
        "last_attempt_at": last_attempt_at,
        "last_attempt_status": run_status,
        "last_error": run_error,
        "last_run_id": run.get("id"),
    }
    report_at = node.get("updated_at") or node.get("last_success_at")
    if not report_at or (health == "unknown" and not (node or {}).get("last_success_at")):
        return {
            "state": "unknown",
            "label": "Waiting for tracker",
            "description": "The tracker has not reported yet.",
            **diagnostic_fields,
            "last_report_at": None,
            "age_seconds": None,
            "timeout_seconds": timeout_seconds,
        }

    try:
        parsed = datetime.fromisoformat(str(report_at))
    except ValueError:
        parsed = None
    if parsed is None:
        return {
            "state": "unknown",
            "label": "Waiting for tracker",
            "description": "The tracker status could not be read.",
            **diagnostic_fields,
            "last_report_at": str(report_at),
            "age_seconds": None,
            "timeout_seconds": timeout_seconds,
        }
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age_seconds = max(0, int((current - parsed).total_seconds()))

    if run_status == "failed" and run_error:
        state = "red"
        label = "Collector error"
        description = "The latest collector check failed."
    elif age_seconds > timeout_seconds or health == "error":
        state = "red"
        label = "Tracker offline"
        description = "No recent tracker update is available."
    elif health in {"stale", "reorg"}:
        state = "orange"
        label = "Needs attention"
        description = "The tracker is running, but its latest check needs attention."
    elif health == "healthy":
        state = "green"
        label = "Running normally"
        description = "The tracker is reporting normally."
    else:
        state = "orange"
        label = "Needs attention"
        description = "The tracker is reporting an unusual status."

    return {
        "state": state,
        "label": label,
        "description": description,
        **diagnostic_fields,
        "last_report_at": str(report_at),
        "age_seconds": age_seconds,
        "timeout_seconds": timeout_seconds,
    }


def _int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stats(pillar: Mapping[str, Any]) -> Mapping[str, Any]:
    return pillar.get("currentStats") or {}


def evaluate_pillar_status(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    epoch: int | None,
    threshold: int = 5,
) -> dict[str, Any]:
    """Determine status from two valid on-chain snapshots.

    The counter is increased only when expected momentums changes while
    produced momentums stays unchanged. A new epoch resets the counter so an
    ordinary epoch rollover cannot create a false inactive alert.
    """
    threshold = max(1, int(threshold))
    current_stats = _stats(current)
    produced = _int(current_stats.get("producedMomentums"))
    expected = _int(current_stats.get("expectedMomentums"))

    if not previous or not bool(previous.get("is_present", True)):
        return {
            "status": "active",
            "missed_momentums": 0,
            "reason": "initial_observation",
        }

    previous_status = previous.get("status") or "active"
    previous_stats = {
        "producedMomentums": previous.get("produced_momentums"),
        "expectedMomentums": previous.get("expected_momentums"),
    }
    previous_produced = _int(previous_stats.get("producedMomentums"))
    previous_expected = _int(previous_stats.get("expectedMomentums"))
    previous_missed = _int(previous.get("missed_momentums"), 0) or 0
    previous_epoch = _int(previous.get("last_seen_epoch"))
    epoch_changed = (
        epoch is not None
        and previous_epoch is not None
        and epoch > previous_epoch
    )

    if (
        produced is not None
        and previous_produced is not None
        and produced > previous_produced
    ):
        return {
            "status": "active",
            "missed_momentums": 0,
            "reason": "momentum_produced",
        }

    if epoch_changed or (
        produced == 0
        and previous_produced is not None
        and previous_produced > 0
    ):
        return {
            "status": previous_status if previous_status in {"active", "inactive"} else "active",
            "missed_momentums": 0,
            "reason": "epoch_reset",
        }

    if (
        produced is not None
        and previous_produced is not None
        and produced == previous_produced
        and expected != previous_expected
    ):
        missed = previous_missed + 1
        return {
            "status": "inactive" if missed >= threshold else previous_status,
            "missed_momentums": missed,
            "reason": "expected_increased_without_production",
        }

    if produced is not None and previous_produced is not None and produced < previous_produced:
        return {
            "status": previous_status,
            "missed_momentums": 0,
            "reason": "counter_decreased_without_epoch_change",
        }

    return {
        "status": previous_status,
        "missed_momentums": previous_missed,
        "reason": "no_status_change",
    }
