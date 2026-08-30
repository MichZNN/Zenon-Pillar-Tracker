#!/usr/bin/env python3
"""Restricted host-side control bridge for the collector Compose service.

The web container talks to this process through a Unix socket. Requests are
limited to status/logs and lifecycle operations for the collector service;
there is deliberately no generic command or argument forwarding.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socketserver
import stat
import subprocess
import threading
from pathlib import Path
from typing import Any, Mapping


LOGGER = logging.getLogger("collector_control_bridge")
ALLOWED_ACTIONS = frozenset({"status", "logs", "start", "stop", "restart"})
MAX_REQUEST_BYTES = 8 * 1024
MAX_OUTPUT_BYTES = 512 * 1024


class BridgeError(RuntimeError):
    """An expected bridge or Docker Compose error."""


def _bounded_text(value: str | None, limit: int = MAX_OUTPUT_BYTES) -> str:
    text = str(value or "")
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    return encoded.decode("utf-8", errors="ignore") + "\n[output truncated]"


class CollectorControlBridge:
    """Execute only the collector operations needed by the admin portal."""

    def __init__(
        self,
        compose_dir: str | Path,
        *,
        command_timeout_seconds: float = 60,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.compose_dir = Path(compose_dir).resolve()
        self.command_timeout_seconds = max(1.0, float(command_timeout_seconds))
        self.environment = dict(environment or os.environ)
        self._command_lock = threading.Lock()

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        action = str(request.get("action", "")).strip().lower()
        if action not in ALLOWED_ACTIONS:
            raise BridgeError(f"Unsupported collector control action: {action}")
        with self._command_lock:
            if action == "status":
                return {"ok": True, "action": action, "collector": self.status()}
            if action == "logs":
                tail = self._tail(request.get("tail", 200))
                return {
                    "ok": True,
                    "action": action,
                    "collector": self.status(),
                    "logs": self.logs(tail),
                }
            self._lifecycle(action)
            return {"ok": True, "action": action, "collector": self.status()}

    def _compose(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = dict(self.environment)
        image_state_file = self.compose_dir / ".deploy-image.env"
        try:
            for line in image_state_file.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() == "IMAGE":
                    environment["IMAGE"] = value.strip()
        except OSError:
            # Compose will fall back to the channel image from .env when the
            # optional immutable deployment state file does not exist yet.
            pass
        try:
            result = subprocess.run(
                ["docker", "compose", *arguments],
                cwd=self.compose_dir,
                env=environment,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeError(f"Could not run Docker Compose: {exc}") from exc
        if result.returncode != 0:
            detail = _bounded_text(result.stderr or result.stdout, 16 * 1024).strip()
            raise BridgeError(detail or "Docker Compose returned a failure")
        return result

    @staticmethod
    def _tail(value: Any) -> int:
        try:
            return max(1, min(int(value), 500))
        except (TypeError, ValueError):
            return 200

    def status(self) -> dict[str, Any]:
        result = self._compose("ps", "--all", "--format", "json", "collector")
        records = self._decode_records(result.stdout)
        record = next(
            (
                item
                for item in records
                if str(item.get("Service") or item.get("service") or "")
                == "collector"
            ),
            records[0] if records else None,
        )
        if not record:
            return {
                "running": False,
                "state": "not_created",
                "status": "Container not created",
            }
        state = str(record.get("State") or record.get("state") or "unknown")
        status = str(record.get("Status") or record.get("status") or state)
        return {
            "running": state.casefold() == "running",
            "state": state,
            "status": status,
            "name": str(record.get("Name") or record.get("name") or ""),
            "health": str(record.get("Health") or record.get("health") or ""),
        }

    def logs(self, tail: int = 200) -> str:
        result = self._compose(
            "logs",
            "--no-color",
            f"--tail={self._tail(tail)}",
            "collector",
        )
        return _bounded_text(result.stdout)

    def _lifecycle(self, action: str) -> None:
        if action == "start":
            self._compose("up", "-d", "collector")
        elif action == "stop":
            self._compose("stop", "collector")
        elif action == "restart":
            current = self.status()
            self._compose(
                *("restart", "collector")
                if current.get("name")
                else ("up", "-d", "collector")
            )

    @staticmethod
    def _decode_records(output: str) -> list[dict[str, Any]]:
        text = output.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = []
            for line in text.splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    decoded.append(item)
        if isinstance(decoded, dict):
            decoded = [decoded]
        return [item for item in decoded if isinstance(item, dict)]


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class _RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if not line or len(line) > MAX_REQUEST_BYTES:
                raise BridgeError("Invalid or oversized control request")
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise BridgeError("Control request must be a JSON object")
            response = self.server.bridge.handle(request)  # type: ignore[attr-defined]
        except (BridgeError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception:
            LOGGER.exception("Unexpected collector control request failure")
            response = {"ok": False, "error": "Internal control bridge error"}
        self.wfile.write(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )


def _prepare_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not stat.S_ISSOCK(path.stat().st_mode):
            raise RuntimeError(f"Control socket path is not a socket: {path}")
        path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-dir", required=True)
    parser.add_argument("--socket", required=True, dest="socket_path")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)

    socket_path = Path(args.socket_path).resolve()
    _prepare_socket(socket_path)
    bridge = CollectorControlBridge(
        args.compose_dir,
        command_timeout_seconds=args.timeout,
    )
    server = _UnixServer(str(socket_path), _RequestHandler)
    server.bridge = bridge  # type: ignore[attr-defined]
    os.chmod(socket_path, 0o660)

    def stop_server(signum, frame) -> None:
        LOGGER.info("Stopping collector control bridge (signal %s)", signum)
        # shutdown() waits for serve_forever() to observe the flag, so it must
        # run outside the signal-handler thread to avoid a self-deadlock.
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, stop_server)
    LOGGER.info("Collector control bridge listening on %s", socket_path)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
