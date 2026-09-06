from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.http_wrapper import HttpWrapper
from utils.telegram_wrapper import TelegramWrapper
from controllers.web_controller import ApiRateLimiter


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class RateLimitTestCase(unittest.TestCase):
    def test_retry_after_header_is_preferred(self):
        response = FakeResponse(
            429,
            payload={"parameters": {"retry_after": 9}},
            headers={"Retry-After": "4"},
        )
        self.assertEqual(HttpWrapper.retry_after_seconds(response), 4.0)

    def test_telegram_retries_using_api_retry_after(self):
        rate_limited = FakeResponse(
            429,
            payload={"parameters": {"retry_after": 3}},
        )
        success = FakeResponse(200, payload={"ok": True, "result": {}})
        with patch(
            "utils.telegram_wrapper.HttpWrapper.post",
            side_effect=[rate_limited, success],
        ), patch("utils.telegram_wrapper.time.sleep") as sleep:
            response = TelegramWrapper(
                "test-token",
                rate_limit_retries=1,
            ).bot_send_message_to_chat("test-chat", "test")

        self.assertIs(response, success)
        sleep.assert_called_once_with(3.0)

    def test_telegram_get_updates_passes_callback_query_options(self):
        response = FakeResponse(200, payload={"ok": True, "result": []})
        with patch(
            "utils.telegram_wrapper.HttpWrapper.get",
            return_value=response,
        ) as get:
            result = TelegramWrapper("test-token").bot_get_updates(
                offset=42,
                timeout=10,
                allowed_updates=("callback_query",),
            )

        self.assertIs(result, response)
        self.assertEqual(get.call_args.kwargs["params"]["offset"], 42)
        self.assertEqual(get.call_args.kwargs["params"]["timeout"], 10)
        self.assertEqual(
            get.call_args.kwargs["params"]["allowed_updates"],
            '["callback_query"]',
        )

    def test_telegram_edit_message_can_include_inline_keyboard(self):
        response = FakeResponse(200, payload={"ok": True, "result": {}})
        keyboard = {"inline_keyboard": [[{"text": "Next", "callback_data": "next"}]]}
        with patch(
            "utils.telegram_wrapper.HttpWrapper.post",
            return_value=response,
        ) as post:
            result = TelegramWrapper("test-token").bot_edit_message(
                "-100channel",
                99,
                "Pillars",
                reply_markup=keyboard,
            )

        self.assertIs(result, response)
        self.assertEqual(post.call_args.args[1]["reply_markup"], keyboard)

    def test_telegram_send_message_can_include_inline_keyboard(self):
        response = FakeResponse(200, payload={"ok": True, "result": {}})
        keyboard = {"inline_keyboard": [[{"text": "Pillars", "callback_data": "pillars"}]]}
        with patch(
            "utils.telegram_wrapper.HttpWrapper.post",
            return_value=response,
        ) as post:
            result = TelegramWrapper("test-token").bot_send_message_to_chat(
                "private-chat",
                "Welcome",
                reply_markup=keyboard,
            )

        self.assertIs(result, response)
        self.assertEqual(post.call_args.args[1]["reply_markup"], keyboard)

    def test_dashboard_limiter_returns_retry_window(self):
        limiter = ApiRateLimiter(max_requests=2, window_seconds=60)
        self.assertEqual(limiter.allow("127.0.0.1"), (True, 0))
        self.assertEqual(limiter.allow("127.0.0.1"), (True, 0))
        allowed, retry_after = limiter.allow("127.0.0.1")
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)
        self.assertEqual(limiter.allow("192.0.2.1"), (True, 0))


if __name__ == "__main__":
    unittest.main()
