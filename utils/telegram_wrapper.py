from __future__ import annotations

import time
from typing import Any

from .http_wrapper import HttpWrapper


class TelegramWrapper:
    API_BASE_URL = "https://api.telegram.org"

    def __init__(
        self,
        bot_api_key: str,
        *,
        timeout: float = 15,
        rate_limit_retries: int = 2,
        rate_limit_max_wait_seconds: float = 60,
    ):
        self.bot_api_key = bot_api_key.strip()
        self.timeout = timeout
        self.rate_limit_retries = max(0, min(int(rate_limit_retries), 5))
        self.rate_limit_max_wait_seconds = max(
            1.0,
            float(rate_limit_max_wait_seconds),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.bot_api_key)

    def _call(self, method: str, data: dict[str, Any]):
        if not self.enabled:
            raise RuntimeError("Telegram bot API key is not configured")
        for attempt in range(self.rate_limit_retries + 1):
            response = HttpWrapper.post(
                f"{self.API_BASE_URL}/bot{self.bot_api_key}/{method}",
                data,
                timeout=self.timeout,
            )
            if (
                response.status_code != 429
                or attempt >= self.rate_limit_retries
            ):
                return response
            time.sleep(
                HttpWrapper.retry_after_seconds(
                    response,
                    fallback=min(2 ** attempt, 5),
                    maximum=self.rate_limit_max_wait_seconds,
                )
            )
        raise RuntimeError("Telegram rate-limit retry loop did not complete")

    def _get(self, method: str):
        if not self.enabled:
            raise RuntimeError("Telegram bot API key is not configured")
        for attempt in range(self.rate_limit_retries + 1):
            response = HttpWrapper.get(
                f"{self.API_BASE_URL}/bot{self.bot_api_key}/{method}",
                timeout=self.timeout,
            )
            if (
                response.status_code != 429
                or attempt >= self.rate_limit_retries
            ):
                return response
            time.sleep(
                HttpWrapper.retry_after_seconds(
                    response,
                    fallback=min(2 ** attempt, 5),
                    maximum=self.rate_limit_max_wait_seconds,
                )
            )
        raise RuntimeError("Telegram rate-limit retry loop did not complete")

    @staticmethod
    def response_ok(response: Any) -> bool:
        if not 200 <= response.status_code < 300:
            return False
        try:
            payload = response.json()
        except ValueError:
            return True
        return bool(payload.get("ok", True))

    def bot_send_message_to_chat(self, chat_id: str, message: str):
        return self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
        )

    def bot_edit_message(self, chat_id: str, message_id: int, message: str):
        return self._call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": message,
                "disable_web_page_preview": True,
            },
        )

    def bot_get_updates(self):
        return self._get("getUpdates")

    def bot_get_webhook_info(self):
        return self._get("getWebhookInfo")
