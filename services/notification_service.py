from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from models.database import Database
from utils.discord_wrapper import DiscordWrapper
from utils.env_loader import get_env_value
from utils.telegram_wrapper import TelegramWrapper


logger = logging.getLogger(__name__)


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
DEFAULT_PILLAR_NOTIFICATION_EVENTS = (
    "pillar_inactive",
    "pillar_active",
    "reward_shares_changed",
)
EPOCH_NOTIFICATION_EMOJIS = (
    "🚀",
    "🎉",
    "✨",
    "🔥",
    "🌟",
    "🥳",
    "💫",
)


def _name(event: Mapping[str, Any]) -> str:
    details = event.get("details") or {}
    return str(
        details.get("name")
        or details.get("new_name")
        or event.get("owner_address")
        or "Unknown pillar"
    )


def _epoch_notification_emoji(epoch: Any) -> str:
    try:
        index = int(epoch) % len(EPOCH_NOTIFICATION_EMOJIS)
    except (TypeError, ValueError):
        index = 0
    return EPOCH_NOTIFICATION_EMOJIS[index]


def format_event(event: Mapping[str, Any]) -> str:
    event_type = event.get("event_type")
    details = event.get("details") or {}
    name = _name(event)

    if event_type == "epoch_available":
        return (
            f"Rewards for epoch {event.get('epoch')} can now be collected! "
            f"{_epoch_notification_emoji(event.get('epoch'))}"
        )
    if event_type == "pillar_created":
        suffix = " again" if details.get("reappeared") else ""
        return (
            f"New pillar spawned{suffix}: {name}\n"
            f"Momentum rewards: {details.get('momentum_reward_percentage', 0)}%\n"
            f"Delegate rewards: {details.get('delegate_reward_percentage', 0)}%"
        )
    if event_type == "pillar_dismantled":
        return f"{name} has been dismantled."
    if event_type == "pillar_name_changed":
        return (
            f"Pillar name changed:\n"
            f"{details.get('old_name', name)} ➡️ {details.get('new_name', name)}"
        )
    if event_type == "reward_shares_changed":
        lines = [f"Pillar: {name}"]
        if "momentum" in details:
            change = details["momentum"]
            lines.append(f"Momentum rewards: {change.get('old')}% ➡️ {change.get('new')}%")
        if "delegate" in details:
            change = details["delegate"]
            lines.append(f"Delegate rewards: {change.get('old')}% ➡️ {change.get('new')}%")
        return "\n".join(lines)
    if event_type == "pillar_inactive":
        return (
            f"{name} has stopped producing momentums. "
            f"Missed checks: {details.get('missed_momentums', 0)}"
        )
    if event_type == "pillar_active":
        return f"{name} is producing momentums again! 🚀"
    return f"Pillar Tracker event: {event_type}"


