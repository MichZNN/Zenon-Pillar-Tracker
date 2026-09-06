from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from functions.subscriptions import normalise_discord_webhook
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
DEFAULT_TELEGRAM_BOT_USERNAME = "ZenonPillarTrackerBot"
PINNED_STATS_PAGE_SIZE = 20
PINNED_STATS_MAX_LENGTH = 3900
PINNED_STATS_STATUSES = frozenset({"all", "active", "inactive"})


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
        self.discord_subscription_webhooks: dict[str, str] = {}
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
        """Build per-owner and network-wide routes for all destinations."""
        self.discord_subscription_webhooks = {}
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
            discord_webhook = normalise_discord_webhook(
                subscription.get("discord_webhook")
            )
            if not channel_id and not discord_webhook:
                raise ValueError(
                    f"telegram_pillar_subscriptions[{index}] must contain "
                    "a Telegram channel ID, a Discord webhook, or both"
                )

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

            routes: list[str] = []
            if (
                channel_id
                and self.telegram.enabled
                and channel_id.casefold() != global_channel_id
            ):
                # The global route already sends to this channel. Avoid a
                # duplicate while still allowing a Discord route on the same
                # subscription.
                routes.append(f"telegram_chat:{channel_id}")

            if discord_webhook and discord_webhook != self.discord_webhook:
                subscription_id = subscription.get("id")
                if subscription_id is None:
                    route = f"discord_webhook:{discord_webhook}"
                else:
                    try:
                        route = f"discord_subscription:{int(subscription_id)}"
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f"telegram_pillar_subscriptions[{index}].id must be an integer"
                        ) from exc
                    self.discord_subscription_webhooks[route] = discord_webhook
                routes.append(route)

            if not routes:
                continue

            for event_type in events:
                if event_type in NETWORK_NOTIFICATION_EVENT_TYPES:
                    network_result.setdefault(event_type, []).extend(routes)
                    continue
                if not owners:
                    raise ValueError(
                        f"telegram_pillar_subscriptions[{index}] must contain "
                        "pillar_owner_addresses for pillar events"
                    )
                for owner in owners:
                    owner_key = owner.casefold()
                    owner_events = result.setdefault(owner_key, {})
                    owner_events.setdefault(event_type, []).extend(routes)

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
                elif channel.startswith("discord_subscription:"):
                    webhook = self.discord_subscription_webhooks.get(channel)
                    if not webhook:
                        raise RuntimeError(
                            "Discord subscription route has no webhook"
                        )
                    response = self.discord.webhook_send_message_to_channel(
                        webhook,
                        message,
                    )
                    if not 200 <= response.status_code < 300:
                        raise RuntimeError(
                            f"Discord returned HTTP {response.status_code}"
                        )
                elif channel.startswith("discord_webhook:"):
                    webhook = channel.split(":", 1)[1]
                    if not webhook:
                        raise RuntimeError(
                            "Discord notification route has no webhook"
                        )
                    response = self.discord.webhook_send_message_to_channel(
                        webhook,
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


def _pinned_rank(pillar: Mapping[str, Any]) -> int | None:
    try:
        return int(pillar.get("rank"))
    except (TypeError, ValueError):
        return None


def _normalise_pinned_status(status: Any) -> str:
    value = str(status or "all").strip().casefold()
    return value if value in PINNED_STATS_STATUSES else "all"


def _ordered_pinned_pillars(
    pillars: Mapping[str, Mapping[str, Any]],
    status: str,
) -> list[Mapping[str, Any]]:
    normalised_status = _normalise_pinned_status(status)
    ordered = [
        pillar
        for pillar in pillars.values()
        if _pinned_rank(pillar) is not None
        and (
            normalised_status == "all"
            or str(pillar.get("status", "")).casefold() == normalised_status
        )
    ]
    return sorted(ordered, key=lambda item: _pinned_rank(item) or 0)


def pinned_stats_page_count(
    pillars: Mapping[str, Mapping[str, Any]],
    status: str = "all",
    page_size: int = PINNED_STATS_PAGE_SIZE,
) -> int:
    size = max(1, int(page_size))
    count = len(_ordered_pinned_pillars(pillars, status))
    return max(1, (count + size - 1) // size)


def pinned_stats_status_summary(
    pillars: Mapping[str, Mapping[str, Any]],
) -> str:
    active = sum(
        1
        for pillar in pillars.values()
        if str(pillar.get("status", "")).casefold() == "active"
    )
    inactive = sum(
        1
        for pillar in pillars.values()
        if str(pillar.get("status", "")).casefold() == "inactive"
    )
    return f"Active: {active} · Inactive: {inactive}"


def _pinned_page(
    pillars: Mapping[str, Mapping[str, Any]],
    status: str,
    page: int,
    page_size: int,
) -> tuple[list[Mapping[str, Any]], str, int, int]:
    size = max(1, int(page_size))
    normalised_status = _normalise_pinned_status(status)
    page_count = pinned_stats_page_count(
        pillars,
        normalised_status,
        page_size=size,
    )
    try:
        current_page = int(page)
    except (TypeError, ValueError):
        current_page = 1
    current_page = min(max(1, current_page), page_count)
    ordered = _ordered_pinned_pillars(pillars, normalised_status)
    start = (current_page - 1) * size
    return (
        ordered[start : start + size],
        normalised_status,
        current_page,
        page_count,
    )


def create_pinned_stats_message(
    pillars: Mapping[str, Mapping[str, Any]],
    momentum_height: int,
    *,
    status: str = "all",
    page: int = 1,
    page_size: int = PINNED_STATS_PAGE_SIZE,
) -> str:
    page_pillars, normalised_status, current_page, page_count = _pinned_page(
        pillars,
        status,
        page,
        page_size,
    )
    status_label = {
        "all": "All",
        "active": "Active",
        "inactive": "Inactive",
    }[normalised_status]
    title = (
        "Pillar reward sharing rates · "
        f"{status_label} · {current_page}/{page_count}"
    )
    lines = [
        title,
        "Last updated: "
        + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        + " (UTC)",
        f"Momentum height: {momentum_height}",
        pinned_stats_status_summary(pillars),
        "M = momentum reward %, D = delegate reward %, W = weight in ZNN",
        "P/E = produced/expected momentums",
        "",
    ]

    for pillar in page_pillars:
        rank = _pinned_rank(pillar)
        weight = round((int(pillar.get("weight") or 0)) / 100000000)
        stats = pillar.get("currentStats") or {}
        inactive_marker = " ⚠️" if pillar.get("status") == "inactive" else ""
        name = " ".join(str(pillar.get("name") or "Unknown pillar").split())
        line = (
            f"{rank + 1} - {name} -> "
            f"M: {pillar.get('giveMomentumRewardPercentage', 0)}% "
            f"D: {pillar.get('giveDelegateRewardPercentage', 0)}% "
            f"W: {weight} "
            f"P/E: {stats.get('producedMomentums', 0)}/"
            f"{stats.get('expectedMomentums', 0)}{inactive_marker}"
        )
        if len("\n".join(lines + [line])) > PINNED_STATS_MAX_LENGTH:
            lines.append("…")
            break
        lines.append(line)
    return "\n".join(lines)


def create_pinned_stats_keyboard(
    *,
    status: str = "all",
    page: int = 1,
    page_count: int = 1,
    bot_username: str = DEFAULT_TELEGRAM_BOT_USERNAME,
    include_bot_button: bool = True,
) -> dict[str, list[list[dict[str, str]]]]:
    normalised_status = _normalise_pinned_status(status)
    total_pages = max(1, int(page_count))
    try:
        current_page = int(page)
    except (TypeError, ValueError):
        current_page = 1
    current_page = min(max(1, current_page), total_pages)

    keyboard = [[
        {
            "text": "⏮️",
            "callback_data": (
                f"pillar:page:{normalised_status}:1"
            ),
        },
        {
            "text": "◀️",
            "callback_data": (
                f"pillar:page:{normalised_status}:{max(1, current_page - 1)}"
            ),
        },
        {
            "text": f"{current_page}/{total_pages}",
            "callback_data": (
                f"pillar:page:{normalised_status}:{current_page}"
            ),
        },
        {
            "text": "▶️",
            "callback_data": (
                f"pillar:page:{normalised_status}:"
                f"{min(total_pages, current_page + 1)}"
            ),
        },
        {
            "text": "⏭️",
            "callback_data": (
                f"pillar:page:{normalised_status}:{total_pages}"
            ),
        },
    ], [
        {
            "text": ("✅ " if normalised_status == "active" else "") + "Active",
            "callback_data": "pillar:page:active:1",
        },
        {
            "text": ("✅ " if normalised_status == "inactive" else "") + "Inactive",
            "callback_data": "pillar:page:inactive:1",
        },
        {
            "text": ("✅ " if normalised_status == "all" else "") + "All",
            "callback_data": "pillar:page:all:1",
        },
    ]]

    username = str(bot_username or "").strip()
    if include_bot_button and username:
        bot_url = (
            username
            if username.startswith(("http://", "https://"))
            else f"https://t.me/{username.lstrip('@')}"
        )
        keyboard.append([{"text": "🤖 Open bot", "url": bot_url}])
    return {"inline_keyboard": keyboard}


def parse_pinned_callback_data(
    value: Any,
) -> tuple[str, int] | None:
    parts = str(value or "").split(":")
    if len(parts) != 4 or parts[:2] != ["pillar", "page"]:
        return None
    status = _normalise_pinned_status(parts[2])
    try:
        page = int(parts[3])
    except (TypeError, ValueError):
        return None
    if page < 1:
        return None
    return status, page
