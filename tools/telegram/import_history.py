"""Import public Telegram channel history into the tracker database.

The public Telegram preview pages contain message timestamps and text, so no
Telegram bot token is needed for this importer. It imports only information
that can be identified reliably from the notification text:

* epoch availability announcements;
* pillar stopped-producing and recovered-producing announcements.

The importer is deliberately conservative. It never changes the current
``pillars`` table, never creates notification outbox records, and can be run
repeatedly without duplicating rows. Missing epoch announcements can be
optionally filled with an explicitly marked, approximate timestamp.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time as time_module
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database import Database  # noqa: E402
from epoch_schedule import (  # noqa: E402
    DEFAULT_EPOCH_DURATION_SECONDS,
    DEFAULT_EPOCH_REFERENCE_EPOCH,
    DEFAULT_EPOCH_REFERENCE_START_AT,
    calculate_epoch_start,
)


BASE_URL = "https://t.me"
DEFAULT_CHANNELS = ("pillar_tracker", "ATSocyPT")
DEFAULT_DATABASE = "data_store/pillar_tracker.sqlite3"
DEFAULT_EPOCH_TIME_UTC = "13:30"
EPOCH_PATTERN = re.compile(
    r"\brewards\s+for\s+epoch\s+(?P<epoch>\d+)\s+"
    r"can\s+now\s+be\s+collected\b",
    re.IGNORECASE,
)
INACTIVE_PATTERN = re.compile(
    r"^(?:heads\s+up!\s*)?(?P<name>.+?)\s+"
    r"has\s+stopped\s+producing\s+momentums\.",
    re.IGNORECASE,
)
ACTIVE_PATTERN = re.compile(
    r"^(?:heads\s+up!\s*)?(?P<name>.+?)\s+"
    r"is\s+producing\s+momentums\s+again!",
    re.IGNORECASE,
)


def normalize_message_text(value: str) -> str:
    """Collapse Telegram HTML whitespace into readable plain text."""
    return " ".join(str(value or "").split()).strip()


def normalize_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class TelegramMessage:
    channel: str
    message_id: int
    observed_at: str
    text: str

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.channel}/{self.message_id}"


class _TelegramPageParser(HTMLParser):
    """Extract message blocks and the pagination link from a preview page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[dict[str, str | None]] = []
        self.previous_url: str | None = None
        self._current: dict[str, str | None] | None = None
        self._capturing_text = False
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "link" and attributes.get("rel") == "prev":
            self.previous_url = attributes.get("href")

        post = attributes.get("data-post")
        if tag == "div" and post and "/" in post:
            self._current = {"post": post, "datetime": None, "text": ""}
            self.messages.append(self._current)

        if self._current is None:
            return
        if tag == "time" and attributes.get("datetime"):
            self._current["datetime"] = attributes["datetime"]
        if tag == "div" and "tgme_widget_message_text" in (
            attributes.get("class") or ""
        ):
            self._capturing_text = True
            self._text_parts = []
        elif self._capturing_text and tag == "br":
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._capturing_text and self._current is not None:
            self._current["text"] = "".join(self._text_parts).strip()
            self._capturing_text = False

    def handle_data(self, data: str) -> None:
        if self._capturing_text:
            self._text_parts.append(data)


def parse_preview_page(
    html: str,
    *,
    channel: str,
) -> tuple[list[TelegramMessage], str | None]:
    parser = _TelegramPageParser()
    parser.feed(html)
    messages: list[TelegramMessage] = []
    for raw in parser.messages:
        post = raw.get("post") or ""
        message_id_text = post.rsplit("/", 1)[-1]
        try:
            message_id = int(message_id_text)
            observed_at = normalize_timestamp(raw.get("datetime") or "")
        except (TypeError, ValueError):
            continue
        text = normalize_message_text(raw.get("text") or "")
        if not text:
            continue
        messages.append(
            TelegramMessage(
                channel=channel,
                message_id=message_id,
                observed_at=observed_at,
                text=text,
            )
        )
    return messages, parser.previous_url


