from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import Database
from notifications import NotificationDispatcher, format_event


class NotificationDispatcherTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "tracker.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_global_channel_and_pillar_routes_are_configured(self):
        config = {
            "telegram_channel_id": "-100global",
            "telegram_pillar_subscriptions": [
                {
                    "channel_id": "-100pillar",
                    "pillar_owner_addresses": ["z1alpha", "z1beta"],
                    "events": ["pillar_inactive", "pillar_active"],
                }
            ],
        }
        with patch("notifications.get_env_value", return_value="test-token"):
            dispatcher = NotificationDispatcher(self.database, config)

        self.assertEqual(dispatcher.channels, ("telegram",))
        self.assertEqual(
            dispatcher.pillar_event_channels,
            {
                "z1alpha": {
                    "pillar_inactive": ("telegram_chat:-100pillar",),
                    "pillar_active": ("telegram_chat:-100pillar",),
                },
                "z1beta": {
                    "pillar_inactive": ("telegram_chat:-100pillar",),
                    "pillar_active": ("telegram_chat:-100pillar",),
                },
            },
        )
        self.assertEqual(dispatcher.network_event_channels, {})

    def test_subscription_defaults_to_status_and_reward_events(self):
        config = {
            "telegram_pillar_subscriptions": [
                {
                    "channel_id": "-100pillar",
                    "pillar_owner_addresses": ["z1alpha"],
                }
            ],
        }
        with patch("notifications.get_env_value", return_value="test-token"):
            dispatcher = NotificationDispatcher(self.database, config)

        self.assertEqual(
            set(dispatcher.pillar_event_channels["z1alpha"]),
            {
                "pillar_inactive",
                "pillar_active",
                "reward_shares_changed",
            },
        )
        self.assertEqual(dispatcher.network_event_channels, {})

    def test_epoch_subscription_can_be_network_only(self):
        config = {
            "telegram_pillar_subscriptions": [
                {
                    "channel_id": "-100epoch",
                    "events": ["epoch_available"],
                }
            ],
        }
        with patch("notifications.get_env_value", return_value="test-token"):
            dispatcher = NotificationDispatcher(self.database, config)

        self.assertEqual(dispatcher.pillar_event_channels, {})
        self.assertEqual(
            dispatcher.network_event_channels,
            {"epoch_available": ("telegram_chat:-100epoch",)},
        )

    def test_global_channel_is_not_added_as_a_second_route(self):
        config = {
            "telegram_channel_id": "-100same",
            "telegram_pillar_subscriptions": [
                {
                    "channel_id": "-100same",
                    "pillar_owner_addresses": ["z1alpha"],
                }
            ],
        }
        with patch("notifications.get_env_value", return_value="test-token"):
            dispatcher = NotificationDispatcher(self.database, config)

        self.assertEqual(dispatcher.pillar_event_channels, {})

    def test_epoch_notification_uses_rotating_emoji_without_raw_reward_units(self):
        first = format_event({"event_type": "epoch_available", "epoch": 42})
        second = format_event({"event_type": "epoch_available", "epoch": 43})

        self.assertEqual(first, "Rewards for epoch 42 can now be collected! 🚀")
        self.assertNotEqual(first[-1], second[-1])
        self.assertNotIn("ZNN:", first)


if __name__ == "__main__":
    unittest.main()
