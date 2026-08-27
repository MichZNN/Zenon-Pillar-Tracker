from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from models.database import Database
from tools.epoch_schedule import calculate_epoch_start
from tools.telegram.import_history import (
    TelegramMessage,
    build_import_records,
    parse_pillar_status,
    parse_preview_page,
)


class TelegramHistoryTestCase(unittest.TestCase):
    def test_preview_page_parser_extracts_messages_and_previous_link(self):
        html = """
        <link rel="prev" href="/s/pillar_tracker?before=10">
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message" data-post="pillar_tracker/11">
            <div class="tgme_widget_message_text js-message_text">
              Rewards for epoch 42 can now be collected! 🚀
            </div>
            <time datetime="2026-01-02T13:30:00+00:00">13:30</time>
          </div>
        </div>
        """

        messages, previous = parse_preview_page(html, channel="pillar_tracker")

        self.assertEqual(previous, "/s/pillar_tracker?before=10")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_id, 11)
        self.assertEqual(messages[0].text, "Rewards for epoch 42 can now be collected! 🚀")

    def test_status_parser_supports_current_and_legacy_messages(self):
        self.assertEqual(
            parse_pillar_status("sunfyre has stopped producing momentums."),
            ("inactive", "sunfyre"),
        )
        self.assertEqual(
            parse_pillar_status(
                "Heads up! EmeraldCap is producing momentums again! 🚀"
            ),
            ("active", "EmeraldCap"),
        )

    def test_missing_epochs_are_explicitly_inferred(self):
        messages = {
            "pillar_tracker": [
                TelegramMessage(
                    "pillar_tracker", 1, "2026-01-01T14:00:00+00:00",
                    "Rewards for epoch 10 can now be collected!",
                ),
                TelegramMessage(
                    "pillar_tracker", 2, "2026-01-04T14:00:00+00:00",
                    "Rewards for epoch 13 can now be collected!",
                ),
                TelegramMessage(
                    "pillar_tracker", 3, "2026-01-02T14:30:00+00:00",
                    "Alpha has stopped producing momentums.",
                ),
            ]
        }

        epochs, events, summary = build_import_records(
            messages,
            fill_missing_epochs=True,
            default_epoch_time="13:30",
        )

        self.assertEqual([row["epoch"] for row in epochs], [10, 11, 12, 13])
        inferred = [row for row in events if row["details"].get("inferred")]
        self.assertEqual(len(inferred), 2)
        self.assertEqual(inferred[0]["observed_at"], "2026-01-02T13:30:00+00:00")
        self.assertEqual(inferred[1]["observed_at"], "2026-01-03T13:30:00+00:00")
        self.assertEqual(summary["pillar_status_announcements"], 1)
        self.assertEqual(
            epochs[1]["epoch_start_at"],
            calculate_epoch_start(11),
        )
        self.assertTrue(epochs[1]["epoch_start_inferred"])

    def test_database_import_is_idempotent_and_does_not_queue_notifications(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "tracker.sqlite3")
            epochs = [
                {
                    "epoch": 10,
                    "observed_at": "2026-01-01T13:30:00+00:00",
                    "source": "telegram:inferred",
                    "announcement_source": "telegram_preview",
                    "announcement_inferred": True,
                    "epoch_start_at": "2025-12-01T13:30:00+00:00",
                }
            ]
            events = [
                {
                    "event_type": "pillar_inactive",
                    "observed_at": "2026-01-01T13:31:00+00:00",
                    "details": {
                        "name": "Alpha",
                        "source_channel": "pillar_tracker",
                        "source_message_id": 4,
                    },
                }
            ]

            first = database.import_historical_data(epochs=epochs, events=events)
            second = database.import_historical_data(epochs=epochs, events=events)

            self.assertEqual(first["epochs_inserted"], 1)
            self.assertEqual(first["events_inserted"], 1)
            self.assertEqual(second["epochs_existing"], 1)
            self.assertEqual(second["events_existing"], 1)
            self.assertEqual(len(database.get_epochs()), 1)
            self.assertEqual(
                database.get_epochs()[0]["announcement_at"],
                "2026-01-01T13:30:00+00:00",
            )
            self.assertEqual(
                database.get_epochs()[0]["announcement_inferred"],
                1,
            )
            self.assertEqual(
                database.get_epochs()[0]["epoch_start_at"],
                "2025-12-01T13:30:00+00:00",
            )
            self.assertTrue(
                database.get_epochs()[0]["epoch_start_inferred"]
            )
            self.assertEqual(len(database.get_events()), 1)
            self.assertEqual(database.get_pending_notifications(), [])


if __name__ == "__main__":
    unittest.main()
