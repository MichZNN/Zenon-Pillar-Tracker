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
