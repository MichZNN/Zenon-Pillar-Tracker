from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from models.database import Database, utc_now
from services.notification_service import NotificationDispatcher, format_event


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
        with patch("services.notification_service.get_env_value", return_value="test-token"):
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
        with patch("services.notification_service.get_env_value", return_value="test-token"):
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
        with patch("services.notification_service.get_env_value", return_value="test-token"):
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
        with patch("services.notification_service.get_env_value", return_value="test-token"):
            dispatcher = NotificationDispatcher(self.database, config)

        self.assertEqual(dispatcher.pillar_event_channels, {})

    def test_database_discord_subscription_route_is_available_without_telegram(self):
        subscription = self.database.create_pillar_subscription(
            user_id=None,
            discord_webhook="https://discord.com/api/webhooks/123/CaseSensitiveToken",
            pillar_owner_addresses=["z1alpha"],
            events=["pillar_inactive"],
        )
        with patch("services.notification_service.get_env_value", return_value=""):
            dispatcher = NotificationDispatcher(self.database, {})

        route = f"discord_subscription:{subscription['id']}"
        self.assertEqual(dispatcher.channels, ())
        self.assertEqual(
            dispatcher.pillar_event_channels,
            {"z1alpha": {"pillar_inactive": (route,)}},
        )
        self.assertEqual(
            dispatcher.discord_subscription_webhooks[route],
            "https://discord.com/api/webhooks/123/CaseSensitiveToken",
        )

    def test_combined_subscription_creates_telegram_and_discord_routes(self):
        subscription = self.database.create_pillar_subscription(
            user_id=None,
            channel_id="-100pillar",
            discord_webhook="https://discord.com/api/webhooks/123/token",
            pillar_owner_addresses=["z1alpha"],
            events=["pillar_inactive"],
        )
        with patch("services.notification_service.get_env_value", return_value="test-token"):
            dispatcher = NotificationDispatcher(self.database, {})

        route = f"discord_subscription:{subscription['id']}"
        self.assertEqual(
            dispatcher.pillar_event_channels,
            {
                "z1alpha": {
                    "pillar_inactive": ("telegram_chat:-100pillar", route),
                }
            },
        )

    def test_discord_subscription_notification_is_delivered(self):
        subscription = self.database.create_pillar_subscription(
            user_id=None,
            discord_webhook="https://discord.com/api/webhooks/123/CaseSensitiveToken",
            pillar_owner_addresses=["z1alpha"],
            events=["pillar_created"],
        )
        with patch("services.notification_service.get_env_value", return_value=""):
            dispatcher = NotificationDispatcher(self.database, {})

        run_id = self.database.begin_poll()
        self.database.record_observation(
            poll_run_id=run_id,
            observed_at=utc_now(),
            momentum={"height": 101},
            epoch_data={"epoch": 1},
            pillars={
                "z1alpha": {
                    "name": "Alpha",
                    "ownerAddress": "z1alpha",
                    "rank": 0,
                    "weight": 1,
                    "giveMomentumRewardPercentage": 10,
                    "giveDelegateRewardPercentage": 90,
                    "currentStats": {
                        "producedMomentums": 1,
                        "expectedMomentums": 1,
                    },
                }
            },
            pillar_notification_channels=dispatcher.pillar_event_channels,
        )

        with patch.object(
            dispatcher.discord,
            "webhook_send_message_to_channel",
            return_value=SimpleNamespace(status_code=204),
        ) as send:
            result = dispatcher.dispatch_pending()

        self.assertEqual(result, {"sent": 1, "failed": 0})
        send.assert_called_once_with(
            "https://discord.com/api/webhooks/123/CaseSensitiveToken",
            "New pillar spawned: Alpha\nMomentum rewards: 10%\nDelegate rewards: 90%",
        )

    def test_epoch_notification_uses_rotating_emoji_without_raw_reward_units(self):
        first = format_event({"event_type": "epoch_available", "epoch": 42})
        second = format_event({"event_type": "epoch_available", "epoch": 43})

        self.assertEqual(first, "Rewards for epoch 42 can now be collected! 🚀")
        self.assertNotEqual(first[-1], second[-1])
        self.assertNotIn("ZNN:", first)


if __name__ == "__main__":
    unittest.main()
