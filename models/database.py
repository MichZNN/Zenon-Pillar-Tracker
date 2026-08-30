from __future__ import annotations

import json
import sqlite3
from secrets import token_urlsafe
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "8"

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'stale', 'failed', 'reorg')),
    momentum_height INTEGER,
    momentum_hash TEXT,
    momentum_timestamp INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS node_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_momentum_height INTEGER,
    last_momentum_hash TEXT,
    last_momentum_timestamp INTEGER,
    last_success_at TEXT,
    stale_count INTEGER NOT NULL DEFAULT 0,
    health TEXT NOT NULL DEFAULT 'unknown'
        CHECK (health IN ('unknown', 'healthy', 'stale', 'error', 'reorg')),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS epochs (
    epoch INTEGER PRIMARY KEY,
    znn_reward INTEGER NOT NULL DEFAULT 0,
    qsr_reward INTEGER NOT NULL DEFAULT 0,
    source_address TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_observed_momentum_height INTEGER,
    source TEXT NOT NULL DEFAULT 'node',
    epoch_start_at TEXT,
    epoch_start_inferred INTEGER NOT NULL DEFAULT 1,
    announcement_at TEXT,
    announcement_source TEXT,
    announcement_inferred INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pillars (
    owner_address TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    live_since TEXT NOT NULL,
    last_seen_at TEXT,
    dismantled_at TEXT,
    name TEXT NOT NULL,
    rank INTEGER,
    weight INTEGER,
    produced_momentums INTEGER,
    expected_momentums INTEGER,
    momentum_reward_percentage INTEGER,
    delegate_reward_percentage INTEGER,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'dismantled', 'unknown')),
    status_since TEXT NOT NULL,
    missed_momentums INTEGER NOT NULL DEFAULT 0,
    is_present INTEGER NOT NULL DEFAULT 1 CHECK (is_present IN (0, 1)),
    last_seen_epoch INTEGER,
    last_seen_momentum_height INTEGER,
    raw_payload TEXT
);