def _validate_pagination_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc not in {"t.me", "telegram.me"}:
        raise ValueError(f"Unexpected Telegram pagination host: {parsed.netloc}")
    if not parsed.path.startswith("/s/"):
        raise ValueError(f"Unexpected Telegram pagination path: {parsed.path}")
    return url


def fetch_preview_page(url: str, *, timeout_seconds: float = 30) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Zenon-Pillar-Tracker historical importer",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as url_error:
        # Windows installations commonly have curl.exe available even when
        # Python's certificate store or network resolver cannot reach Telegram.
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError(f"Could not fetch {url}: {url_error}") from url_error
        try:
            completed = subprocess.run(
                [
                    curl,
                    "-L",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    str(max(1, int(timeout_seconds))),
                    url,
                ],
                check=True,
                capture_output=True,
                timeout=timeout_seconds + 5,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as curl_error:
            raise RuntimeError(f"Could not fetch {url}: {curl_error}") from url_error
        return completed.stdout.decode("utf-8", errors="replace")


def crawl_channel(
    channel: str,
    *,
    max_pages: int = 1000,
    delay_seconds: float = 0.15,
    timeout_seconds: float = 30,
) -> list[TelegramMessage]:
    """Read a public channel's preview pages from newest to oldest."""
    url = f"{BASE_URL}/s/{channel}"
    visited: set[str] = set()
    messages_by_id: dict[int, TelegramMessage] = {}
    for page_number in range(max(1, int(max_pages))):
        if url in visited:
            break
        visited.add(url)
        messages, previous_url = parse_preview_page(
            fetch_preview_page(url, timeout_seconds=timeout_seconds),
            channel=channel,
        )
        for message in messages:
            messages_by_id[message.message_id] = message
        if not previous_url:
            break
        url = _validate_pagination_url(urljoin(BASE_URL, previous_url))
        if delay_seconds > 0:
            time_module.sleep(delay_seconds)
    else:
        raise RuntimeError(
            f"Reached --max-pages={max_pages} while crawling @{channel}; "
            "increase the limit if the channel has more history."
        )
    return sorted(
        messages_by_id.values(),
        key=lambda message: (message.observed_at, message.message_id),
    )


def parse_epoch_number(text: str) -> int | None:
    match = EPOCH_PATTERN.search(normalize_message_text(text))
    return int(match.group("epoch")) if match else None


def parse_pillar_status(text: str) -> tuple[str, str] | None:
    normalized = normalize_message_text(text)
    for status, pattern in (
        ("inactive", INACTIVE_PATTERN),
        ("active", ACTIVE_PATTERN),
    ):
        match = pattern.search(normalized)
        if not match:
            continue
        name = normalize_message_text(match.group("name")).strip(" .")
        if name:
            return status, name
    return None


def _parse_default_time(value: str) -> time:
    try:
        hours, minutes = (int(part) for part in value.split(":", 1))
        parsed = time(hour=hours, minute=minutes)
    except (TypeError, ValueError) as exc:
        raise ValueError("Default epoch time must use HH:MM, for example 13:30") from exc
    return parsed


def _source_details(message: TelegramMessage) -> dict[str, Any]:
    return {
        "source": "telegram_preview",
        "source_channel": message.channel,
        "source_message_id": message.message_id,
        "source_url": message.url,
        "source_text": message.text,
        "inferred": False,
    }


def build_import_records(
    messages_by_channel: dict[str, Iterable[TelegramMessage]],
    *,
    fill_missing_epochs: bool = False,
    default_epoch_time: str = DEFAULT_EPOCH_TIME_UTC,
    epoch_reference_epoch: int = DEFAULT_EPOCH_REFERENCE_EPOCH,
    epoch_reference_start_at: str = DEFAULT_EPOCH_REFERENCE_START_AT,
    epoch_duration_seconds: int = DEFAULT_EPOCH_DURATION_SECONDS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Build database records from already downloaded Telegram messages."""
    channels = list(messages_by_channel)
    channel_priority = {channel: index for index, channel in enumerate(channels)}
    all_messages = [
        message
        for channel in channels
        for message in messages_by_channel[channel]
    ]
    epoch_messages: dict[int, TelegramMessage] = {}
    status_messages: list[tuple[TelegramMessage, str, str]] = []
    for message in all_messages:
        epoch = parse_epoch_number(message.text)
        if epoch is not None:
            previous = epoch_messages.get(epoch)
            if previous is None or (
                channel_priority[message.channel], message.observed_at
            ) < (channel_priority[previous.channel], previous.observed_at):
                epoch_messages[epoch] = message
        status = parse_pillar_status(message.text)
        if status is not None:
            status_messages.append((message, status[0], status[1]))

    epochs: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for epoch, message in sorted(epoch_messages.items()):
        epochs.append(
            {
                "epoch": epoch,
                "observed_at": message.observed_at,
                "source": f"telegram:{message.channel}",
                "znn_reward": 0,
                "qsr_reward": 0,
                "epoch_start_at": calculate_epoch_start(
                    epoch,
                    reference_epoch=epoch_reference_epoch,
                    reference_start_at=epoch_reference_start_at,
                    duration_seconds=epoch_duration_seconds,
                ),
                "announcement_source": "telegram_preview",
                "announcement_inferred": False,
            }
        )
        events.append(
            {
                "event_type": "epoch_available",
                "epoch": epoch,
                "observed_at": message.observed_at,
                "details": {
                    "epoch": epoch,
                    **_source_details(message),
                },
            }
        )

    inferred_count = 0
    if fill_missing_epochs and epoch_messages:
        default_time = _parse_default_time(default_epoch_time)
        known_epochs = sorted(epoch_messages)
        for epoch in range(known_epochs[0], known_epochs[-1] + 1):
            if epoch in epoch_messages:
                continue
            before = max((value for value in known_epochs if value < epoch), default=None)
            after = min((value for value in known_epochs if value > epoch), default=None)
            if before is None or after is None:
                continue
            before_at = datetime.fromisoformat(epoch_messages[before].observed_at)
            after_at = datetime.fromisoformat(epoch_messages[after].observed_at)
            ratio = (epoch - before) / (after - before)
            interpolated = before_at + (after_at - before_at) * ratio
            observed_at = datetime.combine(
                interpolated.date(),
                default_time,
                tzinfo=timezone.utc,
            ).isoformat(timespec="seconds")
            details = {
                "epoch": epoch,
                "source": "telegram_preview",
                "source_channel": "combined",
                "source_message_id": None,
                "source_url": None,
                "source_text": None,
                "inferred": True,
                "inference_reason": (
                    "Missing Telegram announcement; date was interpolated "
                    "between the surrounding epoch announcements and the "
                    "time was set to the configured UTC default."
                ),
                "surrounding_epochs": {
                    "before": before,
                    "after": after,
                },
            }
            epochs.append(
                {
                    "epoch": epoch,
                    "observed_at": observed_at,
                    "source": "telegram:inferred",
                    "znn_reward": 0,
                    "qsr_reward": 0,
                    "epoch_start_at": calculate_epoch_start(
                        epoch,
                        reference_epoch=epoch_reference_epoch,
                        reference_start_at=epoch_reference_start_at,
                        duration_seconds=epoch_duration_seconds,
                    ),
                    "announcement_source": "telegram_preview",
                    "announcement_inferred": True,
                }
            )
            events.append(
                {
                    "event_type": "epoch_available",
                    "epoch": epoch,
                    "observed_at": observed_at,
                    "details": details,
                }
            )
            inferred_count += 1

    for message, status, name in status_messages:
        events.append(
            {
                "event_type": f"pillar_{status}",
                "observed_at": message.observed_at,
                "details": {
                    "name": name,
                    "status": status,
                    **_source_details(message),
                },
            }
        )

    epochs.sort(key=lambda entry: int(entry["epoch"]))
    events.sort(key=lambda entry: (str(entry["observed_at"]), entry["event_type"]))
    summary = {
        "messages": len(all_messages),
        "epoch_announcements": len(epoch_messages),
        "inferred_epochs": inferred_count,
        "pillar_status_announcements": len(status_messages),
    }
    return epochs, events, summary


def _format_channel_summary(
    channel: str,
    messages: list[TelegramMessage],
) -> str:
    return f"@{channel}: {len(messages)} messages"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import epoch and pillar status notifications from public "
            "Telegram preview channels."
        )
    )
    parser.add_argument(
        "--channel",
        action="append",
        dest="channels",
        choices=DEFAULT_CHANNELS,
        help="Channel username to crawl; repeat for multiple channels.",
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_DATABASE,
        help=f"Target SQLite database (default: {DEFAULT_DATABASE}).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1000,
        help="Maximum preview pages per channel (default: 1000).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.15,
        help="Delay between page requests in seconds (default: 0.15).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="Network timeout per page in seconds (default: 30).",
    )
    parser.add_argument(
        "--fill-missing-epochs",
        action="store_true",
        help=(
            "Add missing epoch numbers between announcements with inferred "
            "dates and explicitly marked records."
        ),
    )
    parser.add_argument(
        "--default-epoch-time",
        default=DEFAULT_EPOCH_TIME_UTC,
        help="UTC time for inferred epochs (default: 13:30).",
    )
    parser.add_argument(
        "--epoch-start-reference-epoch",
        type=int,
        default=DEFAULT_EPOCH_REFERENCE_EPOCH,
        help=(
            "Reference epoch used to calculate start times "
            f"(default: {DEFAULT_EPOCH_REFERENCE_EPOCH})."
        ),
    )
    parser.add_argument(
        "--epoch-start-reference-at",
        default=DEFAULT_EPOCH_REFERENCE_START_AT,
        help=(
            "UTC start timestamp for the reference epoch "
            f"(default: {DEFAULT_EPOCH_REFERENCE_START_AT})."
        ),
    )
    parser.add_argument(
        "--epoch-duration-seconds",
        type=int,
        default=DEFAULT_EPOCH_DURATION_SECONDS,
        help=(
            "Epoch duration in seconds "
            f"(default: {DEFAULT_EPOCH_DURATION_SECONDS})."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the prepared records to SQLite; otherwise only preview them.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly select preview mode (the default).",
    )
    args = parser.parse_args(argv)

    channels = args.channels or list(DEFAULT_CHANNELS)
    try:
        if args.apply and args.dry_run:
            raise ValueError("Use either --apply or --dry-run, not both")
        _parse_default_time(args.default_epoch_time)
        messages_by_channel: dict[str, list[TelegramMessage]] = {}
        for channel in channels:
            messages_by_channel[channel] = crawl_channel(
                channel,
                max_pages=args.max_pages,
                delay_seconds=args.delay,
                timeout_seconds=args.timeout,
            )
        epochs, events, summary = build_import_records(
            messages_by_channel,
            fill_missing_epochs=args.fill_missing_epochs,
            default_epoch_time=args.default_epoch_time,
            epoch_reference_epoch=args.epoch_start_reference_epoch,
            epoch_reference_start_at=args.epoch_start_reference_at,
            epoch_duration_seconds=args.epoch_duration_seconds,
        )
        print("Telegram history scan complete.")
        for channel, messages in messages_by_channel.items():
            print(_format_channel_summary(channel, messages))
        print(f"Epoch announcements: {summary['epoch_announcements']}")
        print(f"Inferred missing epochs: {summary['inferred_epochs']}")
        print(
            "Pillar status announcements: "
            f"{summary['pillar_status_announcements']}"
        )
        print(f"Prepared epoch records: {len(epochs)}")
        print(f"Prepared event records: {len(events)}")
        if not args.apply:
            print("Dry run only. Use --apply to write these records to SQLite.")
            return 0

        database = Database(args.database)
        result = database.import_historical_data(epochs=epochs, events=events)
        print(
            "SQLite import complete: "
            f"{result['epochs_inserted']} epochs inserted, "
            f"{result['events_inserted']} events inserted."
        )
        print(
            "Existing records kept: "
            f"{result['epochs_existing']} epochs, "
            f"{result['events_existing']} events."
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Telegram history import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
