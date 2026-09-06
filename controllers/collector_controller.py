from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.settings_service import (
    load_runtime_config,
    load_runtime_config_from_database,
)
from models.database import Database, utc_now
from services.logging_service import configure_logging
from services.notification_service import (
    DEFAULT_TELEGRAM_BOT_USERNAME,
    NotificationDispatcher,
    create_pinned_stats_keyboard,
    create_pinned_stats_message,
    parse_pinned_callback_data,
    pinned_stats_page_count,
    pinned_stats_status_summary,
)
from utils.node_rpc_pool import NodeRpcPool
from utils.node_rpc_wrapper import NodeRpcWrapper


BASE_DIR = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _configured_node_urls(config: Mapping[str, Any]) -> list[str]:
    """Return configured RPC endpoints in primary-to-backup order."""
    configured_urls = config.get("node_rpc_urls")
    if not isinstance(configured_urls, list):
        raise ValueError("node_rpc_urls must be a JSON array of URLs")
    raw_urls = configured_urls

    node_urls: list[str] = []
    for raw_url in raw_urls:
        if not isinstance(raw_url, str):
            raise ValueError("Every node RPC URL must be a string")
        node_url = raw_url.strip().rstrip("/")
        if node_url and node_url not in node_urls:
            node_urls.append(node_url)
    if not node_urls:
        raise ValueError("node_rpc_urls must contain at least one URL")
    return node_urls


