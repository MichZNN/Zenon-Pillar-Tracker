from __future__ import annotations

from pathlib import Path
from typing import Any

from services.settings_service import DEFAULT_DATABASE_PATH, load_runtime_config
from utils.env_loader import get_env_value
from utils.telegram_wrapper import TelegramWrapper


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_runtime_settings(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Load runtime settings from SQLite."""
    return load_runtime_config(database_path)


def create_telegram_client(config: dict[str, Any]) -> TelegramWrapper:
    token = get_env_value("TELEGRAM_BOT_API_KEY")
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_API_KEY is empty or missing in the project .env file"
        )
    timeout = float(config.get("http_timeout_seconds", 15))
    return TelegramWrapper(token, timeout=timeout)


def require_success(response: Any, operation: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"{operation} returned HTTP {response.status_code} with invalid JSON"
        ) from exc

    if not 200 <= response.status_code < 300 or not payload.get("ok", False):
        description = payload.get("description") or "Unknown Telegram error"
        raise RuntimeError(
            f"{operation} failed with HTTP {response.status_code}: {description}"
        )
    return payload