CREATE TABLE IF NOT EXISTS pillar_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_run_id INTEGER NOT NULL REFERENCES poll_runs(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    momentum_height INTEGER NOT NULL,
    epoch INTEGER,
    owner_address TEXT NOT NULL,
    name TEXT NOT NULL,
    rank INTEGER,
    weight INTEGER,
    produced_momentums INTEGER,
    expected_momentums INTEGER,
    momentum_reward_percentage INTEGER,
    delegate_reward_percentage INTEGER,
    status TEXT NOT NULL,
    missed_momentums INTEGER NOT NULL DEFAULT 0,
    raw_payload TEXT,
    UNIQUE (poll_run_id, owner_address)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    owner_address TEXT,
    epoch INTEGER,
    observed_at TEXT NOT NULL,
    momentum_height INTEGER,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    sent_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (event_id, channel)
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user'
        CHECK (role IN ('admin', 'user')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS pillar_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    label TEXT NOT NULL DEFAULT '',
    channel_id TEXT NOT NULL,
    discord_webhook TEXT NOT NULL DEFAULT '',
    pillar_owner_addresses_json TEXT NOT NULL DEFAULT '[]',
    events_json TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    ip_address TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_poll_runs_started_at
    ON poll_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_owner_time
    ON pillar_snapshots(owner_address, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_performance
    ON pillar_snapshots(
        owner_address,
        observed_at,
        id,
        epoch,
        produced_momentums,
        expected_momentums
    );
CREATE INDEX IF NOT EXISTS idx_events_time
    ON events(observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_owner_time
    ON events(owner_address, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_status
    ON notifications(status, last_attempt_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user_expires
    ON sessions(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user_active
    ON pillar_subscriptions(user_id, active);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
    ON audit_log(created_at DESC, id DESC);

INSERT OR IGNORE INTO node_state (id, updated_at)
VALUES (1, '1970-01-01T00:00:00+00:00');
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _load_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _live_seconds(start: str | None, end: str | None = None) -> int | None:
    started = _parse_datetime(start)
    if started is None:
        return None
    finished = _parse_datetime(end) or datetime.now(timezone.utc)
    return max(0, int((finished - started).total_seconds()))


def _duration_text(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _empty_performance(period_days: int = 30) -> dict[str, Any]:
    return {
        "period_days": period_days,
        "produced": 0,
        "expected": 0,
        "percentage": None,
        "observations": 0,
        "intervals": 0,
        "daily": [],
    }


DEFAULT_SUBSCRIPTION_EVENTS = (
    "pillar_inactive",
    "pillar_active",
    "reward_shares_changed",
)
_UNSET = object()


class InitialSetupAlreadyCompletedError(RuntimeError):
    """Raised when a second first-admin setup is attempted."""


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


class _ManagedConnection(sqlite3.Connection):
    """Close short-lived SQLite connections after their context exits."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def initialize_database(database_path: str | Path) -> Path:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.executescript(SCHEMA)
        epoch_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(epochs)").fetchall()
        }
        if "announcement_at" not in epoch_columns:
            connection.execute(
                "ALTER TABLE epochs ADD COLUMN announcement_at TEXT"
            )
        if "announcement_source" not in epoch_columns:
            connection.execute(
                "ALTER TABLE epochs ADD COLUMN announcement_source TEXT"
            )
        if "announcement_inferred" not in epoch_columns:
            connection.execute(
                "ALTER TABLE epochs ADD COLUMN announcement_inferred "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "epoch_start_at" not in epoch_columns:
            connection.execute(
                "ALTER TABLE epochs ADD COLUMN epoch_start_at TEXT"
            )
        if "epoch_start_inferred" not in epoch_columns:
            connection.execute(
                "ALTER TABLE epochs ADD COLUMN epoch_start_inferred "
                "INTEGER NOT NULL DEFAULT 1"
            )
        subscription_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(pillar_subscriptions)"
            ).fetchall()
        }
        if "discord_webhook" not in subscription_columns:
            connection.execute(
                "ALTER TABLE pillar_subscriptions ADD COLUMN "
                "discord_webhook TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """
            INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (SCHEMA_VERSION,),
        )
        connection.commit()
    finally:
        connection.close()
    return path


class Database:
    def __init__(self, database_path: str | Path):
        self.path = initialize_database(database_path)
        self._performance_cache: dict[
            tuple[int, int], dict[str, dict[str, Any]]
        ] = {}
        self._performance_cache_lock = Lock()

    def ensure_settings(self, defaults: Mapping[str, Any]) -> None:
        """Persist code defaults once without replacing administrator values."""
        now = utc_now()
        with self._connect() as connection:
            for raw_key, value in defaults.items():
                key = str(raw_key).strip()
                if not key or key in {"database_path", "telegram_pillar_subscriptions"}:
                    continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO app_settings(key, value_json, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (key, _json(value), now),
                )

    def get_settings(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value_json FROM app_settings ORDER BY key"
            ).fetchall()
        return {
            str(row["key"]): _load_json(row["value_json"])
            for row in rows
        }

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                (str(key),),
            ).fetchone()
        return _load_json(row["value_json"], default) if row else default

    def get_admin_settings(
        self,
        defaults: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = dict(defaults or {})
        settings.update(self.get_settings())
        # Hide the obsolete configurable path from the admin API. Existing
        # databases can retain the row, but logging no longer reads it.
        settings.pop("log_path", None)
        # The database path is bootstrap metadata, not an editable runtime
        # setting. It is useful in the admin UI, but must not be moved by an
        # HTTP request while this process is using the current database.
        settings["database_path"] = str(self.path)
        return settings

    def has_users(self) -> bool:
        """Return whether the database already contains an account."""
        with self._connect() as connection:
            row = connection.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None

    def set_settings(
        self,
        settings: Mapping[str, Any],
        *,
        updated_by: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(settings, Mapping):
            raise ValueError("Settings must be a JSON object")
        protected = {
            "database_path",
            "telegram_bot_api_key",
            "telegram_bot_token",
            "telegram_pillar_subscriptions",
            "log_path",
        }
        now = utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for raw_key, value in settings.items():
                key = str(raw_key).strip()
                if not key:
                    raise ValueError("Setting names cannot be empty")
                if key in protected:
                    raise ValueError(f"Setting cannot be changed here: {key}")
                try:
                    # Settings edited through the API must stay real JSON
                    # values; do not silently stringify unsupported objects.
                    value_json = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Setting {key} is not JSON serializable") from exc
                connection.execute(
                    """
                    INSERT INTO app_settings(key, value_json, updated_at, updated_by)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json = excluded.value_json,
                        updated_at = excluded.updated_at,
                        updated_by = excluded.updated_by
                    """,
                    (key, value_json, now, updated_by),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_settings()

    @staticmethod
    def _user_json(row: Mapping[str, Any]) -> dict[str, Any]:
        available = set(row.keys()) if hasattr(row, "keys") else set(row)
        result = {
            key: row[key]
            for key in (
                "id",
                "username",
                "display_name",
                "role",
                "active",
                "created_at",
                "updated_at",
                "last_login_at",
            )
            if key in available
        }
        result["active"] = bool(result.get("active"))
        if "subscription_count" in available:
            result["subscription_count"] = int(row["subscription_count"] or 0)
        return result

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = "user",
        display_name: str = "",
        active: bool = True,
    ) -> dict[str, Any]:
        username = str(username).strip()
        role = str(role).strip().lower()
        from services.auth_service import username_is_acceptable

        if not username_is_acceptable(username):
            raise ValueError(
                "Username must be 3-80 characters using letters, numbers, ., _ or -"
            )
        if role not in {"admin", "user"}:
            raise ValueError("Role must be admin or user")
        if not password_hash:
            raise ValueError("Password hash is required")
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users(
                    username, display_name, password_hash, role, active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    str(display_name).strip(),
                    password_hash,
                    role,
                    1 if active else 0,
                    now,
                    now,
                ),
            )
            user_id = int(cursor.lastrowid)
        return self.get_user(user_id)  # type: ignore[return-value]

    def create_initial_admin(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str = "",
    ) -> dict[str, Any]:
        """Atomically create the only account allowed during first-run setup."""
        username = str(username).strip()
        from services.auth_service import username_is_acceptable

        if not username_is_acceptable(username):
            raise ValueError(
                "Username must be 3-80 characters using letters, numbers, ., _ or -"
            )
        if not password_hash:
            raise ValueError("Password hash is required")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise InitialSetupAlreadyCompletedError(
                    "Initial administrator setup has already been completed"
                )
            now = utc_now()
            cursor = connection.execute(
                """
                INSERT INTO users(
                    username, display_name, password_hash, role, active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'admin', 1, ?, ?)
                """,
                (username, str(display_name).strip(), password_hash, now, now),
            )
            user_id = int(cursor.lastrowid)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_user(user_id)  # type: ignore[return-value]

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT u.*, COUNT(ps.id) AS subscription_count
                FROM users u
                LEFT JOIN pillar_subscriptions ps ON ps.user_id = u.id
                WHERE u.id = ?
                GROUP BY u.id
                """,
                (int(user_id),),
            ).fetchone()
        return self._user_json(row) if row else None

    def get_user_credentials(self, username: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (str(username).strip(),),
            ).fetchone()
        return dict(row) if row else None

    def list_users(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        clause = "" if include_inactive else "WHERE u.active = 1"
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT u.*, COUNT(ps.id) AS subscription_count
                FROM users u
                LEFT JOIN pillar_subscriptions ps ON ps.user_id = u.id
                {clause}
                GROUP BY u.id
                ORDER BY u.role DESC, u.username COLLATE NOCASE
                """
            ).fetchall()
        return [self._user_json(row) for row in rows]

    def count_active_admins(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND active = 1"
            ).fetchone()
        return int(row["count"] or 0)

    def update_user(
        self,
        user_id: int,
        *,
        display_name: str | None = None,
        role: str | None = None,
        active: bool | None = None,
        password_hash: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_user(int(user_id))
        if current is None:
            raise ValueError("User not found")
        changes: dict[str, Any] = {}
        if display_name is not None:
            changes["display_name"] = str(display_name).strip()
        if role is not None:
            role = str(role).strip().lower()
            if role not in {"admin", "user"}:
                raise ValueError("Role must be admin or user")
            changes["role"] = role
        if active is not None:
            changes["active"] = 1 if active else 0
        if password_hash is not None:
            if not password_hash:
                raise ValueError("Password hash is required")
            changes["password_hash"] = password_hash
        if not changes:
            return current

        will_remove_admin = (
            current["role"] == "admin"
            and current["active"]
            and (
                changes.get("role", "admin") != "admin"
                or changes.get("active", 1) != 1
            )
        )
        if will_remove_admin and self.count_active_admins() <= 1:
            raise ValueError("At least one active administrator is required")

        assignments = list(changes)
        assignments.append("updated_at")
        values = [changes[key] for key in changes]
        values.extend([utc_now(), int(user_id)])
        with self._connect() as connection:
            connection.execute(
                f"UPDATE users SET {', '.join(f'{key} = ?' for key in assignments)} "
                "WHERE id = ?",
                values,
            )
        return self.get_user(int(user_id))  # type: ignore[return-value]

    def mark_user_login(self, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (utc_now(), utc_now(), int(user_id)),
            )

    def create_session(
        self,
        user_id: int,
        *,
        duration_hours: float = 12,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        if duration_hours <= 0:
            raise ValueError("Session duration must be greater than zero")
        user = self.get_user(int(user_id))
        if user is None or not user["active"]:
            raise ValueError("User is not active")
        from services.auth_service import hash_token

        token = token_urlsafe(48)
        csrf_token = token_urlsafe(32)
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(hours=float(duration_hours))
        created_text = created_at.isoformat(timespec="seconds")
        expires_text = expires_at.isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE expires_at <= ?",
                (created_text,),
            )
            connection.execute(
                """
                INSERT INTO sessions(
                    token_hash, user_id, csrf_token_hash, created_at,
                    expires_at, last_seen_at, ip_address, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hash_token(token),
                    int(user_id),
                    hash_token(csrf_token),
                    created_text,
                    expires_text,
                    created_text,
                    str(ip_address or "")[:255],
                    str(user_agent or "")[:500],
                ),
            )
        return {
            "token": token,
            "csrf_token": csrf_token,
            "expires_at": expires_text,
        }

    def get_session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        from services.auth_service import hash_token

        now = utc_now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT u.*
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                  AND s.expires_at > ?
                  AND u.active = 1
                """,
                (hash_token(token), now),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (now, hash_token(token)),
                )
        return self._user_json(row) if row else None

    def rotate_csrf_token(self, token: str | None) -> str | None:
        if not token:
            return None
        from services.auth_service import hash_token

        csrf_token = token_urlsafe(32)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET csrf_token_hash = ?
                WHERE token_hash = ? AND expires_at > ?
                """,
                (hash_token(csrf_token), hash_token(token), utc_now()),
            )
        return csrf_token if cursor.rowcount else None

    def verify_csrf_token(self, token: str | None, csrf_token: str | None) -> bool:
        if not token or not csrf_token:
            return False
        from services.auth_service import hash_token

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT csrf_token_hash
                FROM sessions
                WHERE token_hash = ? AND expires_at > ?
                """,
                (hash_token(token), utc_now()),
            ).fetchone()
        return bool(row and row["csrf_token_hash"] == hash_token(csrf_token))

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        from services.auth_service import hash_token

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (hash_token(token),),
            )

    @staticmethod
    def _subscription_json(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["active"] = bool(result.get("active"))
        result["pillar_owner_addresses"] = _load_json(
            result.pop("pillar_owner_addresses_json", "[]"), []
        )
        result["events"] = _load_json(result.pop("events_json", "[]"), [])
        return result

    def has_subscriptions(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM pillar_subscriptions LIMIT 1"
            ).fetchone()
        return row is not None

    def get_pillar_subscriptions(
        self,
        *,
        user_id: int | None = None,
        include_inactive: bool = True,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("ps.user_id = ?")
            params.append(int(user_id))
        if not include_inactive:
            clauses.append("ps.active = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT ps.*, u.username AS owner_username
                FROM pillar_subscriptions ps
                LEFT JOIN users u ON u.id = ps.user_id
                {where}
                ORDER BY ps.active DESC, ps.updated_at DESC, ps.id DESC
                """,
                params,
            ).fetchall()
        return [self._subscription_json(row) for row in rows]

    def get_pillar_subscription(
        self,
        subscription_id: int,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT ps.*, u.username AS owner_username
                FROM pillar_subscriptions ps
                LEFT JOIN users u ON u.id = ps.user_id
                WHERE ps.id = ?
                """,
                (int(subscription_id),),
            ).fetchone()
        return self._subscription_json(row) if row else None

    def get_active_subscription_config(self) -> list[dict[str, Any]]:
        return [
            {
                "id": row["id"],
                "channel_id": row["channel_id"],
                "discord_webhook": row["discord_webhook"],
                "pillar_owner_addresses": row["pillar_owner_addresses"],
                "events": row["events"],
            }
            for row in self.get_pillar_subscriptions(include_inactive=False)
        ]

    def create_pillar_subscription(
        self,
        *,
        user_id: int | None,
        channel_id: str = "",
        discord_webhook: str = "",
        pillar_owner_addresses: Iterable[str] = (),
        events: Iterable[str] | None = None,
        label: str = "",
        active: bool = True,
        changed_by: int | None = None,
    ) -> dict[str, Any]:
        channel_id = str(channel_id or "").strip()
        discord_webhook = str(discord_webhook or "").strip()
        if not channel_id and not discord_webhook:
            raise ValueError(
                "Provide a Telegram channel ID, a Discord webhook, or both"
            )
        owners = _string_values(pillar_owner_addresses)
        event_values = (
            _string_values(events)
            if events is not None
            else list(DEFAULT_SUBSCRIPTION_EVENTS)
        )
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO pillar_subscriptions(
                    user_id, label, channel_id, discord_webhook,
                    pillar_owner_addresses_json, events_json, active,
                    created_at, updated_at, created_by, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    str(label).strip(),
                    channel_id,
                    discord_webhook,
                    _json(owners),
                    _json(event_values),
                    1 if active else 0,
                    now,
                    now,
                    changed_by,
                    changed_by,
                ),
            )
            subscription_id = int(cursor.lastrowid)
        return self.get_pillar_subscription(subscription_id)  # type: ignore[return-value]

    def update_pillar_subscription(
        self,
        subscription_id: int,
        *,
        user_id: int | None | object = _UNSET,
        channel_id: str | object = _UNSET,
        discord_webhook: str | object = _UNSET,
        pillar_owner_addresses: Iterable[str] | object = _UNSET,
        events: Iterable[str] | object = _UNSET,
        label: str | object = _UNSET,
        active: bool | object = _UNSET,
        changed_by: int | None = None,
    ) -> dict[str, Any]:
        """Update a subscription; omission is represented by an internal sentinel.

        ``user_id=None`` deliberately means an unassigned legacy subscription;
        omitted fields are left unchanged. The web layer only sends ``None``
        for administrators.
        """
        current = self.get_pillar_subscription(subscription_id)
        if current is None:
            raise ValueError("Subscription not found")
        changes: dict[str, Any] = {}
        next_channel_id = (
            str(current.get("channel_id") or "").strip()
            if channel_id is _UNSET
            else str(channel_id or "").strip()
        )
        next_discord_webhook = (
            str(current.get("discord_webhook") or "").strip()
            if discord_webhook is _UNSET
            else str(discord_webhook or "").strip()
        )
        if not next_channel_id and not next_discord_webhook:
            raise ValueError(
                "Provide a Telegram channel ID, a Discord webhook, or both"
            )
        if user_id is not _UNSET:
            changes["user_id"] = None if user_id is None else int(user_id)
        if channel_id is not _UNSET:
            changes["channel_id"] = next_channel_id
        if discord_webhook is not _UNSET:
            changes["discord_webhook"] = next_discord_webhook
        if pillar_owner_addresses is not _UNSET:
            changes["pillar_owner_addresses_json"] = _json(
                _string_values(pillar_owner_addresses)
            )
        if events is not _UNSET:
            changes["events_json"] = _json(_string_values(events))
        if label is not _UNSET:
            changes["label"] = str(label).strip()
        if active is not _UNSET:
            changes["active"] = 1 if active else 0
        if not changes:
            return current
        changes["updated_at"] = utc_now()
        changes["updated_by"] = changed_by
        assignments = list(changes)
        values = [changes[key] for key in assignments]
        values.append(int(subscription_id))
        with self._connect() as connection:
            connection.execute(
                f"UPDATE pillar_subscriptions SET "
                f"{', '.join(f'{key} = ?' for key in assignments)} "
                "WHERE id = ?",
                values,
            )
        return self.get_pillar_subscription(subscription_id)  # type: ignore[return-value]

    def get_subscription_config_from_legacy_or_db(
        self,
        legacy_config: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if self.has_subscriptions():
            return self.get_active_subscription_config()
        configured = legacy_config.get("telegram_pillar_subscriptions", [])
        return configured if isinstance(configured, list) else []

    def add_audit_log(
        self,
        *,
        user_id: int | None,
        action: str,
        entity_type: str,
        entity_id: str | int | None = None,
        details: Mapping[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_log(
                    user_id, action, entity_type, entity_id,
                    details_json, ip_address, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    str(action),
                    str(entity_type),
                    None if entity_id is None else str(entity_id),
                    _json(details or {}),
                    str(ip_address or "")[:255],
                    utc_now(),
                ),
            )

    def get_audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, u.username
                FROM audit_log a
                LEFT JOIN users u ON u.id = a.user_id
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = _load_json(item.pop("details_json"), {})
            result.append(item)
        return result

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
            factory=_ManagedConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def begin_poll(
        self,
        started_at: str | None = None,
        momentum: Mapping[str, Any] | None = None,
    ) -> int:
        started_at = started_at or utc_now()
        momentum = momentum or {}
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO poll_runs(
                    started_at, status, momentum_height, momentum_hash,
                    momentum_timestamp
                ) VALUES (?, 'running', ?, ?, ?)
                """,
                (
                    started_at,
                    _as_int(momentum.get("height")),
                    momentum.get("hash"),
                    _as_int(momentum.get("timestamp")),
                ),
            )
            return int(cursor.lastrowid)

    def finish_poll(
        self,
        poll_run_id: int,
        status: str,
        completed_at: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE poll_runs
                SET completed_at = ?, status = ?, error = ?
                WHERE id = ?
                """,
                (completed_at or utc_now(), status, error, poll_run_id),
            )

    def update_poll_momentum(
        self,
        poll_run_id: int,
        momentum: Mapping[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE poll_runs
                SET momentum_height = ?,
                    momentum_hash = ?,
                    momentum_timestamp = ?
                WHERE id = ?
                """,
                (
                    _as_int(momentum.get("height")),
                    momentum.get("hash"),
                    _as_int(momentum.get("timestamp")),
                    poll_run_id,
                ),
            )

    def get_node_state(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM node_state WHERE id = 1"
            ).fetchone()
        return dict(row) if row else {
            "id": 1,
            "last_momentum_height": None,
            "last_momentum_hash": None,
            "last_momentum_timestamp": None,
            "last_success_at": None,
            "stale_count": 0,
            "health": "unknown",
            "updated_at": utc_now(),
        }

    def update_node_state(
        self,
        *,
        height: int | None,
        momentum_hash: str | None,
        momentum_timestamp: int | None,
        health: str,
        stale_count: int,
        last_success_at: str | None,
        updated_at: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO node_state(
                    id, last_momentum_height, last_momentum_hash,
                    last_momentum_timestamp, last_success_at, stale_count,
                    health, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_momentum_height = excluded.last_momentum_height,
                    last_momentum_hash = excluded.last_momentum_hash,
                    last_momentum_timestamp = excluded.last_momentum_timestamp,
                    last_success_at = excluded.last_success_at,
                    stale_count = excluded.stale_count,
                    health = excluded.health,
                    updated_at = excluded.updated_at
                """,
                (
                    height,
                    momentum_hash,
                    momentum_timestamp,
                    last_success_at,
                    stale_count,
                    health,
                    updated_at or utc_now(),
                ),
            )

    @staticmethod
    def _row_dict(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _pillar_json(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["is_present"] = bool(result.get("is_present"))
        live_end = result.get("dismantled_at") if not result["is_present"] else None
        result["live_seconds"] = _live_seconds(result.get("live_since"), live_end)
        result["live_duration"] = _duration_text(result["live_seconds"])
        status_end = result.get("dismantled_at") if not result["is_present"] else None
        result["status_seconds"] = _live_seconds(
            result.get("status_since"),
            status_end,
        )
        result["status_duration"] = _duration_text(result["status_seconds"])
        if result.get("raw_payload"):
            result["raw_payload"] = _load_json(result["raw_payload"], {})
        return result

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: str,
        owner_address: str | None,
        epoch: int | None,
        observed_at: str,
        momentum_height: int | None,
        details: Mapping[str, Any] | None,
        notification_channels: Iterable[str],
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO events(
                event_type, owner_address, epoch, observed_at,
                momentum_height, details_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                owner_address,
                epoch,
                observed_at,
                momentum_height,
                _json(details or {}),
            ),
        )
        event_id = int(cursor.lastrowid)
        for channel in notification_channels:
            # Keep route values case-preserving. Discord webhook tokens are
            # opaque and must not be lower-cased before delivery.
            channel = str(channel).strip()
            if not channel:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO notifications(
                    event_id, channel, status, created_at
                ) VALUES (?, ?, 'pending', ?)
                """,
                (event_id, channel, observed_at),
            )
        return event_id

    def record_observation(
        self,
        *,
        poll_run_id: int,
        observed_at: str,
        momentum: Mapping[str, Any],
        epoch_data: Mapping[str, Any],
        epoch_history: Iterable[Mapping[str, Any]] | None = None,
        pillars: Mapping[str, Mapping[str, Any]],
        missed_momentums_threshold: int = 5,
        notification_channels: Iterable[str] = (),
        pillar_notification_channels: Mapping[
            str, Mapping[str, Iterable[str]]
        ] | None = None,
        network_notification_channels: Mapping[
            str, Iterable[str]
        ] | None = None,
    ) -> dict[str, Any]:
        from functions.status import evaluate_pillar_status

        momentum_height = _as_int(momentum.get("height"))
        epoch = _as_int(epoch_data.get("epoch"))
        if momentum_height is None:
            raise ValueError("Momentum height is required")
        if epoch is None:
            raise ValueError("Epoch is required")

        channels = tuple(notification_channels)
        pillar_notification_channels = pillar_notification_channels or {}
        network_notification_channels = network_notification_channels or {}

        def channels_for_event(
            owner_address: str | None,
            event_type: str,
        ) -> tuple[str, ...]:
            event_channels = list(channels)
            event_channels.extend(network_notification_channels.get(event_type, ()))
            if owner_address:
                owner_channels = pillar_notification_channels.get(
                    owner_address.lower(),
                    pillar_notification_channels.get(owner_address, {}),
                )
                event_channels.extend(owner_channels.get(event_type, ()))
            return tuple(dict.fromkeys(str(channel) for channel in event_channels))

        connection = self._connect()
        event_count = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            old_rows = {
                row["owner_address"]: dict(row)
                for row in connection.execute("SELECT * FROM pillars").fetchall()
            }
            previous_epoch_row = connection.execute(
                "SELECT epoch FROM epochs ORDER BY epoch DESC LIMIT 1"
            ).fetchone()
            previous_epoch = (
                _as_int(previous_epoch_row["epoch"])
                if previous_epoch_row
                else None
            )

            epoch_entries = list(epoch_history or [epoch_data])
            if not any(
                _as_int(entry.get("epoch")) == epoch
                for entry in epoch_entries
            ):
                epoch_entries.append(epoch_data)
            for entry in epoch_entries:
                entry_epoch = _as_int(entry.get("epoch"))
                if entry_epoch is None:
                    continue
                connection.execute(
                    """
                    INSERT INTO epochs(
                        epoch, znn_reward, qsr_reward, source_address,
                        first_seen_at, last_seen_at,
                        last_observed_momentum_height, source,
                        epoch_start_at,
                        epoch_start_inferred,
                        announcement_at, announcement_source,
                        announcement_inferred
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(epoch) DO UPDATE SET
                        znn_reward = excluded.znn_reward,
                        qsr_reward = excluded.qsr_reward,
                        source_address = excluded.source_address,
                        last_seen_at = excluded.last_seen_at,
                        last_observed_momentum_height =
                            excluded.last_observed_momentum_height,
                        source = excluded.source,
                        epoch_start_at = CASE
                            WHEN ? = 1 AND excluded.epoch_start_at IS NOT NULL
                            THEN excluded.epoch_start_at
                            ELSE COALESCE(
                                epochs.epoch_start_at,
                                excluded.epoch_start_at
                            )
                        END,
                        epoch_start_inferred = CASE
                            WHEN ? = 1 AND excluded.epoch_start_at IS NOT NULL
                            THEN 0
                            WHEN epochs.epoch_start_inferred = 0
                            THEN 0
                            WHEN excluded.epoch_start_at IS NOT NULL
                            THEN excluded.epoch_start_inferred
                            ELSE epochs.epoch_start_inferred
                        END,
                        announcement_at = COALESCE(
                            epochs.announcement_at,
                            excluded.announcement_at
                        ),
                        announcement_source = COALESCE(
                            epochs.announcement_source,
                            excluded.announcement_source
                        ),
                        announcement_inferred = MAX(
                            COALESCE(epochs.announcement_inferred, 0),
                            excluded.announcement_inferred
                        )
                    """,
                    (
                        entry_epoch,
                        _as_int(entry.get("znn_reward"), 0) or 0,
                        _as_int(entry.get("qsr_reward"), 0) or 0,
                        entry.get("source_address"),
                        observed_at,
                        observed_at,
                        momentum_height,
                        entry.get("source", "node"),
                        entry.get("epoch_start_at"),
                        1 if entry.get(
                            "epoch_start_inferred",
                            entry.get("epoch_start_at") is not None,
                        ) else 0,
                        entry.get("announcement_at"),
                        entry.get("announcement_source"),
                        1 if entry.get("announcement_inferred") else 0,
                        1 if entry.get("epoch_start_observed") else 0,
                        1 if entry.get("epoch_start_observed") else 0,
                    ),
                )

            if previous_epoch is not None and epoch > previous_epoch:
                event_count += 1
                self._insert_event(
                    connection,
                    event_type="epoch_available",
                    owner_address=None,
                    epoch=epoch,
                    observed_at=observed_at,
                    momentum_height=momentum_height,
                    details={
                        "previous_epoch": previous_epoch,
                        "epoch": epoch,
                        "znn_reward": _as_int(epoch_data.get("znn_reward"), 0),
                        "qsr_reward": _as_int(epoch_data.get("qsr_reward"), 0),
                    },
                    notification_channels=channels_for_event(
                        None,
                        "epoch_available",
                    ),
                )

            current_addresses = set(pillars)
            for owner_address, pillar in pillars.items():
                previous = old_rows.get(owner_address)
                status_result = evaluate_pillar_status(
                    previous,
                    pillar,
                    epoch=epoch,
                    threshold=missed_momentums_threshold,
                )
                status = status_result["status"]
                missed = status_result["missed_momentums"]
                previous_present = bool(previous and previous.get("is_present"))
                was_reappearing = bool(previous and not previous_present)
                name = str(pillar.get("name") or owner_address)
                stats = pillar.get("currentStats") or {}
                produced = _as_int(stats.get("producedMomentums"))
                expected = _as_int(stats.get("expectedMomentums"))
                rank = _as_int(pillar.get("rank"))
                weight = _as_int(pillar.get("weight"), 0) or 0
                momentum_share = _as_int(
                    pillar.get("giveMomentumRewardPercentage"), 0
                ) or 0
                delegate_share = _as_int(
                    pillar.get("giveDelegateRewardPercentage"), 0
                ) or 0
                raw_payload = _json(pillar.get("raw", pillar))

                if previous is None or was_reappearing:
                    first_seen_at = (
                        previous.get("first_seen_at")
                        if previous and previous.get("first_seen_at")
                        else observed_at
                    )
                    live_since = observed_at
                    status_since = observed_at
                else:
                    first_seen_at = previous["first_seen_at"]
                    live_since = previous.get("live_since") or first_seen_at
                    status_since = (
                        observed_at
                        if previous.get("status") != status
                        else previous.get("status_since") or observed_at
                    )

                if previous is None or was_reappearing:
                    event_count += 1
                    self._insert_event(
                        connection,
                        event_type="pillar_created",
                        owner_address=owner_address,
                        epoch=epoch,
                        observed_at=observed_at,
                        momentum_height=momentum_height,
                        details={
                            "name": name,
                            "reappeared": was_reappearing,
                            "momentum_reward_percentage": momentum_share,
                            "delegate_reward_percentage": delegate_share,
                        },
                        notification_channels=channels_for_event(
                            owner_address,
                            "pillar_created",
                        ),
                    )

                if previous and previous_present and previous.get("name") != name:
                    event_count += 1
                    self._insert_event(
                        connection,
                        event_type="pillar_name_changed",
                        owner_address=owner_address,
                        epoch=epoch,
                        observed_at=observed_at,
                        momentum_height=momentum_height,
                        details={
                            "old_name": previous.get("name"),
                            "new_name": name,
                        },
                        notification_channels=channels_for_event(
                            owner_address,
                            "pillar_name_changed",
                        ),
                    )

                if previous and previous_present:
                    share_changes: dict[str, Any] = {}
                    if previous.get("momentum_reward_percentage") != momentum_share:
                        share_changes["momentum"] = {
                            "old": previous.get("momentum_reward_percentage"),
                            "new": momentum_share,
                        }
                    if previous.get("delegate_reward_percentage") != delegate_share:
                        share_changes["delegate"] = {
                            "old": previous.get("delegate_reward_percentage"),
                            "new": delegate_share,
                        }
                    if share_changes:
                        event_count += 1
                        self._insert_event(
                            connection,
                            event_type="reward_shares_changed",
                            owner_address=owner_address,
                            epoch=epoch,
                            observed_at=observed_at,
                            momentum_height=momentum_height,
                            details=share_changes,
                            notification_channels=channels_for_event(
                                owner_address,
                                "reward_shares_changed",
                            ),
                        )

                if (
                    previous
                    and previous_present
                    and previous.get("status") != status
                    and status in {"active", "inactive"}
                ):
                    event_count += 1
                    self._insert_event(
                        connection,
                        event_type=f"pillar_{status}",
                        owner_address=owner_address,
                        epoch=epoch,
                        observed_at=observed_at,
                        momentum_height=momentum_height,
                        details={
                            "name": name,
                            "previous_status": previous.get("status"),
                            "status": status,
                            "reason": status_result["reason"],
                            "missed_momentums": missed,
                        },
                        notification_channels=channels_for_event(
                            owner_address,
                            f"pillar_{status}",
                        ),
                    )

                connection.execute(
                    """
                    INSERT INTO pillars(
                        owner_address, first_seen_at, live_since, last_seen_at,
                        dismantled_at, name, rank, weight,
                        produced_momentums, expected_momentums,
                        momentum_reward_percentage,
                        delegate_reward_percentage, status, status_since,
                        missed_momentums, is_present, last_seen_epoch,
                        last_seen_momentum_height, raw_payload
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(owner_address) DO UPDATE SET
                        first_seen_at = excluded.first_seen_at,
                        live_since = excluded.live_since,
                        last_seen_at = excluded.last_seen_at,
                        dismantled_at = NULL,
                        name = excluded.name,
                        rank = excluded.rank,
                        weight = excluded.weight,
                        produced_momentums = excluded.produced_momentums,
                        expected_momentums = excluded.expected_momentums,
                        momentum_reward_percentage =
                            excluded.momentum_reward_percentage,
                        delegate_reward_percentage =
                            excluded.delegate_reward_percentage,
                        status = excluded.status,
                        status_since = excluded.status_since,
                        missed_momentums = excluded.missed_momentums,
                        is_present = 1,
                        last_seen_epoch = excluded.last_seen_epoch,
                        last_seen_momentum_height =
                            excluded.last_seen_momentum_height,
                        raw_payload = excluded.raw_payload
                    """,
                    (
                        owner_address,
                        first_seen_at,
                        live_since,
                        observed_at,
                        name,
                        rank,
                        weight,
                        produced,
                        expected,
                        momentum_share,
                        delegate_share,
                        status,
                        status_since,
                        missed,
                        epoch,
                        momentum_height,
                        raw_payload,
                    ),
                )

                connection.execute(
                    """
                    INSERT INTO pillar_snapshots(
                        poll_run_id, observed_at, momentum_height, epoch,
                        owner_address, name, rank, weight,
                        produced_momentums, expected_momentums,
                        momentum_reward_percentage,
                        delegate_reward_percentage, status,
                        missed_momentums, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        poll_run_id,
                        observed_at,
                        momentum_height,
                        epoch,
                        owner_address,
                        name,
                        rank,
                        weight,
                        produced,
                        expected,
                        momentum_share,
                        delegate_share,
                        status,
                        missed,
                        raw_payload,
                    ),
                )

            for owner_address, previous in old_rows.items():
                if owner_address in current_addresses or not previous.get("is_present"):
                    continue
                event_count += 1
                self._insert_event(
                    connection,
                    event_type="pillar_dismantled",
                    owner_address=owner_address,
                    epoch=epoch,
                    observed_at=observed_at,
                    momentum_height=momentum_height,
                    details={
                        "name": previous.get("name"),
                        "last_seen_at": previous.get("last_seen_at"),
                    },
                    notification_channels=channels_for_event(
                        owner_address,
                        "pillar_dismantled",
                    ),
                )
                connection.execute(
                    """
                    UPDATE pillars
                    SET status = 'dismantled',
                        status_since = ?,
                        is_present = 0,
                        dismantled_at = ?
                    WHERE owner_address = ?
                    """,
                    (observed_at, observed_at, owner_address),
                )

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        return {
            "epoch": epoch,
            "momentum_height": momentum_height,
            "pillar_count": len(pillars),
            "event_count": event_count,
        }

    def get_pending_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        ).isoformat(timespec="seconds")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT n.*, e.event_type, e.owner_address, e.epoch,
                       e.observed_at, e.momentum_height, e.details_json
                FROM notifications n
                JOIN events e ON e.id = n.event_id
                WHERE n.status = 'pending'
                   OR (n.status = 'sending' AND n.last_attempt_at < ?)
                ORDER BY n.created_at ASC
                LIMIT ?
                """,
                (cutoff, max(1, min(int(limit), 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = _load_json(item.pop("details_json"), {})
            result.append(item)
        return result

    def claim_notification(self, notification_id: int) -> bool:
        now = utc_now()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        ).isoformat(timespec="seconds")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE notifications
                SET status = 'sending',
                    attempts = attempts + 1,
                    last_attempt_at = ?,
                    last_error = NULL
                WHERE id = ?
                  AND (
                      status = 'pending'
                      OR (status = 'sending' AND last_attempt_at < ?)
                  )
                """,
                (now, notification_id, cutoff),
            )
            return cursor.rowcount == 1

    def mark_notification_sent(self, notification_id: int) -> None:
        sent_at = utc_now()
        with self._connect() as connection:
            notification = connection.execute(
                """
                SELECT n.channel, e.event_type, e.epoch
                FROM notifications n
                JOIN events e ON e.id = n.event_id
                WHERE n.id = ?
                """,
                (notification_id,),
            ).fetchone()
            connection.execute(
                """
                UPDATE notifications
                SET status = 'sent', sent_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (sent_at, notification_id),
            )
            if (
                notification
                and str(notification["channel"]).startswith("telegram")
                and notification["event_type"] == "epoch_available"
                and notification["epoch"] is not None
            ):
                connection.execute(
                    """
                    UPDATE epochs
                    SET announcement_at = COALESCE(announcement_at, ?),
                        announcement_source = COALESCE(
                            announcement_source,
                            'telegram'
                        ),
                        announcement_inferred = CASE
                            WHEN announcement_at IS NULL THEN 0
                            ELSE announcement_inferred
                        END
                    WHERE epoch = ?
                    """,
                    (sent_at, notification["epoch"]),
                )

    def mark_notification_failed(
        self,
        notification_id: int,
        error: str,
        max_attempts: int = 8,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE notifications
                SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'pending' END,
                    last_error = ?
                WHERE id = ?
                """,
                (max_attempts, str(error)[:1000], notification_id),
            )

    def get_overview(self) -> dict[str, Any]:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM poll_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            node = connection.execute(
                "SELECT * FROM node_state WHERE id = 1"
            ).fetchone()
            epoch = connection.execute(
                "SELECT * FROM epochs ORDER BY epoch DESC LIMIT 1"
            ).fetchone()
            counts = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM pillars
                WHERE is_present = 1
                GROUP BY status
                """
            ).fetchall()
            last_snapshot = connection.execute(
                """
                SELECT MAX(observed_at) AS observed_at
                FROM pillar_snapshots
                """
            ).fetchone()
        counts_dict = {row["status"]: row["count"] for row in counts}
        return {
            "last_run": dict(run) if run else None,
            "node": dict(node) if node else None,
            "epoch": dict(epoch) if epoch else None,
            "pillar_counts": {
                "total": sum(counts_dict.values()),
                "active": counts_dict.get("active", 0),
                "inactive": counts_dict.get("inactive", 0),
            },
            "last_snapshot_at": (
                last_snapshot["observed_at"] if last_snapshot else None
            ),
            "recent_events": self.get_events(limit=8),
        }

    def get_health(self) -> dict[str, Any]:
        with self._connect() as connection:
            node = connection.execute(
                "SELECT * FROM node_state WHERE id = 1"
            ).fetchone()
            run = connection.execute(
                "SELECT * FROM poll_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "node": dict(node) if node else None,
            "last_run": dict(run) if run else None,
            "database": "ok",
        }

    def get_pillars(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 200,
        offset: int = 0,
        include_performance: bool = True,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        elif not status:
            clauses.append("is_present = 1")
        if search:
            clauses.append("(LOWER(name) LIKE LOWER(?) OR owner_address LIKE ?)")
            search_value = f"%{search}%"
            params.extend([search_value, search_value])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS count FROM pillars {where}", params
            ).fetchone()["count"]
            rows = connection.execute(
                f"""
                SELECT * FROM pillars
                {where}
                ORDER BY
                    CASE WHEN is_present = 1 THEN 0 ELSE 1 END,
                    CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
                    rank ASC,
                    name COLLATE NOCASE ASC
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()
        items = [self._pillar_json(row) for row in rows]
        if include_performance:
            performance = self.get_pillar_performance(period_days=30)
            for item in items:
                item["performance_last_30_days"] = performance.get(
                    item["owner_address"],
                    _empty_performance(),
                )
        return {
            "items": items,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
        }

    def get_pillar_performance(
        self,
        *,
        period_days: int = 30,
    ) -> dict[str, dict[str, Any]]:
        period_days = max(1, min(int(period_days), 3650))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(id) AS latest_id FROM pillar_snapshots"
            ).fetchone()
        latest_snapshot_id = int(row["latest_id"] or 0)
        cache_key = (period_days, latest_snapshot_id)

        with self._performance_cache_lock:
            cached = self._performance_cache.get(cache_key)
            if cached is not None:
                return cached
            result = self._calculate_pillar_performance(period_days)
            self._performance_cache[cache_key] = result
            while len(self._performance_cache) > 8:
                self._performance_cache.pop(next(iter(self._performance_cache)))
            return result

    def _calculate_pillar_performance(
        self,
        period_days: int,
    ) -> dict[str, dict[str, Any]]:
        """Calculate aggregate and daily production performance.

        Pillar counters reset at epoch boundaries, so raw snapshot values must
        not be summed. Only non-negative counter changes within the same epoch
        are included. One snapshot before the period provides a baseline when
        available. Daily points use the same valid intervals and are assigned
        to the day of the later snapshot.
        """
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=period_days)).isoformat(
            timespec="seconds"
        )
        day_keys = [
            (now.date() - timedelta(days=offset)).isoformat()
            for offset in range(period_days - 1, -1, -1)
        ]
        with self._connect() as connection:
            rows = connection.execute(
                """
                WITH baseline_ids AS (
                    SELECT owner_address, MAX(id) AS id
                    FROM pillar_snapshots
                    WHERE observed_at < ?
                    GROUP BY owner_address
                ),
                baseline AS (
                    SELECT
                        snapshot.id,
                        snapshot.owner_address,
                        snapshot.observed_at,
                        snapshot.epoch,
                        snapshot.produced_momentums,
                        snapshot.expected_momentums
                    FROM pillar_snapshots snapshot
                    JOIN baseline_ids
                      ON baseline_ids.id = snapshot.id
                ),
                recent AS (
                    SELECT
                        snapshot.id,
                        snapshot.owner_address,
                        snapshot.observed_at,
                        snapshot.epoch,
                        snapshot.produced_momentums,
                        snapshot.expected_momentums
                    FROM pillar_snapshots snapshot
                    WHERE observed_at >= ?
                )
                SELECT
                    id,
                    owner_address,
                    observed_at,
                    epoch,
                    produced_momentums,
                    expected_momentums
                FROM baseline
                UNION ALL
                SELECT
                    id,
                    owner_address,
                    observed_at,
                    epoch,
                    produced_momentums,
                    expected_momentums
                FROM recent
                ORDER BY owner_address, observed_at, id
                """,
                (cutoff, cutoff),
            ).fetchall()

        performance: dict[str, dict[str, Any]] = {}
        daily_performance: dict[
            str, dict[str, dict[str, Any]]
        ] = {}
        previous: dict[str, dict[str, int | None]] = {}
        for row in rows:
            item = dict(row)
            owner_address = str(item["owner_address"])
            observed_at = str(item.get("observed_at") or "")
            is_recent = observed_at >= cutoff
            current_epoch = _as_int(item.get("epoch"))
            current_produced = _as_int(item.get("produced_momentums"))
            current_expected = _as_int(item.get("expected_momentums"))

            if is_recent:
                metrics = performance.setdefault(
                    owner_address,
                    _empty_performance(period_days),
                )
                metrics["observations"] += 1
                owner_daily = daily_performance.setdefault(
                    owner_address,
                    {
                        day: {
                            "date": day,
                            "produced": 0,
                            "expected": 0,
                            "percentage": None,
                            "observations": 0,
                            "intervals": 0,
                        }
                        for day in day_keys
                    },
                )
                observed_day = observed_at.split("T", 1)[0]
                if observed_day in owner_daily:
                    owner_daily[observed_day]["observations"] += 1

            prior = previous.get(owner_address)
            if is_recent and prior is not None:
                produced_delta = (
                    current_produced - prior["produced"]
                    if current_produced is not None
                    and prior["produced"] is not None
                    else None
                )
                expected_delta = (
                    current_expected - prior["expected"]
                    if current_expected is not None
                    and prior["expected"] is not None
                    else None
                )
                if (
                    current_epoch is not None
                    and current_epoch == prior["epoch"]
                    and produced_delta is not None
                    and expected_delta is not None
                    and produced_delta >= 0
                    and expected_delta > 0
                ):
                    metrics = performance[owner_address]
                    metrics["produced"] += produced_delta
                    metrics["expected"] += expected_delta
                    metrics["intervals"] += 1
                    observed_day = observed_at.split("T", 1)[0]
                    daily_point = daily_performance[owner_address].get(
                        observed_day
                    )
                    if daily_point is not None:
                        daily_point["produced"] += produced_delta
                        daily_point["expected"] += expected_delta
                        daily_point["intervals"] += 1

            previous[owner_address] = {
                "epoch": current_epoch,
                "produced": current_produced,
                "expected": current_expected,
            }

        for metrics in performance.values():
            if metrics["expected"]:
                metrics["percentage"] = round(
                    metrics["produced"] * 100 / metrics["expected"],
                    2,
                )
        for owner_address, metrics in performance.items():
            owner_daily = daily_performance.get(owner_address, {})
            daily_points = [
                owner_daily.get(
                    day,
                    {
                        "date": day,
                        "produced": 0,
                        "expected": 0,
                        "percentage": None,
                        "observations": 0,
                        "intervals": 0,
                    },
                )
                for day in day_keys
            ]
            for point in daily_points:
                if point["expected"]:
                    point["percentage"] = round(
                        point["produced"] * 100 / point["expected"],
                        2,
                    )
            metrics["daily"] = daily_points
        return performance

    def get_pillar(self, owner_address: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            pillar = connection.execute(
                "SELECT * FROM pillars WHERE owner_address = ?",
                (owner_address,),
            ).fetchone()
            if not pillar:
                return None
            snapshots = connection.execute(
                """
                SELECT * FROM pillar_snapshots
                WHERE owner_address = ?
                ORDER BY observed_at DESC
                LIMIT 120
                """,
                (owner_address,),
            ).fetchall()
            events = connection.execute(
                """
                SELECT * FROM events
                WHERE owner_address = ?
                ORDER BY observed_at DESC
                LIMIT 100
                """,
                (owner_address,),
            ).fetchall()
        snapshot_items = []
        for row in snapshots:
            item = dict(row)
            item["raw_payload"] = _load_json(item.get("raw_payload"), {})
            snapshot_items.append(item)
        event_items = []
        for row in events:
            item = dict(row)
            item["details"] = _load_json(item.pop("details_json"), {})
            event_items.append(item)
        result = self._pillar_json(pillar)
        result["performance_last_30_days"] = self.get_pillar_performance().get(
            owner_address,
            _empty_performance(),
        )
        result["history"] = snapshot_items
        result["events"] = event_items
        return result

    def get_epochs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM epochs
                ORDER BY epoch DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def backfill_epoch_start_times(
        self,
        *,
        reference_epoch: int,
        reference_start_at: str,
        duration_seconds: int,
    ) -> int:
        """Populate calculated start times without changing announcement data."""
        from tools.epoch_schedule import calculate_epoch_start

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT epoch FROM epochs ORDER BY epoch"
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE epochs SET epoch_start_at = ?, "
                    "epoch_start_inferred = 1 WHERE epoch = ?",
                    (
                        calculate_epoch_start(
                            row["epoch"],
                            reference_epoch=reference_epoch,
                            reference_start_at=reference_start_at,
                            duration_seconds=duration_seconds,
                        ),
                        row["epoch"],
                    ),
            )
        return len(rows)

    def backfill_observed_epoch_start_times(self) -> int:
        """Use the first observed momentum carrying each live epoch."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.epoch, MIN(pr.momentum_timestamp) AS start_timestamp
                FROM events e
                JOIN epochs ep ON ep.epoch = e.epoch
                JOIN poll_runs pr ON pr.momentum_height = e.momentum_height
                WHERE e.event_type = 'epoch_available'
                  AND e.momentum_height IS NOT NULL
                  AND pr.status = 'success'
                  AND pr.momentum_timestamp IS NOT NULL
                  AND pr.momentum_timestamp >= 1000000000
                GROUP BY e.epoch
                """
            ).fetchall()
            for row in rows:
                start_at = datetime.fromtimestamp(
                    int(row["start_timestamp"]),
                    timezone.utc,
                ).isoformat(timespec="seconds")
                connection.execute(
                    "UPDATE epochs SET epoch_start_at = ?, "
                    "epoch_start_inferred = 0 WHERE epoch = ?",
                    (start_at, row["epoch"]),
                )
        return len(rows)

    def backfill_announcement_times_from_notifications(self) -> int:
        """Recover live Telegram announcement times from sent notifications."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.epoch, MIN(n.sent_at) AS sent_at
                FROM notifications n
                JOIN events e ON e.id = n.event_id
                JOIN epochs ep ON ep.epoch = e.epoch
                WHERE e.event_type = 'epoch_available'
                  AND n.channel LIKE 'telegram%'
                  AND n.status = 'sent'
                  AND n.sent_at IS NOT NULL
                  AND ep.announcement_at IS NULL
                GROUP BY e.epoch
                """
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE epochs
                    SET announcement_at = ?,
                        announcement_source = COALESCE(
                            announcement_source,
                            'telegram'
                        ),
                        announcement_inferred = 0
                    WHERE epoch = ? AND announcement_at IS NULL
                    """,
                    (row["sent_at"], row["epoch"]),
                )
        return len(rows)

    def get_events(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        clause = ""
        if event_type:
            clause = "WHERE event_type = ?"
            params.append(event_type)
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM events
                {clause}
                ORDER BY observed_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = _load_json(item.pop("details_json"), {})
            result.append(item)
        return result

    def import_historical_data(
        self,
        *,
        epochs: Iterable[Mapping[str, Any]] = (),
        events: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, int]:
        """Import historical records without changing live tracker state.

        Historical imports contain incomplete information by design. Epoch
        records therefore use zero rewards and no momentum height, while
        imported events may have a null owner address when the source only
        contains a pillar name. Existing rows are never overwritten and no
        notification outbox rows are created.
        """
        connection = self._connect()
        result = {
            "epochs_inserted": 0,
            "epochs_existing": 0,
            "events_inserted": 0,
            "events_existing": 0,
        }
        try:
            connection.execute("BEGIN IMMEDIATE")
            for entry in epochs:
                epoch = _as_int(entry.get("epoch"))
                observed_at = str(entry.get("observed_at") or "").strip()
                if epoch is None or not observed_at:
                    raise ValueError(
                        "Historical epoch records require epoch and observed_at"
                    )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO epochs(
                        epoch, znn_reward, qsr_reward, source_address,
                        first_seen_at, last_seen_at,
                        last_observed_momentum_height, source,
                        epoch_start_at,
                        epoch_start_inferred,
                        announcement_at, announcement_source,
                        announcement_inferred
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        epoch,
                        _as_int(entry.get("znn_reward"), 0) or 0,
                        _as_int(entry.get("qsr_reward"), 0) or 0,
                        entry.get("source_address"),
                        observed_at,
                        observed_at,
                        _as_int(entry.get("last_observed_momentum_height")),
                        str(entry.get("source") or "historical"),
                        entry.get("epoch_start_at"),
                        1 if entry.get(
                            "epoch_start_inferred",
                            entry.get("epoch_start_at") is not None,
                        ) else 0,
                        observed_at,
                        str(entry.get("announcement_source") or "historical"),
                        1 if entry.get("announcement_inferred") else 0,
                    ),
                )
                if cursor.rowcount == 1:
                    result["epochs_inserted"] += 1
                else:
                    connection.execute(
                        """
                        UPDATE epochs
                        SET epoch_start_at = COALESCE(epoch_start_at, ?),
                            announcement_at = COALESCE(announcement_at, ?),
                            announcement_source = COALESCE(
                                announcement_source, ?
                            ),
                            announcement_inferred = MAX(
                                COALESCE(announcement_inferred, 0), ?
                            )
                        WHERE epoch = ?
                        """,
                        (
                            entry.get("epoch_start_at"),
                            observed_at,
                            str(entry.get("announcement_source") or "historical"),
                            1 if entry.get("announcement_inferred") else 0,
                            epoch,
                        ),
                    )
                    result["epochs_existing"] += 1

            for entry in events:
                event_type = str(entry.get("event_type") or "").strip()
                observed_at = str(entry.get("observed_at") or "").strip()
                if not event_type or not observed_at:
                    raise ValueError(
                        "Historical events require event_type and observed_at"
                    )
                owner_address = entry.get("owner_address")
                epoch = _as_int(entry.get("epoch"))
                momentum_height = _as_int(entry.get("momentum_height"))
                details_json = _json(entry.get("details") or {})
                existing = connection.execute(
                    """
                    SELECT 1
                    FROM events
                    WHERE event_type = ?
                      AND owner_address IS ?
                      AND epoch IS ?
                      AND observed_at = ?
                      AND details_json = ?
                    LIMIT 1
                    """,
                    (
                        event_type,
                        owner_address,
                        epoch,
                        observed_at,
                        details_json,
                    ),
                ).fetchone()
                if existing:
                    result["events_existing"] += 1
                    continue
                connection.execute(
                    """
                    INSERT INTO events(
                        event_type, owner_address, epoch, observed_at,
                        momentum_height, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_type,
                        owner_address,
                        epoch,
                        observed_at,
                        momentum_height,
                        details_json,
                    ),
                )
                result["events_inserted"] += 1

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return result

    def apply_legacy_statuses(
        self,
        statuses: Mapping[str, Mapping[str, Any]],
        *,
        observed_at: str,
    ) -> int:
        updated = 0
        with self._connect() as connection:
            for owner_address, status_data in statuses.items():
                is_producing = bool(status_data.get("isProducing", True))
                status = "active" if is_producing else "inactive"
                cursor = connection.execute(
                    """
                    UPDATE pillars
                    SET status = ?, status_since = ?, missed_momentums = ?
                    WHERE owner_address = ?
                    """,
                    (
                        status,
                        observed_at,
                        _as_int(status_data.get("missedMomentums"), 0) or 0,
                        owner_address,
                    ),
                )
                updated += cursor.rowcount
        return updated
