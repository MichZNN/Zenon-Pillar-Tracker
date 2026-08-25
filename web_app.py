from __future__ import annotations

import argparse
import json
import mimetypes
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import ceil
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from database import Database


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
STATIC_DIR = BASE_DIR / "static"


class ApiRateLimiter:
    """Thread-safe per-client sliding-window rate limiter."""

    def __init__(self, max_requests: int = 120, window_seconds: float = 60):
        self.max_requests = max(0, int(max_requests))
        self.window_seconds = float(window_seconds)
        if self.window_seconds <= 0:
            raise ValueError("API rate-limit window must be greater than zero")
        self._requests: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, client_key: str) -> tuple[bool, int]:
        """Return whether a request is allowed and its retry delay."""
        if self.max_requests == 0:
            return True, 0

        now = monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._requests.setdefault(client_key, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= self.max_requests:
                retry_after = max(
                    1,
                    ceil(timestamps[0] + self.window_seconds - now),
                )
                return False, retry_after

            timestamps.append(now)
            return True, 0


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ZenonPillarTracker/1.0"
    client_disconnect_errors = (
        BrokenPipeError,
        ConnectionAbortedError,
        ConnectionResetError,
    )

    @property
    def database(self) -> Database:
        return self.server.database  # type: ignore[attr-defined]

    @property
    def web_dir(self) -> Path:
        return self.server.web_dir  # type: ignore[attr-defined]

    @property
    def static_dir(self) -> Path:
        return self.server.static_dir  # type: ignore[attr-defined]

    @property
    def api_rate_limiter(self) -> ApiRateLimiter:
        return self.server.api_rate_limiter  # type: ignore[attr-defined]

    def _send_json(
        self,
        payload: Any,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for header, value in (headers or {}).items():
                self.send_header(header, value)
            self.end_headers()
            self.wfile.write(body)
        except self.client_disconnect_errors:
            return

    def _send_file(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self._send_json({"error": "Not found"}, 404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except self.client_disconnect_errors:
            return

    def _api(self, parsed) -> None:
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/api/overview":
            self._send_json(self.database.get_overview())
            return
        if path == "/api/health":
            self._send_json(self.database.get_health())
            return
        if path == "/api/pillars":
            status = query.get("status", [None])[0]
            search = query.get("q", query.get("search", [None]))[0]
            limit = int(query.get("limit", ["200"])[0])
            offset = int(query.get("offset", ["0"])[0])
            self._send_json(
                self.database.get_pillars(
                    status=status,
                    search=search,
                    limit=limit,
                    offset=offset,
                )
            )
            return
        if path.startswith("/api/pillars/"):
            owner_address = unquote(path.split("/api/pillars/", 1)[1])
            pillar = self.database.get_pillar(owner_address)
            if pillar is None:
                self._send_json({"error": "Pillar not found"}, 404)
            else:
                self._send_json(pillar)
            return
        if path == "/api/epochs":
            self._send_json(
                self.database.get_epochs(
                    int(query.get("limit", ["100"])[0])
                )
            )
            return
        if path == "/api/events":
            self._send_json(
                self.database.get_events(
                    limit=int(query.get("limit", ["100"])[0]),
                    event_type=query.get("type", [None])[0],
                )
            )
            return
        self._send_json({"error": "API endpoint not found"}, 404)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                allowed, retry_after = self.api_rate_limiter.allow(
                    str(self.client_address[0])
                )
                if not allowed:
                    self._send_json(
                        {
                            "error": "Dashboard API rate limit exceeded",
                            "retry_after_seconds": retry_after,
                        },
                        429,
                        headers={"Retry-After": str(retry_after)},
                    )
                    return
                self._api(parsed)
                return
            if parsed.path.startswith("/static/"):
                relative = unquote(parsed.path.removeprefix("/static/")).lstrip("/")
                root = self.static_dir.resolve()
                candidate = (root / relative).resolve()
                if candidate != root and root not in candidate.parents:
                    self._send_json({"error": "Forbidden"}, 403)
                    return
                self._send_file(candidate)
                return
            requested = parsed.path
            if requested in {"", "/"}:
                requested = "/index.html"
            relative = requested.lstrip("/")
            root = self.web_dir.resolve()
            candidate = (root / relative).resolve()
            if candidate != root and root not in candidate.parents:
                self._send_json({"error": "Forbidden"}, 403)
                return
            self._send_file(candidate)
        except self.client_disconnect_errors:
            return
        except (ValueError, TypeError) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
            print(f"Web request failed: {exc}")
            self._send_json({"error": "Internal server error"}, 500)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} - {format % args}")


class DashboardServer(ThreadingHTTPServer):
    def __init__(
        self,
        address,
        handler,
        database: Database,
        web_dir: Path,
        static_dir: Path,
        api_rate_limiter: ApiRateLimiter,
    ):
        super().__init__(address, handler)
        self.database = database
        self.web_dir = web_dir
        self.static_dir = static_dir
        self.api_rate_limiter = api_rate_limiter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve the Zenon Pillar Tracker dashboard."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--database",
        default="data_store/pillar_tracker.sqlite3",
    )
    parser.add_argument("--web-dir", default=str(WEB_DIR))
    parser.add_argument(
        "--api-rate-limit",
        type=int,
        default=120,
        help=(
            "Maximum dashboard API requests per client per window; "
            "use 0 to disable (default: 120)"
        ),
    )
    parser.add_argument(
        "--api-rate-window",
        type=float,
        default=60,
        help="Dashboard API rate-limit window in seconds (default: 60)",
    )
    args = parser.parse_args(argv)
    if args.api_rate_limit < 0:
        parser.error("--api-rate-limit cannot be negative")
    if args.api_rate_window <= 0:
        parser.error("--api-rate-window must be greater than zero")

    database_path = Path(args.database)
    if not database_path.is_absolute():
        database_path = BASE_DIR / database_path
    web_dir = Path(args.web_dir)
    if not web_dir.is_absolute():
        web_dir = BASE_DIR / web_dir
    if not (web_dir / "index.html").exists():
        raise FileNotFoundError(f"Dashboard files not found in {web_dir}")
    static_dir = STATIC_DIR
    if not static_dir.exists():
        raise FileNotFoundError(f"Static asset directory not found in {static_dir}")

    database = Database(database_path)
    api_rate_limiter = ApiRateLimiter(
        max_requests=args.api_rate_limit,
        window_seconds=args.api_rate_window,
    )
    server = DashboardServer(
        (args.host, args.port),
        DashboardHandler,
        database,
        web_dir,
        static_dir,
        api_rate_limiter,
    )
    print(f"Dashboard listening on http://{args.host}:{args.port}")
    if args.api_rate_limit:
        print(
            "Dashboard API rate limit: "
            f"{args.api_rate_limit} requests/{args.api_rate_window:g}s per client"
        )
    else:
        print("Dashboard API rate limit: disabled")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
