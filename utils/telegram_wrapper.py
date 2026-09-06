from __future__ import annotations

import json
import threading
import time
from typing import Any, Iterable, Mapping

import requests

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
        self._session_local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._session_local, "session", None)
        if session is None:
            session = requests.Session()
            self._session_local.session = session
        return session

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
                session=self._session(),
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

    def _get(
        self,
        method: str,
        *,
        params: Mapping[str, Any] | None = None,
    ):
        if not self.enabled:
            raise RuntimeError("Telegram bot API key is not configured")
        for attempt in range(self.rate_limit_retries + 1):
            response = HttpWrapper.get(
                f"{self.API_BASE_URL}/bot{self.bot_api_key}/{method}",
                params=params,
                timeout=self.timeout,
                session=self._session(),
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

    def bot_send_message_to_chat(
        self,
        chat_id: str,
        message: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
    ):
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        return self._call("sendMessage", data)

    def bot_edit_message(
        self,
        chat_id: str,
        message_id: int,
        message: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
    ):
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": message,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        return self._call("editMessageText", data)

    def bot_answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ):
        data: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            data["text"] = text
        return self._call("answerCallbackQuery", data)

    def bot_get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 0,
        allowed_updates: Iterable[str] | None = None,
    ):
        params: dict[str, Any] = {"timeout": max(0, int(timeout))}
        if offset is not None:
            params["offset"] = int(offset)
        if allowed_updates is not None:
            params["allowed_updates"] = json.dumps(
                list(allowed_updates),
                separators=(",", ":"),
            )
        return self._get("getUpdates", params=params)

    def bot_get_webhook_info(self):
        return self._get("getWebhookInfo")
