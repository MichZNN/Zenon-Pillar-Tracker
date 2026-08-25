from __future__ import annotations

from typing import Any, Mapping


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
