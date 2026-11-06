"""Application logging with a bounded, Linux-friendly rotating log file."""

from __future__ import annotations

import logging
import os
import sys
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = PROJECT_ROOT / "data_store" / "pillar_tracker.log"
_HANDLER_MARKER = "_zenon_pillar_tracker_handler"


def resolve_log_path(config: Mapping[str, Any] | None = None) -> Path:
    """Return the one supported application log path.

    ``config`` remains an accepted argument for callers from older releases,
    but the path is deliberately not configurable. The container only mounts
    ``data_store`` as writable, so allowing an arbitrary path made logging
    appear to work while silently pointing it at the read-only image layer.
    """
    del config
    return DEFAULT_LOG_PATH


def _ensure_log_file(path: Path) -> None:
    # mkdir honours the process umask and works for a service account on Linux
    # as long as that account owns (or can write) the configured data_store.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    if not os.access(path.parent, os.W_OK | os.X_OK):
        raise PermissionError(
            f"Log directory is not writable by the current process: {path.parent}"
        )
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_APPEND | os.O_WRONLY,
        0o640,
    )
    os.close(descriptor)


def _create_file_handler(
    path: Path,
    *,
    max_bytes: int,
    backup_count: int,
) -> RotatingFileHandler:
    _ensure_log_file(path)
    return RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )


def configure_logging(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    root = logging.getLogger()
    configured_level = str(config.get("log_level", "INFO")).upper()
    level = getattr(logging, configured_level, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    try:
        max_bytes = max(1, int(config.get("log_max_bytes", 5 * 1024 * 1024)))
    except (TypeError, ValueError):
        max_bytes = 5 * 1024 * 1024
    try:
        backup_count = max(0, min(20, int(config.get("log_backup_count", 5))))
    except (TypeError, ValueError):
        backup_count = 5
    path = resolve_log_path()
    file_enabled = True
    error = None
    try:
        handler: logging.Handler = _create_file_handler(
            path,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
    except (OSError, ValueError) as exc:
        file_enabled = False
        error = str(exc)
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_MARKER, True)
    root.setLevel(level)
    root.addHandler(handler)
    if file_enabled:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        setattr(console_handler, _HANDLER_MARKER, True)
        root.addHandler(console_handler)
    logging.captureWarnings(True)
    logger = logging.getLogger("logging_setup")
    if error:
        logger.error("Could not open fixed log file %s: %s", path, error)
    else:
        logger.info(
            "File logging enabled at %s (max %d bytes, %d backups)",
            path,
            max_bytes,
            backup_count,
        )
    return {
        "path": str(path),
        "max_bytes": max_bytes,
        "backup_count": backup_count,
        "level": logging.getLevelName(level),
        "file_enabled": file_enabled,
        "error": error,
    }


def read_log_tail(
    config: Mapping[str, Any] | None = None,
    *,
    lines: int = 300,
    max_bytes: int = 512 * 1024,
) -> dict[str, Any]:
    del config
    path = resolve_log_path()
    lines = max(1, min(int(lines), 1000))
    max_bytes = max(4096, min(int(max_bytes), 2 * 1024 * 1024))
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "lines": [],
        }
    try:
        size = path.stat().st_size
        with path.open("rb") as file:
            file.seek(max(0, size - max_bytes))
            text = file.read(max_bytes).decode("utf-8", errors="replace")
    except OSError as exc:
        return {
            "path": str(path),
            "exists": False,
            "lines": [],
            "error": str(exc),
        }
    recent_lines = list(deque(text.splitlines(), maxlen=lines))
    result = {
        "path": str(path),
        "exists": True,
        "size_bytes": size,
        # The portal presents logs as a live operational view, so the most
        # recent entry should be immediately visible at the top.
        "lines": list(reversed(recent_lines)),
    }
    return result


_LOG_TIMESTAMP = __import__("re").compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{4}|Z)?"
)
MAX_ROTATED_LOG_FILES = 20


def _parse_log_timestamp(line: str):
    from datetime import datetime, timezone

    match = _LOG_TIMESTAMP.match(line)
    if not match:
        return None
    zone = match.group(2) or "+0000"
    if zone == "Z":
        zone = "+0000"
    try:
        return datetime.strptime(match.group(1) + zone, "%Y-%m-%dT%H:%M:%S%z").astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _log_files(path: Path) -> list[Path]:
    """The active log and its rotated backups, newest first."""
    files = [path] if path.exists() else []
    for index in range(1, MAX_ROTATED_LOG_FILES + 1):
        candidate = path.with_name(f"{path.name}.{index}")
        if candidate.exists():
            files.append(candidate)
    return files


def read_log_range(
    since=None,
    until=None,
    *,
    lines: int = 1000,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return log lines between ``since`` and ``until`` (UTC datetimes), newest first.

    Reads the active log and the rotated backups, so a period can go back as
    far as the retained files. Lines without a timestamp (tracebacks) belong
    to the entry before them.
    """
    path = path or resolve_log_path()
    limit = max(1, min(int(lines), 5000))
    files = _log_files(path)
    oldest_available = None
    if files:
        try:
            with files[-1].open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    oldest_available = _parse_log_timestamp(line)
                    if oldest_available:
                        break
        except OSError:
            oldest_available = None

    collected: list[str] = []
    truncated = False
    done = False
    for log_file in files:
        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Group continuation lines with the timestamped line they follow.
        entries: list[tuple[Any, str]] = []
        for line in text.splitlines():
            stamp = _parse_log_timestamp(line)
            if stamp is None and entries:
                entries[-1] = (entries[-1][0], entries[-1][1] + "\n" + line)
            elif line.strip():
                entries.append((stamp, line))
        for stamp, entry in reversed(entries):
            if stamp is not None and until is not None and stamp >= until:
                continue
            if stamp is not None and since is not None and stamp < since:
                done = True
                break
            if len(collected) >= limit:
                truncated = True
                done = True
                break
            collected.append(entry)
        if done:
            break
    return {
        "path": str(path),
        "exists": bool(files),
        "files": len(files),
        "lines": collected,
        "truncated": truncated,
        "oldest_available_at": (
            oldest_available.isoformat(timespec="seconds") if oldest_available else None
        ),
    }
