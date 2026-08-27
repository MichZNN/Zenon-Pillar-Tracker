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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send a test message through the configured Telegram bot."
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="Path to the SQLite database",
    )
    parser.add_argument(
        "--chat-id",
        help="Override telegram_channel_id from SQLite settings",
    )
    parser.add_argument(
        "--message",
        default="Zenon Pillar Tracker test message",
        help="Message text to send",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete Telegram API response as JSON",
    )
    args = parser.parse_args(argv)

    try:
        config = load_runtime_settings(args.database)
        chat_id = str(
            args.chat_id or config.get("telegram_channel_id", "")
        ).strip()
        if not chat_id:
            raise ValueError(
                "telegram_channel_id is empty in SQLite settings"
            )
        response = create_telegram_client(config).bot_send_message_to_chat(
            chat_id,
            args.message,
        )
        payload = require_success(response, "Telegram sendMessage")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Could not send Telegram test message: {exc}", file=sys.stderr)
        return 1

    result = payload.get("result") or {}
    print("Telegram test message sent successfully.")
    print(f"Channel ID: {result.get('chat', {}).get('id', chat_id)}")
    print(f"Message ID: {result.get('message_id', 'unknown')}")
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
