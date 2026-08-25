from __future__ import annotations

import os
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def load_env_file(env_path: str | Path = DEFAULT_ENV_PATH) -> None:
    """Load simple KEY=value entries from the project .env file."""
    path = Path(env_path)
    if not path.exists():
        return

    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            if entry.startswith("export "):
                entry = entry[7:].lstrip()
            if "=" not in entry:
                raise ValueError(
                    f"Invalid .env entry on line {line_number}: expected KEY=value"
                )
            name, value = entry.split("=", 1)
            name = name.strip()
            if not _ENV_NAME.fullmatch(name):
                raise ValueError(
                    f"Invalid .env variable name on line {line_number}: {name}"
                )
            os.environ[name] = _parse_value(value)


def get_env_value(name: str) -> str:
    """Load the project .env file and return one environment value."""
    load_env_file()
    return os.environ.get(name, "").strip()