class Collector:
    SETTINGS_CHECK_INTERVAL_SECONDS = 60

    def __init__(self, config: Mapping[str, Any]):
        initial_config = dict(config)
        self.database = Database(
            _resolve_path(
                initial_config.get(
                    "database_path",
                    "data_store/pillar_tracker.sqlite3",
                )
            )
        )
        self._settings_revision = self.database.get_settings_revision()
        self._settings_reload_error_revision: int | None = None
        self._telegram_update_offset: int | None = None
        self._pinned_status = "all"
        self._pinned_page = 1
        self._last_momentum_height: int | None = None
        self._telegram_pillar_snapshot: dict[str, dict[str, Any]] | None = None
        self._apply_runtime_config(initial_config)

    def _apply_runtime_config(self, config: Mapping[str, Any]) -> None:
        """Build runtime components before swapping them into the collector."""
        next_config = dict(config)
        node_urls = _configured_node_urls(next_config)
        timeout = float(next_config.get("http_timeout_seconds", 15))
        nodes = [
            NodeRpcWrapper(
                node_url,
                timeout=timeout,
                page_size=int(next_config.get("pillar_page_size", 250)),
                retries=int(next_config.get("rpc_retries", 2)),
                rate_limit_max_wait_seconds=float(
                    next_config.get("rate_limit_max_wait_seconds", 60)
                ),
            )
            for node_url in node_urls
        ]
        node = NodeRpcPool(
            nodes,
            require_sync_info=bool(
                next_config.get("node_require_sync_info", False)
            ),
            max_frontier_age_seconds=float(
                next_config.get("node_max_frontier_age_seconds", 300)
            ),
            failure_cooldown_seconds=float(
                next_config.get("node_failure_cooldown_seconds", 120)
            ),
            sync_retry_seconds=float(
                next_config.get("node_sync_retry_seconds", 30)
            ),
            sync_retry_interval_seconds=float(
                next_config.get("node_sync_retry_interval_seconds", 5)
            ),
        )
        dispatcher = NotificationDispatcher(self.database, next_config)

        self.config = next_config
        self.node_urls = node_urls
        self.node = node
        self.dispatcher = dispatcher
        self.missed_momentums_threshold = max(
            1,
            int(next_config.get("missed_momentums_threshold", 5)),
        )
        self.stale_grace_runs = max(
            1,
            int(next_config.get("stale_grace_runs", 3)),
        )
        self.allow_empty_pillars = bool(
            next_config.get("allow_empty_pillars", False)
        )

    def _reload_settings_if_changed(self) -> bool:
        """Apply the latest SQLite settings without restarting the process."""
        revision = self.database.get_settings_revision()
        if revision == self._settings_revision:
            return False

        try:
            next_config = load_runtime_config_from_database(self.database)
            self._apply_runtime_config(next_config)
            configure_logging(next_config)
        except Exception:
            if self._settings_reload_error_revision != revision:
                logger.exception(
                    "Could not apply runtime settings revision %s; "
                    "keeping the previous valid configuration",
                    revision,
                )
                self._settings_reload_error_revision = revision
            return False

        self._settings_revision = revision
        self._settings_reload_error_revision = None
        logger.info("Runtime settings reloaded (revision %s)", revision)
        return True

    def _add_epoch_start_times(
        self,
        epoch_data: Mapping[str, Any],
        epoch_history: list[Mapping[str, Any]] | None,
        *,
        observed_epoch: int | None = None,
        observed_epoch_start_at: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
        def enrich(entry: Mapping[str, Any]) -> dict[str, Any]:
            enriched = dict(entry)
            epoch = _as_int(enriched.get("epoch"))
            if epoch is not None:
                if (
                    epoch == observed_epoch
                    and observed_epoch_start_at is not None
                ):
                    enriched["epoch_start_at"] = observed_epoch_start_at
                    enriched["epoch_start_inferred"] = False
                    enriched["epoch_start_observed"] = True
            return enriched

        enriched_history = (
            [enrich(entry) for entry in epoch_history]
            if epoch_history is not None
            else None
        )
        return enrich(epoch_data), enriched_history

    @staticmethod
    def _momentum_timestamp_as_utc(
        momentum: Mapping[str, Any],
    ) -> str | None:
        timestamp = _as_int(momentum.get("timestamp"))
        if timestamp is None or timestamp < 1_000_000_000:
            return None
        try:
            return datetime.fromtimestamp(
                timestamp,
                timezone.utc,
            ).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None

    def _mark_failed(
        self,
        poll_run_id: int,
        error: Exception,
    ) -> None:
        message = str(error)[:1000]
        self.database.finish_poll(
            poll_run_id,
            "failed",
            error=message,
        )
        state = self.database.get_node_state()
        self.database.update_node_state(
            height=state.get("last_momentum_height"),
            momentum_hash=state.get("last_momentum_hash"),
            momentum_timestamp=state.get("last_momentum_timestamp"),
            health="error",
            stale_count=int(state.get("stale_count") or 0),
            last_success_at=state.get("last_success_at"),
        )
        logger.error("Collector failed: %s", message)
        dev_channel_id = str(
            self.config.get("telegram_dev_channel_id", "")
        ).strip()
        if dev_channel_id and self.dispatcher.telegram.enabled:
            try:
                self.dispatcher.telegram.bot_send_message_to_chat(
                    dev_channel_id,
                    f"Pillar Tracker collector error: {message}",
                )
            except Exception as notify_error:
                logger.warning(
                    "Could not send developer error message: %s",
                    notify_error,
                )

    def _mark_stale(
        self,
        poll_run_id: int,
        latest_momentum: Mapping[str, Any],
        previous_state: Mapping[str, Any],
        *,
        reason: str | None = None,
        sync_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        stale_count = int(previous_state.get("stale_count") or 0) + 1
        health = (
            "stale"
            if stale_count >= self.stale_grace_runs
            else "healthy"
        )
        self.database.update_node_state(
            height=previous_state.get("last_momentum_height"),
            momentum_hash=previous_state.get("last_momentum_hash"),
            momentum_timestamp=previous_state.get("last_momentum_timestamp"),
            health=health,
            stale_count=stale_count,
            last_success_at=previous_state.get("last_success_at"),
        )
        self.database.finish_poll(poll_run_id, "stale")
        result = {
            "status": "stale",
            "height": latest_momentum.get("height"),
            "stale_count": stale_count,
            "health": health,
        }
        if reason == "node_syncing" and sync_info is not None:
            logger.info(
                "Poll deferred: node is still syncing "
                f"(state {sync_info.get('state')}, current height "
                f"{sync_info.get('currentHeight')}, target height "
                f"{sync_info.get('targetHeight')}; stale check "
                f"{stale_count}, health: {health})"
            )
            result["reason"] = reason
            result["sync_info"] = dict(sync_info)
        else:
            logger.info(
                f"No new momentum at height {latest_momentum.get('height')} "
                f"(stale check {stale_count}, health: {health})"
            )
        return result

    def _mark_reorg(
        self,
        poll_run_id: int,
        latest_momentum: Mapping[str, Any],
        previous_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        self.database.update_node_state(
            height=previous_state.get("last_momentum_height"),
            momentum_hash=previous_state.get("last_momentum_hash"),
            momentum_timestamp=previous_state.get("last_momentum_timestamp"),
            health="reorg",
            stale_count=int(previous_state.get("stale_count") or 0),
            last_success_at=previous_state.get("last_success_at"),
        )
        self.database.finish_poll(poll_run_id, "reorg")
        result = {
            "status": "reorg",
            "height": latest_momentum.get("height"),
            "previous_height": previous_state.get("last_momentum_height"),
        }
        logger.warning(
            f"Momentum height moved backwards or hash changed: "
            f"{previous_state.get('last_momentum_height')} -> "
            f"{latest_momentum.get('height')}"
        )
        return result

    def _get_pinned_pillars(
        self,
        pillars: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        if pillars is None and self._telegram_pillar_snapshot is not None:
            return self._telegram_pillar_snapshot
        source = pillars or {}
        current = self.database.get_pillars(
            status="all",
            include_performance=False,
        )["items"]
        current_by_address: dict[str, dict[str, Any]] = {}
        for item in current:
            if not item["is_present"]:
                continue
            current_item = dict(source.get(item["owner_address"], {}))
            current_item.update(
                {
                    "name": item["name"],
                    "rank": item["rank"],
                    "weight": item["weight"],
                    "giveMomentumRewardPercentage": item[
                        "momentum_reward_percentage"
                    ],
                    "giveDelegateRewardPercentage": item[
                        "delegate_reward_percentage"
                    ],
                    "status": item["status"],
                    "currentStats": {
                        "producedMomentums": item["produced_momentums"],
                        "expectedMomentums": item["expected_momentums"],
                    },
                }
            )
            current_by_address[item["owner_address"]] = current_item
        self._telegram_pillar_snapshot = current_by_address
        return current_by_address

    def _pinned_momentum_height(self, fallback: int | None = None) -> int:
        if fallback is not None:
            self._last_momentum_height = fallback
        if self._last_momentum_height is not None:
            return self._last_momentum_height
        try:
            health = self.database.get_health()
            node = health.get("node") or {}
            self._last_momentum_height = _as_int(
                node.get("last_momentum_height")
            )
        except Exception:
            self._last_momentum_height = None
        return self._last_momentum_height or 0

    def _edit_pinned_message(
        self,
        pillars: Mapping[str, Mapping[str, Any]],
        momentum_height: int,
    ) -> None:
        channel_id = str(self.config.get("telegram_channel_id", "")).strip()
        message_id = _as_int(self.config.get("telegram_pinned_message_id"))
        page_count = pinned_stats_page_count(pillars, self._pinned_status)
        self._pinned_page = min(max(1, self._pinned_page), page_count)
        message = create_pinned_stats_message(
            pillars,
            momentum_height,
            status=self._pinned_status,
            page=self._pinned_page,
        )
        reply_markup = create_pinned_stats_keyboard(
            status=self._pinned_status,
            page=self._pinned_page,
            page_count=page_count,
            bot_username=self.config.get(
                "telegram_bot_username",
                DEFAULT_TELEGRAM_BOT_USERNAME,
            ),
        )
        response = self.dispatcher.telegram.bot_edit_message(
            channel_id,
            message_id,
            message,
            reply_markup=reply_markup,
        )
        if not self.dispatcher.telegram.response_ok(response):
            logger.warning(
                "Telegram pinned message returned HTTP %s",
                response.status_code,
            )

    def _update_pinned_message(
        self,
        pillars: Mapping[str, Mapping[str, Any]],
        momentum_height: int,
    ) -> None:
        if not self.dispatcher.telegram.enabled:
            return
        current_by_address = self._get_pinned_pillars(pillars)
        self._last_momentum_height = momentum_height
        channel_id = str(self.config.get("telegram_channel_id", "")).strip()
        message_id = _as_int(self.config.get("telegram_pinned_message_id"))
        if not channel_id or message_id is None or message_id <= 0:
            return

        try:
            self._edit_pinned_message(current_by_address, momentum_height)
        except Exception as exc:
            logger.warning("Could not update Telegram pinned message: %s", exc)

    def _telegram_updates_configured(self) -> bool:
        """Return whether the collector can receive bot updates."""
        channel_id = str(self.config.get("telegram_channel_id", "")).strip()
        return self.dispatcher.telegram.enabled and bool(channel_id)

    def _answer_telegram_callback(
        self,
        callback_query_id: str,
        text: str | None = None,
    ) -> None:
        try:
            response = self.dispatcher.telegram.bot_answer_callback_query(
                callback_query_id,
                text=text,
            )
            if not self.dispatcher.telegram.response_ok(response):
                logger.warning(
                    "Telegram callback answer returned HTTP %s",
                    response.status_code,
                )
        except Exception as exc:
            logger.warning("Could not answer Telegram callback: %s", exc)

    def _send_telegram_message(
        self,
        chat_id: Any,
        message: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> bool:
        try:
            response = self.dispatcher.telegram.bot_send_message_to_chat(
                str(chat_id),
                message,
                reply_markup=reply_markup,
            )
            if not self.dispatcher.telegram.response_ok(response):
                logger.warning(
                    "Telegram sendMessage returned HTTP %s",
                    response.status_code,
                )
                return False
            return True
        except Exception as exc:
            logger.warning("Could not send Telegram message: %s", exc)
            return False

    @staticmethod
    def _parse_pillars_command(arguments: list[str]) -> tuple[str, int]:
        status = "all"
        page = 1
        for argument in arguments:
            value = argument.strip().casefold()
            if value in {"all", "active", "inactive"}:
                status = value
                continue
            try:
                page = max(1, int(value))
            except (TypeError, ValueError):
                continue
        return status, page

    def _private_pillar_keyboard(
        self,
        pillars: Mapping[str, Mapping[str, Any]],
        status: str,
        page: int,
    ) -> dict[str, list[list[dict[str, str]]]]:
        page_count = pinned_stats_page_count(pillars, status)
        current_page = min(max(1, int(page)), page_count)
        return create_pinned_stats_keyboard(
            status=status,
            page=current_page,
            page_count=page_count,
            include_bot_button=False,
        )

    def _send_private_pillar_message(
        self,
        chat_id: Any,
        *,
        status: str = "all",
        page: int = 1,
    ) -> bool:
        pillars = self._get_pinned_pillars()
        keyboard = self._private_pillar_keyboard(pillars, status, page)
        return self._send_telegram_message(
            chat_id,
            create_pinned_stats_message(
                pillars,
                self._pinned_momentum_height(),
                status=status,
                page=page,
            ),
            reply_markup=keyboard,
        )

    def _edit_private_pillar_message(
        self,
        chat_id: Any,
        message_id: Any,
        *,
        status: str,
        page: int,
        current_message: str | None = None,
    ) -> bool:
        numeric_message_id = _as_int(message_id)
        if numeric_message_id is None:
            return False
        pillars = self._get_pinned_pillars()
        page_count = pinned_stats_page_count(pillars, status)
        current_page = min(max(1, int(page)), page_count)
        rendered_message = create_pinned_stats_message(
            pillars,
            self._pinned_momentum_height(),
            status=status,
            page=current_page,
        )
        if current_message == rendered_message:
            return True
        response = self.dispatcher.telegram.bot_edit_message(
            str(chat_id),
            numeric_message_id,
            rendered_message,
            reply_markup=create_pinned_stats_keyboard(
                status=status,
                page=current_page,
                page_count=page_count,
                include_bot_button=False,
            ),
        )
        if not self.dispatcher.telegram.response_ok(response):
            logger.warning(
                "Telegram private pillar message returned HTTP %s",
                response.status_code,
            )
            return False
        return True

    def _handle_telegram_message(self, message: Mapping[str, Any]) -> None:
        chat = message.get("chat")
        if not isinstance(chat, Mapping) or chat.get("type") != "private":
            return
        text = str(message.get("text") or "").strip()
        if not text.startswith("/"):
            return
        command_parts = text.split()
        command = command_parts[0][1:].split("@", 1)[0].casefold()
        chat_id = chat.get("id")
        if chat_id is None:
            return

        if command in {"start", "help"}:
            pillars = self._get_pinned_pillars()
            self._send_telegram_message(
                chat_id,
                "\U0001f44b Zenon Pillar Tracker\n\n"
                f"{pinned_stats_status_summary(pillars)}\n"
                f"Momentum height: {self._pinned_momentum_height()}\n\n"
                "Use the buttons below to browse the pillar list.\n"
                "Commands: /status, /pillars [all|active|inactive] [page]",
                reply_markup=self._private_pillar_keyboard(
                    pillars,
                    "all",
                    1,
                ),
            )
            return

        if command == "status":
            pillars = self._get_pinned_pillars()
            self._send_telegram_message(
                chat_id,
                "Zenon Pillar Tracker status\n\n"
                f"{pinned_stats_status_summary(pillars)}\n"
                f"Momentum height: {self._pinned_momentum_height()}\n\n"
                "Use /pillars to browse the list.",
                reply_markup=self._private_pillar_keyboard(
                    pillars,
                    "all",
                    1,
                ),
            )
            return

        if command == "pillars":
            status, page = self._parse_pillars_command(command_parts[1:])
            self._send_private_pillar_message(
                chat_id,
                status=status,
                page=page,
            )
            return

        self._send_telegram_message(
            chat_id,
            "Unknown command. Use /help to see what I can do.",
        )

    def _handle_private_telegram_callback(
        self,
        callback_id: str,
        message: Mapping[str, Any],
        parsed: tuple[str, int],
    ) -> None:
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, Mapping) else None
        if chat_id is None:
            self._answer_telegram_callback(
                callback_id,
                "This button is no longer active.",
            )
            return
        status, page = parsed
        self._answer_telegram_callback(callback_id)
        try:
            updated = self._edit_private_pillar_message(
                chat_id,
                message.get("message_id"),
                status=status,
                page=page,
                current_message=str(message.get("text") or ""),
            )
            if not updated:
                logger.warning("Could not update the private pillar list")
        except Exception as exc:
            logger.warning("Could not handle private Telegram callback: %s", exc)

    def _handle_telegram_callback(
        self,
        callback: Mapping[str, Any],
    ) -> None:
        callback_id = str(callback.get("id", "")).strip()
        if not callback_id:
            return

        message = callback.get("message")
        chat = message.get("chat") if isinstance(message, Mapping) else None
        chat_id = chat.get("id") if isinstance(chat, Mapping) else None
        parsed = parse_pinned_callback_data(callback.get("data"))
        if (
            isinstance(chat, Mapping)
            and str(chat.get("type", "")).casefold() == "private"
        ):
            if parsed is None:
                self._answer_telegram_callback(callback_id, "Unknown button.")
                return
            self._handle_private_telegram_callback(
                callback_id,
                message,
                parsed,
            )
            return

        channel_id = str(self.config.get("telegram_channel_id", "")).strip()
        message_id = _as_int(self.config.get("telegram_pinned_message_id"))
        configured_username = channel_id.lstrip("@").casefold()
        chat_username = str(
            chat.get("username", "") if isinstance(chat, Mapping) else ""
        ).casefold()
        channel_matches = (
            str(chat_id or "") == channel_id
            or (
                channel_id.startswith("@")
                and configured_username
                and chat_username == configured_username
            )
        )
        if (
            not isinstance(message, Mapping)
            or not channel_matches
            or _as_int(message.get("message_id")) != message_id
        ):
            self._answer_telegram_callback(
                callback_id,
                "This button is no longer active.",
            )
            return

        if parsed is None:
            self._answer_telegram_callback(callback_id, "Unknown button.")
            return
        status, page = parsed
        if status == self._pinned_status and page == self._pinned_page:
            self._answer_telegram_callback(callback_id)
            return

        self._pinned_status = status
        self._pinned_page = page
        self._answer_telegram_callback(callback_id)
        try:
            pillars = self._get_pinned_pillars()
            self._edit_pinned_message(
                pillars,
                self._pinned_momentum_height(),
            )
        except Exception as exc:
            logger.warning("Could not handle Telegram callback: %s", exc)

    def _process_telegram_updates(self, timeout: int = 0) -> bool:
        if not self._telegram_updates_configured():
            return False
        try:
            response = self.dispatcher.telegram.bot_get_updates(
                offset=self._telegram_update_offset,
                timeout=max(0, int(timeout)),
                allowed_updates=("callback_query", "message"),
            )
            if not self.dispatcher.telegram.response_ok(response):
                logger.warning(
                    "Telegram getUpdates returned HTTP %s",
                    response.status_code,
                )
                return True
            payload = response.json()
            updates = payload.get("result", []) if isinstance(payload, Mapping) else []
            for update in updates:
                if not isinstance(update, Mapping):
                    continue
                update_id = _as_int(update.get("update_id"))
                if update_id is not None:
                    next_offset = update_id + 1
                    if (
                        self._telegram_update_offset is None
                        or next_offset > self._telegram_update_offset
                    ):
                        self._telegram_update_offset = next_offset
                callback = update.get("callback_query")
                if isinstance(callback, Mapping):
                    self._handle_telegram_callback(callback)
                message = update.get("message")
                if isinstance(message, Mapping):
                    self._handle_telegram_message(message)
        except Exception as exc:
            logger.warning("Could not process Telegram updates: %s", exc)
        return True

    def run_once(self) -> dict[str, Any]:
        self._reload_settings_if_changed()
        started_at = utc_now()
        poll_run_id = self.database.begin_poll(started_at)
        latest_momentum: dict[str, Any] | None = None
        try:
            self.dispatcher.refresh_routes()
            previous_state = self.database.get_node_state()
            previous_height = _as_int(
                previous_state.get("last_momentum_height")
            )
            overview_before_observation = self.database.get_overview()
            is_bootstrap = overview_before_observation.get("last_snapshot_at") is None
            previous_epoch = _as_int(
                (overview_before_observation.get("epoch") or {}).get("epoch")
            )
            snapshot = self.node.collect_snapshot(
                reference_reward_address=str(
                    self.config.get("reference_reward_address", "")
                ).strip(),
                previous_height=previous_height,
                previous_hash=previous_state.get("last_momentum_hash"),
                previous_epoch=previous_epoch,
                reward_page_size=int(self.config.get("reward_page_size", 100)),
                allow_empty_pillars=self.allow_empty_pillars,
            )
            latest_momentum = snapshot.latest_momentum
            self.database.update_poll_momentum(poll_run_id, latest_momentum)

            if snapshot.status == "reorg":
                return self._mark_reorg(
                    poll_run_id,
                    latest_momentum,
                    previous_state,
                )
            if snapshot.status == "stale":
                return self._mark_stale(
                    poll_run_id,
                    latest_momentum,
                    previous_state,
                    reason=snapshot.reason,
                    sync_info=snapshot.sync_info,
                )

            current_height = _as_int(latest_momentum.get("height"))
            if current_height is None:
                raise RuntimeError("Latest momentum has no height")
            pillars = snapshot.pillars or {}
            epoch_data = snapshot.epoch_data
            if not isinstance(epoch_data, dict):
                raise RuntimeError("Node snapshot has no epoch data")
            current_epoch = _as_int(epoch_data.get("epoch"))
            is_new_epoch = (
                current_epoch is not None
                and previous_epoch is not None
                and current_epoch > previous_epoch
            )
            observed_epoch_start_at = (
                self._momentum_timestamp_as_utc(latest_momentum)
                if is_new_epoch
                else None
            )
            epoch_data, epoch_history = self._add_epoch_start_times(
                epoch_data,
                snapshot.epoch_history,
                observed_epoch=current_epoch if is_new_epoch else None,
                observed_epoch_start_at=observed_epoch_start_at,
            )
            observation = self.database.record_observation(
                poll_run_id=poll_run_id,
                observed_at=utc_now(),
                momentum=latest_momentum,
                epoch_data=epoch_data,
                epoch_history=epoch_history,
                pillars=pillars,
                missed_momentums_threshold=self.missed_momentums_threshold,
                notification_channels=(
                    () if is_bootstrap else self.dispatcher.channels
                ),
                pillar_notification_channels=(
                    {} if is_bootstrap else self.dispatcher.pillar_event_channels
                ),
                network_notification_channels=(
                    {} if is_bootstrap else self.dispatcher.network_event_channels
                ),
            )
            self.database.update_node_state(
                height=current_height,
                momentum_hash=latest_momentum.get("hash"),
                momentum_timestamp=_as_int(latest_momentum.get("timestamp")),
                health="healthy",
                stale_count=0,
                last_success_at=utc_now(),
            )
            self.database.finish_poll(poll_run_id, "success")

            self._update_pinned_message(pillars, current_height)
            self._process_telegram_updates()
            notification_result = self.dispatcher.dispatch_pending()
            result = {
                "status": "success",
                **observation,
                "bootstrap": is_bootstrap,
                "notifications": notification_result,
            }
            logger.info(
                f"Collected height {current_height}, epoch "
                f"{observation['epoch']}, {observation['pillar_count']} pillars "
                f"via {snapshot.node_url}"
            )
            return result
        except Exception as exc:
            self._mark_failed(poll_run_id, exc)
            raise

    def run_forever(self, interval_seconds: int | None = None) -> None:
        interval_override = interval_seconds is not None
        logger.info(
            "Collector loop started; runtime settings are checked every %s "
            "seconds",
            self.SETTINGS_CHECK_INTERVAL_SECONDS,
        )
        while True:
            try:
                self.run_once()
            except Exception:
                pass

            try:
                interval = max(
                    5,
                    int(
                        interval_seconds
                        if interval_override
                        else self.config.get("poll_interval_seconds", 60)
                    ),
                )
            except (TypeError, ValueError):
                interval = 60

            deadline = time.monotonic() + interval
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if self._process_telegram_updates(
                    timeout=min(10, max(0, int(remaining)))
                ):
                    if self._reload_settings_if_changed() and not interval_override:
                        break
                    time.sleep(min(1, remaining))
                    continue
                time.sleep(min(remaining, self.SETTINGS_CHECK_INTERVAL_SECONDS))
                if self._reload_settings_if_changed() and not interval_override:
                    # Apply a changed poll interval immediately instead of
                    # waiting out the previous interval.
                    break


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect Zenon pillar and epoch data into SQLite."
    )
    parser.add_argument(
        "--database",
        default="data_store/pillar_tracker.sqlite3",
        help="Path to the SQLite database",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep running instead of executing one poll.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        help="Polling interval in seconds when --loop is used.",
    )
    args = parser.parse_args(argv)

    logging_configured = False
    try:
        config = load_runtime_config(args.database)
        configure_logging(config)
        logging_configured = True
        collector = Collector(config)
        if args.loop:
            collector.run_forever(args.interval)
        else:
            collector.run_once()
        return 0
    except KeyboardInterrupt:
        logger.info("Collector stopped.")
        return 0
    except Exception as exc:
        if not logging_configured:
            # Runtime configuration is loaded before the configured logger is
            # available. Keep startup failures in the default mounted log too,
            # so a broken database/configuration is diagnosable from Docker.
            configure_logging()
        logger.exception("Collector did not complete: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
