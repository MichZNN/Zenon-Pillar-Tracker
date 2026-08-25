from __future__ import annotations

from utils.http_wrapper import HttpWrapper


class DiscordWrapper:
    def __init__(self, *, timeout: float = 15):
        self.timeout = timeout

    def webhook_send_message_to_channel(self, webhook_url: str, message: str):
        return HttpWrapper.post(
            webhook_url,
            {"content": message},
            timeout=self.timeout,
        )
