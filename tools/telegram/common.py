from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.env_loader import get_env_value
from utils.telegram_wrapper import TelegramWrapper


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"


def load_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    path = Path(config_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {path}. "
            "Create config/config.json first."
        )
    try:
        with path.open(encoding="utf-8") as file:
            config = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in configuration file: {path}") from exc
    if not isinstance(config, dict):
        raise ValueError("Configuration root must be a JSON object")
    return config


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
