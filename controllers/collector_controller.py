from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.settings_service import load_runtime_config
from models.database import Database, utc_now
from services.logging_service import configure_logging
from services.notification_service import NotificationDispatcher, create_pinned_stats_message
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
    def __init__(self, config: Mapping[str, Any]):
        self.config = dict(config)
        node_urls = _configured_node_urls(self.config)

        timeout = float(self.config.get("http_timeout_seconds", 15))
        self.database = Database(
            _resolve_path(
                self.config.get(
                    "database_path",
                    "data_store/pillar_tracker.sqlite3",
                )
            )
        )
        nodes = [
            NodeRpcWrapper(
                node_url,
                timeout=timeout,
                page_size=int(self.config.get("pillar_page_size", 250)),
                retries=int(self.config.get("rpc_retries", 2)),
                rate_limit_max_wait_seconds=float(
                    self.config.get("rate_limit_max_wait_seconds", 60)
                ),
            )
            for node_url in node_urls
        ]
        self.node = NodeRpcPool(
            nodes,
            require_sync_info=bool(
                self.config.get("node_require_sync_info", False)
            ),
            max_frontier_age_seconds=float(
                self.config.get("node_max_frontier_age_seconds", 300)
            ),
            failure_cooldown_seconds=float(
                self.config.get("node_failure_cooldown_seconds", 120)
            ),
            sync_retry_seconds=float(
                self.config.get("node_sync_retry_seconds", 30)
            ),
            sync_retry_interval_seconds=float(
                self.config.get("node_sync_retry_interval_seconds", 5)
            ),
        )
        self.node_urls = node_urls
        self.dispatcher = NotificationDispatcher(self.database, self.config)
        self.missed_momentums_threshold = max(
            1,
            int(self.config.get("missed_momentums_threshold", 5)),
        )
        self.stale_grace_runs = max(
            1,
            int(self.config.get("stale_grace_runs", 3)),
        )
        self.allow_empty_pillars = bool(
            self.config.get("allow_empty_pillars", False)
        )
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

    def _update_pinned_message(
        self,
        pillars: Mapping[str, Mapping[str, Any]],
        momentum_height: int,
    ) -> None:
        if not self.dispatcher.telegram.enabled:
            return
        channel_id = str(self.config.get("telegram_channel_id", "")).strip()
        message_id = _as_int(self.config.get("telegram_pinned_message_id"))
        if not channel_id or message_id is None or message_id <= 0:
            return

        current = self.database.get_pillars(
            status="all",
            include_performance=False,
        )["items"]
        current_by_address: dict[str, dict[str, Any]] = {}
        for item in current:
            if not item["is_present"]:
                continue
            current_item = dict(pillars.get(item["owner_address"], {}))
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

        message = create_pinned_stats_message(current_by_address, momentum_height)
        try:
            response = self.dispatcher.telegram.bot_edit_message(
                channel_id,
                message_id,
                message,
            )
            if not self.dispatcher.telegram.response_ok(response):
                logger.warning(
                    "Telegram pinned message returned HTTP %s",
                    response.status_code,
                )
        except Exception as exc:
            logger.warning("Could not update Telegram pinned message: %s", exc)

    def run_once(self) -> dict[str, Any]:
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
        interval = max(
            5,
            int(
                interval_seconds
                or self.config.get("poll_interval_seconds", 60)
            ),
        )
        logger.info("Collector loop started; polling every %s seconds", interval)
        while True:
            try:
                self.run_once()
            except Exception:
                pass
            time.sleep(interval)


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
