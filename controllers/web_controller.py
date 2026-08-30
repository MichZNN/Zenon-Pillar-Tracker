from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import sqlite3
from collections import deque
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import ceil
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from services.auth_service import (
    hash_password,
    password_is_acceptable,
    username_is_acceptable,
    verify_password,
)
from services.settings_service import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_SETTINGS,
    load_runtime_database,
    validate_settings,
)
from models.database import Database, InitialSetupAlreadyCompletedError
from services.collector_control import (
    CollectorControlClient,
    CollectorControlError,
    CollectorControlUnavailable,
)
from services.logging_service import configure_logging, read_log_tail
from functions.status import collector_status
from functions.subscriptions import normalise_subscription


BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
logger = logging.getLogger(__name__)

SESSION_COOKIE = "zenon_tracker_session"
CSRF_COOKIE = "zenon_tracker_csrf"
MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024


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


def _as_bool(value: Any, *, default: bool | None = None) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None and default is not None:
        return default
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    return None


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ZenonPillarTracker/2.0"
    client_disconnect_errors = (
        BrokenPipeError,
        ConnectionAbortedError,
        ConnectionResetError,
    )

    @property
    def database(self) -> Database:
        return self.server.database  # type: ignore[attr-defined]

    @property
    def templates_dir(self) -> Path:
        return self.server.templates_dir  # type: ignore[attr-defined]

    @property
    def static_dir(self) -> Path:
        return self.server.static_dir  # type: ignore[attr-defined]

    @property
    def api_rate_limiter(self) -> ApiRateLimiter:
        return self.server.api_rate_limiter  # type: ignore[attr-defined]

    @property
    def runtime_config(self) -> dict[str, Any]:
        return self.server.runtime_config  # type: ignore[attr-defined]

    @property
    def collector_control(self) -> CollectorControlClient:
        return self.server.collector_control  # type: ignore[attr-defined]

    def _send_json(
        self,
        payload: Any,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        cookies: list[str] | None = None,
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
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            for header, value in (headers or {}).items():
                self.send_header(header, value)
            for cookie in cookies or []:
                self.send_header("Set-Cookie", cookie)
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
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except self.client_disconnect_errors:
            return

    def _send_redirect(self, location: str, status: int = 302) -> None:
        try:
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        except self.client_disconnect_errors:
            return

    def _cookie(self, name: str) -> str | None:
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = cookies.get(name)
        return morsel.value if morsel else None

    def _session_token(self) -> str | None:
        return self._cookie(SESSION_COOKIE)

    def _current_user(self) -> dict[str, Any] | None:
        return self.database.get_session_user(self._session_token())

    def _request_ip(self) -> str:
        return str(self.client_address[0]) if self.client_address else ""

    def _cookie_flags(self, *, http_only: bool = False, max_age: int | None = None) -> str:
        flags = ["Path=/", "SameSite=Lax"]
        if http_only:
            flags.append("HttpOnly")
        if self.headers.get("X-Forwarded-Proto", "").casefold() == "https":
            flags.append("Secure")
        if max_age is not None:
            flags.append(f"Max-Age={max_age}")
        return "; ".join(flags)

    def _session_cookie(self, token: str, expires_at: str) -> str:
        return f"{SESSION_COOKIE}={token}; {self._cookie_flags(http_only=True)}"

    def _csrf_cookie(self, token: str) -> str:
        return f"{CSRF_COOKIE}={token}; {self._cookie_flags()}"

    def _expired_cookie(self, name: str) -> str:
        return f"{name}=; {self._cookie_flags(max_age=0)}"

    def _read_json_body(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if content_length < 0 or content_length > MAX_REQUEST_BODY_BYTES:
            raise ValueError("Request body is too large")
        body = self.rfile.read(content_length)
        if not body:
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must contain valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def _require_user(self, *, admin: bool = False) -> dict[str, Any] | None:
        user = self._current_user()
        if user is None:
            self._send_json({"error": "Authentication required"}, 401)
            return None
        if admin and user.get("role") != "admin":
            self._send_json({"error": "Administrator access required"}, 403)
            return None
        return user

    def _require_csrf(self, user: Mapping[str, Any]) -> bool:
        token = self._session_token()
        header = self.headers.get("X-CSRF-Token", "")
        cookie = self._cookie(CSRF_COOKIE)
        if not token or not header or cookie != header:
            self._send_json({"error": "CSRF validation failed"}, 403)
            return False
        if not self.database.verify_csrf_token(token, header):
            self._send_json({"error": "CSRF validation failed"}, 403)
            return False
        return True

    def _audit(
        self,
        user: Mapping[str, Any] | None,
        action: str,
        entity_type: str,
        entity_id: str | int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            self.database.add_audit_log(
                user_id=int(user["id"]) if user else None,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
                ip_address=self._request_ip(),
            )
        except Exception:
            logger.exception("Could not write audit entry")

    def _rate_limit(self) -> bool:
        allowed, retry_after = self.api_rate_limiter.allow(self._request_ip())
        if allowed:
            return True
        self._send_json(
            {
                "error": "Dashboard API rate limit exceeded",
                "retry_after_seconds": retry_after,
            },
            429,
            headers={"Retry-After": str(retry_after)},
        )
        return False

    def _api_auth_me(self) -> None:
        token = self._session_token()
        user = self.database.get_session_user(token)
        if user is None:
            self._send_json({"authenticated": False})
            return
        csrf_token = self.database.rotate_csrf_token(token)
        cookies = [self._csrf_cookie(csrf_token)] if csrf_token else []
        self._send_json(
            {"authenticated": True, "user": user, "csrf_token": csrf_token},
            cookies=cookies,
        )

    def _collector_control_request(
        self,
        action: str,
        *,
        tail: int = 200,
    ) -> None:
        try:
            result = self.collector_control.request(action, tail=tail)
        except CollectorControlUnavailable as exc:
            self._send_json(
                {"available": False, "error": str(exc)},
                503,
            )
            return
        except CollectorControlError as exc:
            self._send_json({"available": True, "error": str(exc)}, 502)
            return
        self._send_json({"available": True, **result})

    def _admin_collector_control(
        self,
        user: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        if not self._require_csrf(user):
            return
        action = str(payload.get("action", "")).strip().lower()
        if action not in {"start", "stop", "restart"}:
            raise ValueError("Collector action must be start, stop, or restart")
        try:
            result = self.collector_control.request(action)
        except CollectorControlUnavailable as exc:
            self._send_json(
                {"available": False, "error": str(exc)},
                503,
            )
            return
        except CollectorControlError as exc:
            self._send_json({"available": True, "error": str(exc)}, 502)
            return
        self._audit(
            user,
            f"collector_{action}",
            "collector",
            details={"status": result.get("collector", {})},
        )
        self._send_json({"available": True, **result})

    def _api_get(self, parsed) -> None:
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/api/auth/me":
            self._api_auth_me()
            return
        if path == "/api/overview":
            self._send_json(self.database.get_overview())
            return
        if path == "/api/collector-status":
            self._send_json(
                collector_status(
                    self.database.get_node_state(),
                    poll_interval_seconds=self.runtime_config.get(
                        "poll_interval_seconds", 60
                    ),
                )
            )
            return
        if path == "/api/health":
            health = self.database.get_health()
            health["collector"] = collector_status(
                health.get("node"),
                poll_interval_seconds=self.runtime_config.get(
                    "poll_interval_seconds", 60
                ),
            )
            self._send_json(health)
            return
        if path == "/api/pillars":
            status = query.get("status", [None])[0]
            search = query.get("q", query.get("search", [None]))[0]
            limit = int(query.get("limit", ["200"])[0])
            offset = int(query.get("offset", ["0"])[0])
            include_performance = _as_bool(query.get("performance", [None])[0])
            if include_performance is None:
                include_performance = True
            self._send_json(
                self.database.get_pillars(
                    status=status,
                    search=search,
                    limit=limit,
                    offset=offset,
                    include_performance=include_performance,
                )
            )
            return
        if path == "/api/performance":
            period_days = int(
                query.get("days", query.get("period_days", ["30"]))[0]
            )
            self._send_json(
                self.database.get_pillar_performance(period_days=period_days)
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
                self.database.get_epochs(int(query.get("limit", ["100"])[0]))
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
        if path == "/api/subscriptions":
            user = self._require_user()
            if user is None:
                return
            self._send_json(
                self.database.get_pillar_subscriptions(user_id=int(user["id"]))
            )
            return
        if path == "/api/admin/settings":
            user = self._require_user(admin=True)
            if user is None:
                return
            self._send_json(self.database.get_admin_settings(DEFAULT_SETTINGS))
            return
        if path == "/api/admin/users":
            if self._require_user(admin=True) is not None:
                self._send_json(self.database.list_users())
            return
        if path == "/api/admin/subscriptions":
            if self._require_user(admin=True) is not None:
                self._send_json(self.database.get_pillar_subscriptions())
            return
        if path == "/api/admin/collector-control":
            if self._require_user(admin=True) is not None:
                self._collector_control_request("status")
            return
        if path == "/api/admin/collector-logs":
            if self._require_user(admin=True) is not None:
                self._collector_control_request(
                    "logs",
                    tail=int(query.get("tail", ["200"])[0]),
                )
            return
        if path == "/api/admin/logs":
            if self._require_user(admin=True) is None:
                return
            settings = self.database.get_admin_settings(self.runtime_config)
            self._send_json(
                {
                    "file": read_log_tail(settings),
                    "audit": self.database.get_audit_log(
                        int(query.get("limit", ["100"])[0])
                    ),
                }
            )
            return
        self._send_json({"error": "API endpoint not found"}, 404)

    def _login(self, payload: Mapping[str, Any]) -> None:
        username = str(payload.get("username", "")).strip()
        password = payload.get("password", "")
        credentials = self.database.get_user_credentials(username)
        if (
            not username_is_acceptable(username)
            or not isinstance(password, str)
            or credentials is None
            or not credentials.get("active")
            or not verify_password(password, credentials.get("password_hash", ""))
        ):
            logger.warning("Failed login for username %s", username[:80])
            self._send_json({"error": "Invalid username or password"}, 401)
            return
        duration = float(
            self.database.get_setting(
                "auth_session_hours",
                self.runtime_config.get("auth_session_hours", 12),
            )
        )
        session = self.database.create_session(
            int(credentials["id"]),
            duration_hours=duration,
            ip_address=self._request_ip(),
            user_agent=self.headers.get("User-Agent", ""),
        )
        self.database.mark_user_login(int(credentials["id"]))
        user = self.database.get_user(int(credentials["id"]))
        self._audit(user, "login", "session")
        self._send_json(
            {"authenticated": True, "user": user, "csrf_token": session["csrf_token"]},
            cookies=[
                self._session_cookie(session["token"], session["expires_at"]),
                self._csrf_cookie(session["csrf_token"]),
            ],
        )

    def _setup_initial_admin(self, payload: Mapping[str, Any]) -> None:
        """Create and sign in the first administrator, once only."""
        if self.database.has_users():
            self._send_json(
                {"error": "Initial administrator setup has already been completed"},
                409,
            )
            return

        username = str(payload.get("username", "")).strip()
        display_name = str(payload.get("display_name", "")).strip()
        password = payload.get("password", "")
        confirmation = payload.get("password_confirmation", "")
        if password != confirmation:
            raise ValueError("Passwords do not match")
        if not password_is_acceptable(password):
            raise ValueError("Password must contain at least 12 characters")
        if not username_is_acceptable(username):
            raise ValueError(
                "Username must be 3-80 characters using letters, numbers, ., _ or -"
            )

        try:
            created = self.database.create_initial_admin(
                username=username,
                display_name=display_name,
                password_hash=hash_password(password),
            )
        except InitialSetupAlreadyCompletedError:
            self._send_json(
                {"error": "Initial administrator setup has already been completed"},
                409,
            )
            return

        duration = float(
            self.database.get_setting(
                "auth_session_hours",
                self.runtime_config.get("auth_session_hours", 12),
            )
        )
        session = self.database.create_session(
            int(created["id"]),
            duration_hours=duration,
            ip_address=self._request_ip(),
            user_agent=self.headers.get("User-Agent", ""),
        )
        user = self.database.get_user(int(created["id"]))
        self._audit(
            user,
            "create",
            "user",
            created["id"],
            {"role": "admin", "initial_setup": True},
        )
        self._send_json(
            {
                "authenticated": True,
                "user": user,
                "csrf_token": session["csrf_token"],
            },
            201,
            cookies=[
                self._session_cookie(session["token"], session["expires_at"]),
                self._csrf_cookie(session["csrf_token"]),
            ],
        )

    def _logout(self, user: Mapping[str, Any] | None) -> None:
        if user is not None:
            if not self._require_csrf(user):
                return
            self._audit(user, "logout", "session")
        self.database.delete_session(self._session_token())
        self._send_json(
            {"authenticated": False},
            cookies=[
                self._expired_cookie(SESSION_COOKIE),
                self._expired_cookie(CSRF_COOKIE),
            ],
        )

    def _admin_settings_update(
        self,
        user: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        if not self._require_csrf(user):
            return
        settings = payload.get("settings", payload)
        if not isinstance(settings, Mapping):
            raise ValueError("settings must be a JSON object")
        validate_settings(settings)
        updated = self.database.set_settings(
            settings,
            updated_by=int(user["id"]),
        )
        self.runtime_config.update(updated)
        self._audit(
            user,
            "update",
            "settings",
            details={"keys": sorted(str(key) for key in settings)},
        )
        logging_status = configure_logging(self.runtime_config)
        self._send_json(
            {
                "settings": self.database.get_admin_settings(DEFAULT_SETTINGS),
                "logging": logging_status,
            }
        )

    def _admin_user_create(
        self,
        user: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        if not self._require_csrf(user):
            return
        username = str(payload.get("username", "")).strip()
        if not username_is_acceptable(username):
            raise ValueError(
                "Username must be 3-80 characters using letters, numbers, ., _ or -"
            )
        password = payload.get("password", "")
        if not password_is_acceptable(password):
            raise ValueError("Password must contain at least 12 characters")
        role = str(payload.get("role", "user")).strip().lower()
        active = _as_bool(payload.get("active"), default=True)
        if active is None:
            raise ValueError("active must be a boolean")
        created = self.database.create_user(
            username=username,
            password_hash=hash_password(password),
            role=role,
            display_name=str(payload.get("display_name", "")),
            active=active,
        )
        self._audit(user, "create", "user", created["id"], {"role": role})
        self._send_json(created, 201)

    def _admin_user_update(
        self,
        user: Mapping[str, Any],
        user_id: int,
        payload: Mapping[str, Any],
    ) -> None:
        if not self._require_csrf(user):
            return
        password = payload.get("password")
        password_hash = None
        if password is not None:
            if not password_is_acceptable(password):
                raise ValueError("Password must contain at least 12 characters")
            password_hash = hash_password(password)
        role = payload.get("role")
        active = None
        if "active" in payload:
            active = _as_bool(payload.get("active"))
            if active is None:
                raise ValueError("active must be a boolean")
        updated = self.database.update_user(
            user_id,
            display_name=(
                str(payload["display_name"])
                if "display_name" in payload
                else None
            ),
            role=str(role) if role is not None else None,
            active=active,
            password_hash=password_hash,
        )
        self._audit(user, "update", "user", user_id, {"fields": sorted(payload)})
        self._send_json(updated)

    def _account_update(
        self,
        user: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        """Update the signed-in user's profile without exposing password data."""
        if not self._require_csrf(user):
            return

        has_display_name = "display_name" in payload
        display_name: str | None = None
        if has_display_name:
            raw_display_name = payload.get("display_name")
            if not isinstance(raw_display_name, str):
                raise ValueError("Display name must be text")
            display_name = raw_display_name.strip()
            if len(display_name) > 120:
                raise ValueError("Display name must be 120 characters or fewer")

        current_password = payload.get("current_password", "")
        new_password = payload.get("new_password", "")
        confirmation = payload.get("new_password_confirmation", "")
        if not isinstance(current_password, str):
            raise ValueError("Current password is invalid")
        if not isinstance(new_password, str) or not isinstance(confirmation, str):
            raise ValueError("New password fields are invalid")
        if new_password != confirmation:
            raise ValueError("New passwords do not match")

        password_hash: str | None = None
        if new_password:
            if not password_is_acceptable(new_password):
                raise ValueError("Password must contain at least 12 characters")
            if not current_password:
                raise ValueError("Current password is required to change your password")
            credentials = self.database.get_user_credentials(str(user["username"]))
            if (
                credentials is None
                or not verify_password(current_password, credentials.get("password_hash", ""))
            ):
                raise ValueError("Current password is incorrect")
            password_hash = hash_password(new_password)

        if not has_display_name and password_hash is None:
            raise ValueError("No account changes provided")

        updated = self.database.update_user(
            int(user["id"]),
            display_name=display_name,
            password_hash=password_hash,
        )
        changed_fields = []
        if has_display_name:
            changed_fields.append("display_name")
        if password_hash is not None:
            changed_fields.append("password")
        self._audit(
            user,
            "update",
            "user",
            int(user["id"]),
            {"fields": changed_fields},
        )
        self._send_json(updated)

    def _subscription_for_write(
        self,
        user: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        current: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], int | None | object]:
        merged = dict(current or {})
        merged.update(
            {
                key: payload[key]
                for key in (
                    "channel_id",
                    "discord_webhook",
                    "pillar_owner_addresses",
                    "events",
                    "label",
                    "active",
                )
                if key in payload
            }
        )
        normalised = normalise_subscription(merged)
        is_admin = user.get("role") == "admin"
        if "owner_user_id" in payload:
            if not is_admin:
                raise PermissionError("Only administrators can reassign subscriptions")
            raw_owner = payload.get("owner_user_id")
            owner_id = None if raw_owner in (None, "") else int(raw_owner)
            if owner_id is not None and self.database.get_user(owner_id) is None:
                raise ValueError("Subscription owner not found")
            return normalised, owner_id
        if current is None:
            return normalised, int(user["id"])
        if not is_admin and current.get("user_id") != int(user["id"]):
            raise PermissionError("This subscription belongs to another user")
        return normalised, _UNSET

    def _subscription_create(
        self,
        user: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        if not self._require_csrf(user):
            return
        normalised, owner_id = self._subscription_for_write(user, payload)
        if owner_id is _UNSET:
            owner_id = int(user["id"])
        created = self.database.create_pillar_subscription(
            user_id=owner_id,  # type: ignore[arg-type]
            channel_id=normalised["channel_id"],
            discord_webhook=normalised["discord_webhook"],
            pillar_owner_addresses=normalised["pillar_owner_addresses"],
            events=normalised["events"],
            label=normalised["label"],
            active=normalised["active"],
            changed_by=int(user["id"]),
        )
        self._audit(user, "create", "subscription", created["id"])
        self._send_json(created, 201)

    def _subscription_update(
        self,
        user: Mapping[str, Any],
        subscription_id: int,
        payload: Mapping[str, Any],
    ) -> None:
        if not self._require_csrf(user):
            return
        current = self.database.get_pillar_subscription(subscription_id)
        if current is None:
            self._send_json({"error": "Subscription not found"}, 404)
            return
        normalised, owner_id = self._subscription_for_write(
            user,
            payload,
            current=current,
        )
        if user.get("role") != "admin" and current.get("user_id") != int(user["id"]):
            raise PermissionError("This subscription belongs to another user")
        update_kwargs = {
            "channel_id": normalised["channel_id"],
            "discord_webhook": normalised["discord_webhook"],
            "pillar_owner_addresses": normalised["pillar_owner_addresses"],
            "events": normalised["events"],
            "label": normalised["label"],
            "active": normalised["active"],
            "changed_by": int(user["id"]),
        }
        if owner_id is not _UNSET:
            update_kwargs["user_id"] = owner_id
        updated = self.database.update_pillar_subscription(
            subscription_id,
            **update_kwargs,
        )
        self._audit(user, "update", "subscription", subscription_id)
        self._send_json(updated)

    def _api_write(self, method: str, parsed) -> None:
        path = parsed.path.rstrip("/") or "/"
        payload = self._read_json_body()

        if path == "/api/setup/admin" and method == "POST":
            self._setup_initial_admin(payload)
            return
        if path == "/api/auth/login" and method == "POST":
            self._login(payload)
            return
        if path == "/api/auth/logout" and method == "POST":
            self._logout(self._current_user())
            return

        if path == "/api/admin/settings" and method in {"POST", "PUT", "PATCH"}:
            user = self._require_user(admin=True)
            if user is not None:
                self._admin_settings_update(user, payload)
            return
        if path == "/api/admin/collector-control" and method == "POST":
            user = self._require_user(admin=True)
            if user is not None:
                self._admin_collector_control(user, payload)
            return
        if path == "/api/admin/users" and method == "POST":
            user = self._require_user(admin=True)
            if user is not None:
                self._admin_user_create(user, payload)
            return
        if path.startswith("/api/admin/users/") and method == "PATCH":
            user = self._require_user(admin=True)
            if user is not None:
                user_id = int(path.rsplit("/", 1)[1])
                self._admin_user_update(user, user_id, payload)
            return
        if path == "/api/account" and method == "PATCH":
            user = self._require_user()
            if user is not None:
                self._account_update(user, payload)
            return
        if path == "/api/subscriptions" and method == "POST":
            user = self._require_user()
            if user is not None:
                self._subscription_create(user, payload)
            return
        if path.startswith("/api/subscriptions/") and method == "PATCH":
            user = self._require_user()
            if user is not None:
                subscription_id = int(path.rsplit("/", 1)[1])
                self._subscription_update(user, subscription_id, payload)
            return
        if path == "/api/admin/subscriptions" and method == "POST":
            user = self._require_user(admin=True)
            if user is not None:
                self._subscription_create(user, payload)
            return
        if path.startswith("/api/admin/subscriptions/") and method == "PATCH":
            user = self._require_user(admin=True)
            if user is not None:
                subscription_id = int(path.rsplit("/", 1)[1])
                self._subscription_update(user, subscription_id, payload)
            return
        self._send_json({"error": "API endpoint not found"}, 404)

    def _dispatch_api(self, method: str, parsed) -> None:
        if not self._rate_limit():
            return
        if method == "GET":
            self._api_get(parsed)
            return
        self._api_write(method, parsed)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/.well-known/appspecific/com.chrome.devtools.json":
                # Chrome probes this optional endpoint while DevTools is open.
                # It is not an application error and should not fill the log.
                self._send_json({})
                return
            if parsed.path.startswith("/api/"):
                self._dispatch_api("GET", parsed)
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
            if parsed.path == "/favicon.ico":
                self._send_file(self.static_dir / "icons" / "favicon.ico")
                return
            if not self.database.has_users() and parsed.path in {
                "/portal",
                "/portal.html",
                "/admin",
                "/account",
                "/login",
                "/login.html",
            }:
                self._send_redirect("/setup")
                return

            requested = parsed.path
            aliases = {
                "/portal": "/portal.html",
                # Keep old bookmarks working, but serve the same role-aware
                # portal rather than separate account/admin pages.
                "/admin": "/portal.html",
                "/account": "/portal.html",
                "/login": "/login.html",
            }
            requested = aliases.get(requested, requested)
            if requested in {"/setup", "/setup.html"}:
                if self.database.has_users():
                    self._send_redirect(
                        "/portal" if self._current_user() else "/login"
                    )
                    return
                requested = "/setup.html"
            if requested in {"", "/"}:
                requested = "/index.html"
            relative = requested.lstrip("/")
            root = self.templates_dir.resolve()
            candidate = (root / relative).resolve()
            if candidate != root and root not in candidate.parents:
                self._send_json({"error": "Forbidden"}, 403)
                return
            self._send_file(candidate)
        except self.client_disconnect_errors:
            return
        except (ValueError, TypeError) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception:
            logger.exception("Web GET request failed")
            self._send_json({"error": "Internal server error"}, 500)

    def _do_write(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self._send_json({"error": "Method not allowed"}, 405)
                return
            self._dispatch_api(method, parsed)
        except self.client_disconnect_errors:
            return
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, 403)
        except (ValueError, TypeError, KeyError, sqlite3.IntegrityError) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception:
            logger.exception("Web %s request failed", method)
            self._send_json({"error": "Internal server error"}, 500)

    def do_POST(self) -> None:
        self._do_write("POST")

    def do_PUT(self) -> None:
        self._do_write("PUT")

    def do_PATCH(self) -> None:
        self._do_write("PATCH")

    def do_DELETE(self) -> None:
        self._send_json(
            {"error": "Delete is intentionally disabled; deactivate the record instead"},
            405,
        )

    def log_message(self, format: str, *args: Any) -> None:
        """Keep access logging focused on useful operational events.

        The standard HTTP server calls this for every request, including
        successful requests for JavaScript, CSS, icons, and public dashboard
        data. Those reads quickly obscure the application errors we need to
        investigate, so only failed requests and successful write requests
        are sent to the application log.
        """
        request_line = str(args[0]) if args else ""
        method = request_line.split(" ", 1)[0].upper() if request_line else ""
        try:
            status = int(args[1]) if len(args) > 1 else 0
        except (TypeError, ValueError):
            status = 0
        if status >= 500:
            logger.error("%s - %s", self.address_string(), format % args)
        elif status >= 400:
            logger.warning("%s - %s", self.address_string(), format % args)
        elif method not in {"GET", "HEAD"}:
            logger.info("%s - %s", self.address_string(), format % args)


class DashboardServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        address,
        handler,
        database: Database,
        templates_dir: Path,
        static_dir: Path,
        api_rate_limiter: ApiRateLimiter,
        runtime_config: Mapping[str, Any] | None = None,
        collector_control: CollectorControlClient | None = None,
    ):
        super().__init__(address, handler)
        self.database = database
        self.templates_dir = templates_dir
        self.static_dir = static_dir
        self.api_rate_limiter = api_rate_limiter
        self.runtime_config = dict(runtime_config or {})
        self.collector_control = collector_control or CollectorControlClient()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve the Zenon Pillar Tracker dashboard and admin panel."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="SQLite file path",
    )
    parser.add_argument(
        "--templates-dir",
        "--web-dir",
        dest="templates_dir",
        default=str(TEMPLATES_DIR),
        help="HTML template directory (legacy alias: --web-dir)",
    )
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
    templates_dir = Path(args.templates_dir)
    if not templates_dir.is_absolute():
        templates_dir = BASE_DIR / templates_dir
    if not (templates_dir / "index.html").exists():
        raise FileNotFoundError(f"Dashboard templates not found in {templates_dir}")
    if not STATIC_DIR.exists():
        raise FileNotFoundError(f"Static asset directory not found in {STATIC_DIR}")

    try:
        runtime_config, database = load_runtime_database(database_path)
        configure_logging(runtime_config)
    except Exception as exc:
        # Keep failures that happen while opening the database/configuration
        # visible in the mounted default log and in Docker stderr.
        configure_logging()
        logger.exception("Dashboard did not start: %s", exc)
        return 1
    api_rate_limiter = ApiRateLimiter(
        max_requests=args.api_rate_limit,
        window_seconds=args.api_rate_window,
    )
    server = DashboardServer(
        (args.host, args.port),
        DashboardHandler,
        database,
        templates_dir,
        STATIC_DIR,
        api_rate_limiter,
        runtime_config,
    )
    logger.info("Dashboard listening on http://%s:%s", args.host, args.port)
    if args.api_rate_limit:
        logger.info(
            "Dashboard API rate limit: %s requests/%ss per client",
            args.api_rate_limit,
            args.api_rate_window,
        )
    else:
        logger.info("Dashboard API rate limit: disabled")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Dashboard stopped.")
    finally:
        server.server_close()
    return 0


_UNSET = object()


if __name__ == "__main__":
    raise SystemExit(main())
