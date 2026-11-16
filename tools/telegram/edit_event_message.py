"""Rewrite a Telegram message that was sent for a stored event.

The text is rebuilt from the event in SQLite with the current formatting, so
an old notification can be corrected after a formatting fix. Without --apply
the tool only prints what it would change.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from models.database import Database  # noqa: E402
from services.notification_service import (  # noqa: E402
    format_event,
    telegram_chat_id_for_channel,
)
from tools.telegram.common import (  # noqa: E402
    DEFAULT_DATABASE_PATH,
    create_telegram_client,
    load_runtime_settings,
)

NOT_MODIFIED = "message is not modified"


def _targets(
    database: Database,
    event_id: int,
    global_channel_id: str,
    message_id: int | None,
    chat_id: str | None,
) -> list[tuple[str, int]]:
    if message_id is not None:
        target_chat = chat_id or global_channel_id
        if not target_chat:
            raise ValueError(
                "telegram_channel_id is empty in SQLite settings; pass --chat-id"
            )
        return [(target_chat, message_id)]

    targets = []
    filtered_out = 0
    for notification in database.get_event_notifications(event_id):
        stored_id = notification.get("external_message_id")
        target_chat = telegram_chat_id_for_channel(
            str(notification["channel"]),
            global_channel_id,
        )
        if not stored_id or not target_chat:
            continue
        if chat_id and target_chat.casefold() != chat_id.casefold():
            filtered_out += 1
            continue
        targets.append((target_chat, int(stored_id)))
    if not targets and filtered_out:
        raise ValueError(
            f"This event has stored Telegram messages, but none in chat {chat_id}"
        )
    return targets


def _print_recent(database: Database, event_type: str | None, limit: int) -> None:
    for event in database.get_events(limit=limit, event_type=event_type):
        stored = [
            f"{n['channel']}#{n['external_message_id']}"
            for n in database.get_event_notifications(event["id"])
            if n.get("external_message_id")
        ]
        print(f"Event {event['id']} · {event['event_type']} · {event['observed_at']}")
        print("  " + format_event(event).replace("\n", "\n  "))
        print("  Stored Telegram messages: " + (", ".join(stored) or "none"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild the text of a stored event and edit the Telegram message "
            "that announced it."
        )
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="Path to the SQLite database",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List recent events with their IDs and current text",
    )
    parser.add_argument(
        "--event-type",
        help="With --list: only show this event type, e.g. reward_shares_changed",
    )
    parser.add_argument(
        "--limit",
        type=lambda value: max(1, min(int(value), 100)),
        default=10,
        help="With --list: number of events to show (default 10)",
    )
    parser.add_argument("--event-id", type=int, help="Event to rewrite")
    parser.add_argument(
        "--message-id",
        type=int,
        help=(
            "Telegram message ID to edit. Needed for messages sent before "
            "message IDs were stored."
        ),
    )
    parser.add_argument(
        "--chat-id",
        help="Telegram chat ID; defaults to telegram_channel_id from SQLite",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Edit the message. Without it the tool only shows the new text.",
    )
    args = parser.parse_args(argv)

    try:
        database = Database(args.database)
        if args.list:
            _print_recent(database, args.event_type, args.limit)
            return 0
        if args.event_id is None:
            parser.error("--event-id is required unless --list is used")

        event = database.get_event(args.event_id)
        if event is None:
            raise ValueError(f"Event {args.event_id} does not exist")
        config = load_runtime_settings(args.database)
        global_channel_id = str(config.get("telegram_channel_id", "")).strip()
        chat_id = str(args.chat_id).strip() if args.chat_id else None
        targets = _targets(
            database,
            args.event_id,
            global_channel_id,
            args.message_id,
            chat_id,
        )
        if not targets:
            raise ValueError(
                "No Telegram message ID is stored for this event; "
                "pass --message-id"
            )

        message = format_event(event)
        print("New text:")
        print("  " + message.replace("\n", "\n  "))
        for target_chat, target_message in targets:
            print(f"Target: chat {target_chat}, message {target_message}")
        if not args.apply:
            print("Dry run. Add --apply to edit the message.")
            return 0

        telegram = create_telegram_client(config)
        failures = 0
        for target_chat, target_message in targets:
            response = telegram.bot_edit_message(
                target_chat,
                target_message,
                message,
            )
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            description = str(payload.get("description") or "")
            if telegram.response_ok(response):
                print(f"Edited message {target_message} in chat {target_chat}.")
            elif NOT_MODIFIED in description:
                print(f"Message {target_message} already has this text.")
            else:
                failures += 1
                print(
                    f"Could not edit message {target_message} in chat "
                    f"{target_chat}: HTTP {response.status_code} "
                    f"{description or 'Unknown Telegram error'}",
                    file=sys.stderr,
                )
        return 1 if failures else 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Could not edit Telegram message: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