class NotificationDispatcher:
    def __init__(self, database: Database, config: Mapping[str, Any]):
        self.config = dict(config)
        timeout = float(config.get("http_timeout_seconds", 15))
        self.database = database
        self.telegram = TelegramWrapper(
            get_env_value("TELEGRAM_BOT_API_KEY"),
            timeout=timeout,
            rate_limit_retries=int(config.get("telegram_rate_limit_retries", 2)),
            rate_limit_max_wait_seconds=float(
                config.get("rate_limit_max_wait_seconds", 60)
            ),
        )
        self.telegram_channel_id = str(
            config.get("telegram_channel_id", "")
        ).strip()
        self.discord = DiscordWrapper(timeout=timeout)
        self.discord_webhook = str(
            config.get("discord_channel_webhook", "")
        ).strip()
        (
            self.pillar_event_channels,
            self.network_event_channels,
        ) = self._build_pillar_event_channels(config)

    @staticmethod
    def _string_list(value: Any, field_name: str) -> list[str]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple)):
            values = list(value)
        else:
            raise ValueError(f"{field_name} must be a string or JSON array")
        result = [str(item).strip() for item in values if str(item).strip()]
        return result

    def _build_pillar_event_channels(
        self,
        config: Mapping[str, Any],
    ) -> tuple[
        dict[str, dict[str, tuple[str, ...]]],
        dict[str, tuple[str, ...]],
    ]:
        """Build per-owner and network-wide Telegram routes."""
        if not self.telegram.enabled:
            return {}, {}
        configured = (
            self.database.get_active_subscription_config()
            if self.database.has_subscriptions()
            else config.get("telegram_pillar_subscriptions", [])
        )
        if configured in (None, []):
            return {}, {}
        if not isinstance(configured, list):
            raise ValueError("telegram_pillar_subscriptions must be a JSON array")

        global_channel_id = self.telegram_channel_id.casefold()
        result: dict[str, dict[str, list[str]]] = {}
        network_result: dict[str, list[str]] = {}
        for index, subscription in enumerate(configured):
            if not isinstance(subscription, Mapping):
                raise ValueError(
                    f"telegram_pillar_subscriptions[{index}] must be an object"
                )
            channel_id = str(subscription.get("channel_id", "")).strip()
            if not channel_id:
                raise ValueError(
                    f"telegram_pillar_subscriptions[{index}].channel_id is required"
                )
            if channel_id.casefold() == global_channel_id and global_channel_id:
                # The global route already sends to this channel. Avoid duplicate
                # messages when a user lists the main channel as a subscription.
                continue

            owners = self._string_list(
                subscription.get("pillar_owner_addresses", []),
                f"telegram_pillar_subscriptions[{index}].pillar_owner_addresses",
            )
            configured_events = subscription.get("events")
            if configured_events is None:
                events = list(DEFAULT_PILLAR_NOTIFICATION_EVENTS)
            else:
                events = self._string_list(
                    configured_events,
                    f"telegram_pillar_subscriptions[{index}].events",
                )
            if "all" in {event.casefold() for event in events}:
                events = sorted(SUPPORTED_NOTIFICATION_EVENT_TYPES)
            invalid_events = set(events) - SUPPORTED_NOTIFICATION_EVENT_TYPES
            if invalid_events:
                invalid = ", ".join(sorted(invalid_events))
                raise ValueError(
                    f"Unsupported pillar notification event(s): {invalid}"
                )

            route = f"telegram_chat:{channel_id}"
            for event_type in events:
                if event_type in NETWORK_NOTIFICATION_EVENT_TYPES:
                    network_result.setdefault(event_type, []).append(route)
                    continue
                if not owners:
                    raise ValueError(
                        f"telegram_pillar_subscriptions[{index}] must contain "
                        "pillar_owner_addresses for pillar events"
                    )
                for owner in owners:
                    owner_key = owner.casefold()
                    owner_events = result.setdefault(owner_key, {})
                    owner_events.setdefault(event_type, []).append(route)

        return (
            {
                owner: {
                    event_type: tuple(dict.fromkeys(routes))
                    for event_type, routes in event_map.items()
                }
                for owner, event_map in result.items()
            },
            {
                event_type: tuple(dict.fromkeys(routes))
                for event_type, routes in network_result.items()
            },
        )

    def refresh_routes(self) -> None:
        """Reload DB-backed subscription routes before a collector poll."""
        (
            self.pillar_event_channels,
            self.network_event_channels,
        ) = self._build_pillar_event_channels(self.config)

    @property
    def channels(self) -> tuple[str, ...]:
        channels: list[str] = []
        if self.telegram.enabled and self.telegram_channel_id:
            channels.append("telegram")
        if self.discord_webhook:
            channels.append("discord")
        return tuple(channels)

    def dispatch_pending(self, limit: int = 50) -> dict[str, int]:
        sent = 0
        failed = 0
        for notification in self.database.get_pending_notifications(limit):
            notification_id = int(notification["id"])
            if not self.database.claim_notification(notification_id):
                continue
            try:
                message = format_event(notification)
                channel = notification["channel"]
                if channel == "telegram" or channel.startswith("telegram_chat:"):
                    target_channel_id = (
                        self.telegram_channel_id
                        if channel == "telegram"
                        else channel.split(":", 1)[1]
                    )
                    if not target_channel_id:
                        raise RuntimeError(
                            "Telegram notification route has no channel ID"
                        )
                    response = self.telegram.bot_send_message_to_chat(
                        target_channel_id,
                        message,
                    )
                    if not self.telegram.response_ok(response):
                        raise RuntimeError(
                            f"Telegram returned HTTP {response.status_code}"
                        )
                elif channel == "discord":
                    response = self.discord.webhook_send_message_to_channel(
                        self.discord_webhook,
                        message,
                    )
                    if not 200 <= response.status_code < 300:
                        raise RuntimeError(
                            f"Discord returned HTTP {response.status_code}"
                        )
                else:
                    raise RuntimeError(f"Unsupported notification channel: {channel}")
                self.database.mark_notification_sent(notification_id)
                sent += 1
            except Exception as exc:
                self.database.mark_notification_failed(notification_id, str(exc))
                failed += 1
                logger.warning("Notification %s failed: %s", notification_id, exc)
        return {"sent": sent, "failed": failed}


def create_pinned_stats_message(
    pillars: Mapping[str, Mapping[str, Any]],
    momentum_height: int,
) -> str:
    ordered = sorted(
        pillars.values(),
        key=lambda item: (
            item.get("rank") is None,
            item.get("rank") if item.get("rank") is not None else 999999,
        ),
    )
    title = (
        "Pillar reward sharing rates"
        + (" (top 70)" if len(ordered) > 70 else "")
    )
    lines = [
        title,
        "Last updated: "
        + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        + " (UTC)",
        f"Momentum height: {momentum_height}",
        "M = momentum reward %, D = delegate reward %, W = weight in ZNN",
        "P/E = produced/expected momentums",
        "",
    ]

    for pillar in ordered:
        rank = pillar.get("rank")
        if rank is None or rank >= 70:
            continue
        weight = round((int(pillar.get("weight") or 0)) / 100000000)
        stats = pillar.get("currentStats") or {}
        status = " ⚠️" if pillar.get("status") == "inactive" else ""
        line = (
            f"{rank + 1} - {pillar.get('name')} -> "
            f"M: {pillar.get('giveMomentumRewardPercentage', 0)}% "
            f"D: {pillar.get('giveDelegateRewardPercentage', 0)}% "
            f"W: {weight} "
            f"P/E: {stats.get('producedMomentums', 0)}/"
            f"{stats.get('expectedMomentums', 0)}{status}"
        )
        lines.append(line)
        if len("\n".join(lines)) > 3900:
            lines.append("…")
            break
    return "\n".join(lines)
