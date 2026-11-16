from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from models.database import Database, utc_now
from services.notification_service import (
    NotificationDispatcher,
    create_pinned_stats_keyboard,
    create_pinned_stats_message,
    format_event,
    parse_pinned_callback_data,
    pinned_stats_page_count,
    pinned_stats_status_summary,
)


class NotificationDispatcherTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "tracker.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pinned_stats_are_paginated_and_filterable(self):
        pillars = {
            f"z1pillar{index}": {
                "name": f"Pillar {index}",
                "rank": index,
                "weight": 100000000 * (index + 1),
                "giveMomentumRewardPercentage": 10,
                "giveDelegateRewardPercentage": 90,
                "status": "active" if index % 2 == 0 else "inactive",
                "currentStats": {
                    "producedMomentums": index,
                    "expectedMomentums": index + 1,
                },
            }
            for index in range(45)
        }

        first_page = create_pinned_stats_message(pillars, 123, page=1)
        second_page = create_pinned_stats_message(pillars, 123, page=2)
        active_page = create_pinned_stats_message(
            pillars,
            123,
            status="active",
            page=1,
        )

        self.assertIn("Filter: All pillars · Page 1/3", first_page)
        self.assertIn("🟢 Active: 23 🔴 Inactive: 22", first_page)
        self.assertIn(
            "Momentum height: 123\n🌐 https://pillartracker.zenon.nom.co\n",
            first_page,
        )
        self.assertIn("1 · Pillar 0 •", first_page)
        self.assertIn("20 · Pillar 19 •", first_page)
        self.assertNotIn("21 · Pillar 20 •", first_page)
        self.assertIn("21 · Pillar 20 •", second_page)
        self.assertIn("Filter: Active pillars · Page 1/2", active_page)
        self.assertIn("1 · Pillar 0 •", active_page)
        self.assertNotIn("2 · Pillar 1 •", active_page)
        self.assertLess(
            first_page.index("🟢 Active: 23"),
            first_page.index("Momentum height"),
        )
        self.assertLess(
            first_page.index("Momentum height"),
            first_page.index("Pillar reward sharing rates"),
        )
        self.assertLess(
            first_page.index("Pillar reward sharing rates"),
            first_page.index("Filter: All pillars"),
        )
        self.assertLess(
            first_page.index("Filter: All pillars"),
            first_page.index("1 · Pillar 0"),
        )
        self.assertLess(
            first_page.index("20 · Pillar 19"),
            first_page.index("🕒 Updated"),
        )
        self.assertRegex(
            first_page,
            r"🕒 Updated: [A-Z][a-z]{2} \d{2} \d{2}:\d{2} UTC",
        )
        self.assertEqual(pinned_stats_page_count(pillars, "inactive"), 2)

        keyboard = create_pinned_stats_keyboard(
            status="active",
            page=2,
            page_count=2,
            bot_username="ZenonPillarTrackerBot",
        )
        self.assertEqual(
            [button["text"] for button in keyboard["inline_keyboard"][0]],
            ["⏮️", "◀️", "2/2", "▶️", "⏭️"],
        )
        self.assertEqual(
            keyboard["inline_keyboard"][-1],
            [
                {"text": "🤖 Open bot", "url": "https://t.me/ZenonPillarTrackerBot"},
                {"text": "🌐 Open tracker", "url": "https://pillartracker.zenon.nom.co"},
            ],
        )
        private_keyboard = create_pinned_stats_keyboard(
            status="all",
            page=1,
            page_count=1,
            include_bot_button=False,
        )
        self.assertEqual(
            private_keyboard["inline_keyboard"][-1],
            [{"text": "🌐 Open tracker", "url": "https://pillartracker.zenon.nom.co"}],
        )
        self.assertEqual(
            parse_pinned_callback_data("pillar:page:inactive:3"),
            ("inactive", 3),
        )
        self.assertIsNone(parse_pinned_callback_data("unknown:button"))
        self.assertEqual(
            pinned_stats_status_summary(pillars),
            "Active: 23 · Inactive: 22",
        )
        self.assertEqual(
            pinned_stats_status_summary(pillars, include_icons=True),
            "🟢 Active: 23 🔴 Inactive: 22",
        )

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

    def _record_alpha(self, channels):
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
            pillar_notification_channels={
                "z1alpha": {"pillar_created": channels},
            },
        )
        return self.database.get_events(event_type="pillar_created")[0]["id"]

    def test_telegram_message_id_is_stored_after_sending(self):
        with patch("services.notification_service.get_env_value", return_value="token"):
            dispatcher = NotificationDispatcher(
                self.database,
                {"telegram_channel_id": "-100global"},
            )
        event_id = self._record_alpha(("telegram_chat:-100pillar",))
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True, "result": {"message_id": 4242}},
        )
        with patch.object(
            dispatcher.telegram,
            "bot_send_message_to_chat",
            return_value=response,
        ):
            result = dispatcher.dispatch_pending()

        self.assertEqual(result, {"sent": 1, "failed": 0})
        notifications = self.database.get_event_notifications(event_id)
        self.assertEqual(notifications[0]["channel"], "telegram_chat:-100pillar")
        self.assertEqual(notifications[0]["external_message_id"], "4242")

    def test_edit_tool_rewrites_stored_and_manual_messages(self):
        from tools.telegram import edit_event_message

        event_id = self._record_alpha(("telegram_chat:-100pillar",))
        notification_id = self.database.get_event_notifications(event_id)[0]["id"]
        self.database.mark_notification_sent(notification_id, 77)
        edited = []
        client = SimpleNamespace(
            bot_edit_message=lambda chat, message_id, text: edited.append(
                (chat, message_id, text)
            ) or SimpleNamespace(status_code=200, json=lambda: {"ok": True}),
            response_ok=lambda response: True,
        )
        config = {"telegram_channel_id": "-100global"}
        database_path = str(self.database.path)
        with patch.object(
            edit_event_message, "load_runtime_settings", return_value=config
        ), patch.object(
            edit_event_message, "create_telegram_client", return_value=client
        ):
            dry_run = edit_event_message.main(
                ["--database", database_path, "--event-id", str(event_id)]
            )
            stored = edit_event_message.main(
                ["--database", database_path, "--event-id", str(event_id), "--apply"]
            )
            manual = edit_event_message.main(
                [
                    "--database", database_path,
                    "--event-id", str(event_id),
                    "--message-id", "12",
                    "--apply",
                ]
            )

        self.assertEqual((dry_run, stored, manual), (0, 0, 0))
        text = "New pillar spawned: Alpha\nMomentum rewards: 10%\nDelegate rewards: 90%"
        self.assertEqual(
            edited,
            [("-100pillar", 77, text), ("-100global", 12, text)],
        )

    def test_reward_share_notification_uses_pillar_name(self):
        message = format_event(
            {
                "event_type": "reward_shares_changed",
                "owner_address": "z1alpha",
                "details": {
                    "name": "Alpha",
                    "momentum": {"old": 0, "new": 100},
                    "delegate": {"old": 0, "new": 100},
                },
            }
        )

        self.assertEqual(
            message,
            "Pillar: Alpha\n"
            "Momentum rewards: 0% ➡️ 100%\n"
            "Delegate rewards: 0% ➡️ 100%",
        )

    def test_epoch_notification_uses_rotating_emoji_without_raw_reward_units(self):
        first = format_event({"event_type": "epoch_available", "epoch": 42})
        second = format_event({"event_type": "epoch_available", "epoch": 43})

        self.assertEqual(first, "Rewards for epoch 42 can now be collected! 🚀")
        self.assertNotEqual(first[-1], second[-1])
        self.assertNotIn("ZNN:", first)


if __name__ == "__main__":
    unittest.main()
