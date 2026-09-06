from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from controllers.collector_controller import Collector
from services.settings_service import DEFAULT_SETTINGS
from utils.node_rpc_pool import NodeRpcPool


def pillar(produced: int, expected: int):
    return {
        "alpha": {
            "name": "Alpha",
            "ownerAddress": "alpha",
            "currentStats": {
                "producedMomentums": produced,
                "expectedMomentums": expected,
            },
            "weight": 10000000000,
            "giveMomentumRewardPercentage": 10,
            "giveDelegateRewardPercentage": 90,
            "rank": 0,
            "raw": {},
        }
    }


class FakeNode:
    def __init__(self):
        self.node_url = "http://fake-node"
        self.height = 100
        self.produced = 1
        self.expected = 1

    def get_latest_momentum(self):
        return {
            "height": self.height,
            "hash": f"hash-{self.height}",
            "timestamp": self.height,
        }

    def get_sync_info(self):
        return {
            "state": 2,
            "currentHeight": self.height,
            "targetHeight": self.height,
        }

    def get_all_pillars(self):
        return {"pillars": pillar(self.produced, self.expected)}

    def get_reward_epoch(self, address):
        return {
            "epoch": 1,
            "znn_reward": 100,
            "qsr_reward": 10,
            "source_address": address,
        }


class CollectorTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.collector = Collector(
            {
                "node_rpc_urls": ["http://fake-node"],
                "database_path": str(Path(self.temp_dir.name) / "tracker.sqlite3"),
                "reference_reward_address": "reference",
                "missed_momentums_threshold": 2,
                "stale_grace_runs": 2,
            }
        )
        self.node = FakeNode()
        self.collector.node = NodeRpcPool([self.node])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_collector_handles_stale_runs_and_status_changes(self):
        first = self.collector.run_once()
        self.assertEqual(first["status"], "success")
        self.assertIsNone(
            self.collector.database.get_epochs(limit=1)[0]["epoch_start_at"]
        )

        stale = self.collector.run_once()
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(
            self.collector.database.get_health()["node"]["health"],
            "healthy",
        )

        self.node.height += 1
        self.node.expected = 2
        self.collector.run_once()
        self.node.height += 1
        self.node.expected = 3
        self.collector.run_once()

        pillar_record = self.collector.database.get_pillar("alpha")
        self.assertEqual(pillar_record["status"], "inactive")
        self.assertEqual(
            len(
                self.collector.database.get_events(
                    event_type="pillar_inactive"
                )
            ),
            1,
        )

    def test_momentum_timestamp_can_be_used_for_a_live_epoch_start(self):
        self.assertEqual(
            self.collector._momentum_timestamp_as_utc(
                {"timestamp": 1787664230}
            ),
            "2026-08-25T13:23:50+00:00",
        )

    def test_runtime_settings_reload_without_restarting_collector(self):
        self.collector.database.set_settings(
            {
                **DEFAULT_SETTINGS,
                "node_rpc_urls": ["http://fake-node"],
                "missed_momentums_threshold": 7,
            }
        )

        with patch("controllers.collector_controller.configure_logging"):
            self.assertTrue(self.collector._reload_settings_if_changed())

        self.assertEqual(self.collector.missed_momentums_threshold, 7)
        self.assertEqual(
            self.collector._settings_revision,
            self.collector.database.get_settings_revision(),
        )
        self.assertFalse(self.collector._reload_settings_if_changed())

    def test_telegram_pillar_snapshot_is_reused_until_refreshed(self):
        source = pillar(3, 3)
        database_item = {
            "owner_address": "alpha",
            "is_present": True,
            "name": "Alpha",
            "rank": 0,
            "weight": 10000000000,
            "momentum_reward_percentage": 10,
            "delegate_reward_percentage": 90,
            "status": "active",
            "produced_momentums": 3,
            "expected_momentums": 3,
        }
        with patch.object(
            self.collector.database,
            "get_pillars",
            return_value={"items": [database_item]},
        ) as get_pillars:
            first = self.collector._get_pinned_pillars(source)
            second = self.collector._get_pinned_pillars()

        self.assertIs(first, second)
        self.assertEqual(first["alpha"]["name"], "Alpha")
        get_pillars.assert_called_once_with(
            status="all",
            include_performance=False,
        )

    def test_pinned_callback_changes_shared_page_and_filter(self):
        self.collector.config.update(
            {
                "telegram_channel_id": "-100channel",
                "telegram_pinned_message_id": 17,
            }
        )
        answers = []
        self.collector.dispatcher.telegram = SimpleNamespace(
            enabled=True,
            bot_answer_callback_query=lambda callback_id, text=None: answers.append(
                (callback_id, text)
            ),
            response_ok=lambda response: True,
        )
        with patch.object(
            self.collector,
            "_get_pinned_pillars",
            return_value={},
        ), patch.object(self.collector, "_edit_pinned_message") as edit:
            self.collector._handle_telegram_callback(
                {
                    "id": "callback-1",
                    "data": "pillar:page:inactive:2",
                    "message": {
                        "message_id": 17,
                        "chat": {"id": "-100channel"},
                    },
                }
            )

        self.assertEqual(self.collector._pinned_status, "inactive")
        self.assertEqual(self.collector._pinned_page, 2)
        edit.assert_called_once()
        self.assertEqual(answers, [("callback-1", None)])

    def test_private_bot_start_and_buttons_show_pillars(self):
        sent = []
        edited = []
        answers = []
        response = SimpleNamespace(status_code=200)
        self.collector.dispatcher.telegram = SimpleNamespace(
            enabled=True,
            bot_send_message_to_chat=lambda *args, **kwargs: sent.append(
                (args, kwargs)
            ) or response,
            bot_edit_message=lambda *args, **kwargs: edited.append(
                (args, kwargs)
            ) or response,
            bot_answer_callback_query=lambda callback_id, text=None: answers.append(
                (callback_id, text)
            ) or response,
            response_ok=lambda result: True,
        )
        pillars = {
            "alpha": {
                "name": "Alpha",
                "rank": 0,
                "weight": 100000000,
                "status": "active",
                "giveMomentumRewardPercentage": 10,
                "giveDelegateRewardPercentage": 90,
                "currentStats": {
                    "producedMomentums": 3,
                    "expectedMomentums": 3,
                },
            }
        }

        with patch.object(
            self.collector,
            "_get_pinned_pillars",
            return_value=pillars,
        ):
            self.collector._handle_telegram_message(
                {
                    "text": "/start",
                    "chat": {"id": 123, "type": "private"},
                }
            )
            self.collector._handle_telegram_callback(
                {
                    "id": "private-callback",
                    "data": "pillar:page:active:1",
                    "message": {
                        "message_id": 8,
                        "chat": {"id": 123, "type": "private"},
                    },
                }
            )

        self.assertEqual(len(sent), 1)
        self.assertIn("Online: 1 · Offline: 0", sent[0][0][1])
        self.assertFalse(
            any(
                button.get("url")
                for row in sent[0][1]["reply_markup"]["inline_keyboard"]
                for button in row
            )
        )
        self.assertEqual(edited[0][0][0:2], ("123", 8))
        self.assertEqual(answers, [("private-callback", None)])

    def test_telegram_update_polling_receives_private_messages(self):
        requests = []
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "ok": True,
                "result": [
                    {
                        "update_id": 41,
                        "message": {
                            "text": "/start",
                            "chat": {"id": 123, "type": "private"},
                        },
                    }
                ],
            },
        )
        self.collector.config["telegram_channel_id"] = "-100channel"
        self.collector.dispatcher.telegram = SimpleNamespace(
            enabled=True,
            bot_get_updates=lambda **kwargs: requests.append(kwargs) or response,
            response_ok=lambda result: True,
        )

        with patch.object(self.collector, "_handle_telegram_message") as handle:
            self.assertTrue(self.collector._process_telegram_updates())

        handle.assert_called_once()
        self.assertEqual(requests[0]["allowed_updates"], ("callback_query", "message"))
        self.assertEqual(self.collector._telegram_update_offset, 42)


if __name__ == "__main__":
    unittest.main()
