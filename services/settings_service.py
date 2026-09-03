"""SQLite-backed runtime settings.

This module contains code defaults and the database loader used by the
collector, dashboard, and maintenance tools. JSON configuration files are not
part of the runtime configuration path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from models.database import Database


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data_store" / "pillar_tracker.sqlite3"

# database_path and the Telegram bot token intentionally do not belong here:
# the former is needed to find the database, while the latter remains a secret
# supplied through .env.
DEFAULT_SETTINGS: dict[str, Any] = {
    "node_rpc_urls": [],
    "node_require_sync_info": False,
    "node_max_frontier_age_seconds": 300,
    "node_failure_cooldown_seconds": 120,
    "node_sync_retry_seconds": 30,
    "node_sync_retry_interval_seconds": 5,
    "http_timeout_seconds": 15,
    "rpc_retries": 2,
    "rate_limit_max_wait_seconds": 60,
    "telegram_rate_limit_retries": 2,
    "pillar_page_size": 250,
    "reward_page_size": 100,
    "poll_interval_seconds": 60,
    "stale_grace_runs": 3,
    "missed_momentums_threshold": 5,
    "allow_empty_pillars": False,
    "telegram_channel_id": "",
    "telegram_pinned_message_id": "",
    "telegram_dev_channel_id": "",
    "discord_channel_webhook": "",
    "reference_reward_address": "",
    "log_max_bytes": 5 * 1024 * 1024,
    "log_backup_count": 5,
    "log_level": "INFO",
    "auth_session_hours": 12,
}


def validate_settings(settings: Mapping[str, Any]) -> None:
    """Validate values that can otherwise make a running service unusable."""
    if not isinstance(settings, Mapping):
        raise ValueError("Settings must be a JSON object")
    if "log_path" in settings:
        raise ValueError(
            "log_path is fixed at data_store/pillar_tracker.log and cannot be changed"
        )
    urls = settings.get("node_rpc_urls")
    if urls is not None and (
        not isinstance(urls, list)
        or any(not isinstance(url, str) or not url.strip() for url in urls)
    ):
        raise ValueError("node_rpc_urls must be a list of non-empty strings")
    positive_integer_settings = {
        "pillar_page_size",
        "reward_page_size",
        "rpc_retries",
        "telegram_rate_limit_retries",
        "stale_grace_runs",
        "missed_momentums_threshold",
    }
    positive_number_settings = {
        "http_timeout_seconds",
        "rate_limit_max_wait_seconds",
        "poll_interval_seconds",
        "node_max_frontier_age_seconds",
        "node_failure_cooldown_seconds",
        "node_sync_retry_seconds",
        "node_sync_retry_interval_seconds",
        "auth_session_hours",
    }
    for key in positive_integer_settings:
        if key in settings:
            try:
                value = int(settings[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be an integer") from exc
            if value < 0 or (key not in {"rpc_retries", "telegram_rate_limit_retries"} and value == 0):
                raise ValueError(f"{key} must be greater than zero")
    for key in positive_number_settings:
        if key in settings:
            try:
                value = float(settings[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a number") from exc
            if value <= 0 and key != "node_max_frontier_age_seconds":
                raise ValueError(f"{key} must be greater than zero")
    if "node_max_frontier_age_seconds" in settings:
        try:
            if float(settings["node_max_frontier_age_seconds"]) < 0:
                raise ValueError("node_max_frontier_age_seconds cannot be negative")
        except (TypeError, ValueError) as exc:
            if str(exc) == "node_max_frontier_age_seconds cannot be negative":
                raise
            raise ValueError("node_max_frontier_age_seconds must be a number") from exc
    if "log_max_bytes" in settings:
        try:
            if int(settings["log_max_bytes"]) < 1:
                raise ValueError("log_max_bytes must be greater than zero")
        except (TypeError, ValueError) as exc:
            if str(exc) == "log_max_bytes must be greater than zero":
                raise
            raise ValueError("log_max_bytes must be an integer") from exc
    if "log_backup_count" in settings:
        try:
            value = int(settings["log_backup_count"])
        except (TypeError, ValueError) as exc:
            raise ValueError("log_backup_count must be an integer") from exc
        if value < 0 or value > 20:
            raise ValueError("log_backup_count must be between 0 and 20")
    if "log_level" in settings and str(settings["log_level"]).upper() not in {
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    }:
        raise ValueError("log_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    for key in {"node_require_sync_info", "allow_empty_pillars"}:
        if key in settings and not isinstance(settings[key], bool):
            raise ValueError(f"{key} must be a boolean")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _runtime_config(
    database: Database,
    selected_database_path: Path,
) -> dict[str, Any]:
    database.ensure_settings(DEFAULT_SETTINGS)
    runtime = dict(DEFAULT_SETTINGS)
    runtime.update(database.get_settings())
    # Databases created by older releases may still contain this setting. It
    # is no longer part of the runtime configuration; logs always use the
    # writable data_store mount.
    runtime.pop("log_path", None)
    runtime["database_path"] = str(selected_database_path)
    runtime["telegram_pillar_subscriptions"] = (
        database.get_active_subscription_config()
    )
    return runtime


def load_runtime_config(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Load all runtime settings from SQLite, with code defaults."""
    selected_database_path = resolve_path(database_path)
    database = Database(selected_database_path)
    return _runtime_config(database, selected_database_path)


def load_runtime_config_from_database(database: Database) -> dict[str, Any]:
    """Load current settings using an already-open database connection target."""
    return _runtime_config(database, Path(database.path))


def load_runtime_database(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> tuple[dict[str, Any], Database]:
    """Variant used by the web server when it needs both settings and DB."""
    selected_database_path = resolve_path(database_path)
    database = Database(selected_database_path)
    return _runtime_config(database, selected_database_path), database
