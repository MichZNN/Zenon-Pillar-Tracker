from __future__ import annotations

from datetime import datetime, timedelta, timezone


DEFAULT_EPOCH_REFERENCE_EPOCH = 1627
DEFAULT_EPOCH_REFERENCE_START_AT = "2026-05-10T13:30:00+00:00"
DEFAULT_EPOCH_DURATION_SECONDS = 86_400


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "Epoch reference start must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def calculate_epoch_start(
    epoch: int,
    *,
    reference_epoch: int = DEFAULT_EPOCH_REFERENCE_EPOCH,
    reference_start_at: str = DEFAULT_EPOCH_REFERENCE_START_AT,
    duration_seconds: int = DEFAULT_EPOCH_DURATION_SECONDS,
) -> str:
    """Calculate an epoch start from a known UTC schedule reference."""
    try:
        epoch_number = int(epoch)
        reference_number = int(reference_epoch)
        duration = int(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("Epoch schedule values must be integers") from exc
    if duration <= 0:
        raise ValueError("Epoch duration must be greater than zero")

    start = _parse_timestamp(reference_start_at)
    offset = (epoch_number - reference_number) * duration
    return (start + timedelta(seconds=offset)).isoformat(timespec="seconds")
