"""Validation and normalisation for database-backed pillar subscriptions."""

from __future__ import annotations

from typing import Any, Mapping


PILLAR_NOTIFICATION_EVENT_TYPES = frozenset(
    {
        "pillar_created",
        "pillar_dismantled",
        "pillar_name_changed",
        "reward_shares_changed",
        "pillar_inactive",
        "pillar_active",
    }
)
NETWORK_NOTIFICATION_EVENT_TYPES = frozenset({"epoch_available"})
SUPPORTED_NOTIFICATION_EVENT_TYPES = (
    PILLAR_NOTIFICATION_EVENT_TYPES | NETWORK_NOTIFICATION_EVENT_TYPES
)
DEFAULT_SUBSCRIPTION_EVENTS = (
    "pillar_inactive",
    "pillar_active",
    "reward_shares_changed",
)


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.replace(",", "\n").splitlines()
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("Expected a string or a list of strings")
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def normalise_subscription(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Subscription must be a JSON object")
    channel_id = str(payload.get("channel_id", "")).strip()
    if not channel_id:
        raise ValueError("Channel ID is required")
    if len(channel_id) > 255:
        raise ValueError("Channel ID is too long")

    owners = string_list(payload.get("pillar_owner_addresses", []))
    if any(len(owner) > 255 for owner in owners):
        raise ValueError("A pillar owner address is too long")
    if len(owners) > 500:
        raise ValueError("A subscription may contain at most 500 pillars")

    if payload.get("events") is None:
        events = list(DEFAULT_SUBSCRIPTION_EVENTS)
    else:
        events = string_list(payload.get("events"))
    event_keys = {event.casefold() for event in events}
    if "all" in event_keys:
        events = sorted(SUPPORTED_NOTIFICATION_EVENT_TYPES)
        event_keys = set(events)
    invalid = set(events) - SUPPORTED_NOTIFICATION_EVENT_TYPES
    if invalid:
        raise ValueError(
            "Unsupported event type(s): " + ", ".join(sorted(invalid))
        )
    if not owners and not (event_keys & NETWORK_NOTIFICATION_EVENT_TYPES):
        raise ValueError(
            "Pillar events require at least one pillar owner address"
        )

    return {
        "channel_id": channel_id,
        "pillar_owner_addresses": owners,
        "events": events,
        "label": str(payload.get("label", "")).strip()[:120],
        "active": bool(payload.get("active", True)),
    }
