from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.telegram.common import (  # noqa: E402
    DEFAULT_DATABASE_PATH,
    create_telegram_client,
    load_runtime_settings,
    require_success,
)


def _print_channel_posts(updates: list[dict]) -> None:
    found = False
    for update in updates:
        channel_post = update.get("channel_post")
        if not isinstance(channel_post, dict):
            continue
        found = True
        chat = channel_post.get("chat") or {}
        print(f"Channel: {chat.get('title', 'Unknown channel')}")
        print(f"Channel ID: {chat.get('id', 'unknown')}")
        print(f"Message ID: {channel_post.get('message_id', 'unknown')}")
        print(f"Text: {channel_post.get('text', '')}")
        print()
    if not found:
        print("No channel_post updates found.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show pending Telegram channel posts and their IDs."
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="Path to the SQLite database",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete Telegram API response as JSON",
    )
    args = parser.parse_args(argv)

    try:
        config = load_runtime_settings(args.database)
        response = create_telegram_client(config).bot_get_updates()
        payload = require_success(response, "Telegram getUpdates")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Could not retrieve Telegram updates: {exc}", file=sys.stderr)
        return 1

    updates = payload.get("result") or []
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_channel_posts(updates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
