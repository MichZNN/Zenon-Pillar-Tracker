"""Client for the optional host-side collector control bridge."""

from __future__ import annotations

import json
import os
import socket
from typing import Any


DEFAULT_CONTROL_SOCKET = "/run/zenon-control/collector.sock"
CONTROL_ACTIONS = frozenset({"status", "logs", "start", "stop", "restart"})


class CollectorControlError(RuntimeError):
    """Base error raised when collector control cannot be completed."""


class CollectorControlUnavailable(CollectorControlError):
    """Raised when the host-side bridge is not installed or reachable."""


class CollectorControlClient:
    """Send one allowlisted request to the local Unix-socket bridge."""

    def __init__(
        self,
        socket_path: str | None = None,
        *,
        timeout_seconds: float = 5,
    ) -> None:
        self.socket_path = str(
            socket_path
            or os.environ.get("COLLECTOR_CONTROL_SOCKET", DEFAULT_CONTROL_SOCKET)
        ).strip()
        if not self.socket_path:
            self.socket_path = DEFAULT_CONTROL_SOCKET
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    def request(self, action: str, *, tail: int = 200) -> dict[str, Any]:
        action = str(action).strip().lower()
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"Unsupported collector control action: {action}")
        if not hasattr(socket, "AF_UNIX"):
            raise CollectorControlUnavailable(
                "Collector control bridge requires Unix sockets"
            )
        request: dict[str, Any] = {"action": action}
        if action == "logs":
            request["tail"] = max(1, min(int(tail), 500))

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(self.socket_path)
                connection.sendall(
                    json.dumps(request, separators=(",", ":")).encode("utf-8")
                    + b"\n"
                )
                response = self._read_response(connection)
        except (FileNotFoundError, ConnectionRefusedError, PermissionError, OSError) as exc:
            raise CollectorControlUnavailable(
                f"Collector control bridge is unavailable: {exc}"
            ) from exc

        if not isinstance(response, dict):
            raise CollectorControlError("Collector control returned an invalid response")
        if not response.get("ok", False):
            raise CollectorControlError(
                str(response.get("error") or "Collector control request failed")
            )
        return response

    @staticmethod
    def _read_response(connection: socket.socket) -> Any:
        data = bytearray()
        while b"\n" not in data:
            chunk = connection.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 512 * 1024:
                raise CollectorControlError("Collector control response is too large")
        if not data:
            raise CollectorControlError("Collector control returned no response")
        line = bytes(data).split(b"\n", 1)[0]
        try:
            return json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectorControlError(
                "Collector control returned invalid JSON"
            ) from exc
