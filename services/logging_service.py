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
    configured = (config or {}).get("log_path", "data_store/pillar_tracker.log")
    path = Path(str(configured).strip() or "data_store/pillar_tracker.log")
    return path if path.is_absolute() else PROJECT_ROOT / path


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
    configured_path = resolve_log_path(config)
    path = configured_path
    file_enabled = True
    error = None
    try:
        handler: logging.Handler = _create_file_handler(
            path,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
    except (OSError, ValueError) as exc:
        # A legacy or host-specific path may point at a read-only location in
        # the container. Fall back to the mounted data directory so both the
        # collector and dashboard retain a readable operational log.
        fallback_path = DEFAULT_LOG_PATH
        original_error = str(exc)
        if fallback_path != configured_path:
            try:
                handler = _create_file_handler(
                    fallback_path,
                    max_bytes=max_bytes,
                    backup_count=backup_count,
                )
                path = fallback_path
                error = (
                    f"Configured log path {configured_path} is unavailable "
                    f"({original_error}); using fallback {fallback_path}."
                )
            except (OSError, ValueError) as fallback_error:
                file_enabled = False
                error = (
                    f"{original_error}; fallback log path {fallback_path} "
                    f"is also unavailable: {fallback_error}"
                )
                handler = logging.StreamHandler(sys.stderr)
        else:
            file_enabled = False
            error = original_error
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
    if error and file_enabled and path != configured_path:
        logger.error("Could not open configured log file: %s", error)
    elif error:
        logger.error("Could not open log file %s: %s", path, error)
    else:
        logger.info(
            "File logging enabled at %s (max %d bytes, %d backups)",
            path,
            max_bytes,
            backup_count,
        )
    return {
        "path": str(path),
        "configured_path": str(configured_path),
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
    configured_path = resolve_log_path(config)
    path = configured_path
    lines = max(1, min(int(lines), 1000))
    max_bytes = max(4096, min(int(max_bytes), 2 * 1024 * 1024))
    candidates = [configured_path]
    if DEFAULT_LOG_PATH != configured_path:
        candidates.append(DEFAULT_LOG_PATH)
    last_error = None
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            size = candidate.stat().st_size
            with candidate.open("rb") as file:
                file.seek(max(0, size - max_bytes))
                text = file.read(max_bytes).decode("utf-8", errors="replace")
        except OSError as exc:
            last_error = str(exc)
            continue
        result = {
            "path": str(candidate),
            "configured_path": str(configured_path),
            "exists": True,
            "size_bytes": size,
            "lines": list(deque(text.splitlines(), maxlen=lines)),
        }
        if candidate != configured_path:
            result["error"] = (
                f"Configured log path {configured_path} is unavailable; "
                f"showing fallback log {candidate}."
            )
        return result

    fallback_path = DEFAULT_LOG_PATH if DEFAULT_LOG_PATH != configured_path else path
    result = {
        "path": str(fallback_path),
        "configured_path": str(configured_path),
        "exists": False,
        "lines": [],
    }
    if last_error:
        result["error"] = last_error
    return result
