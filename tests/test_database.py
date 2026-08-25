from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import Database, utc_now


def make_pillar(
    name: str,
    *,
    produced: int,
    expected: int,
    rank: int = 0,
    weight: int = 10000000000,
    owner: str | None = None,
):
    owner = owner or f"z1{ name.lower() }"
    return {
        "name": name,
        "ownerAddress": owner,
        "currentStats": {
            "producedMomentums": produced,
            "expectedMomentums": expected,
        },
        "weight": weight,
        "giveMomentumRewardPercentage": 10,
        "giveDelegateRewardPercentage": 90,
        "rank": rank,
        "raw": {
            "name": name,
            "ownerAddress": owner,
            "currentStats": {
                "producedMomentums": produced,
                "expectedMomentums": expected,
            },
        },
    }


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "tracker.sqlite3")
        self.height = 100

    def tearDown(self):
        self.temp_dir.cleanup()

    def record(
        self,
        pillars,
        epoch=1,
        *,
        notification_channels=(),
        pillar_notification_channels=None,
        network_notification_channels=None,
        epoch_start_at=None,
        epoch_start_observed=False,
        epoch_start_inferred=None,
    ):
        self.height += 1
        run_id = self.database.begin_poll()
        result = self.database.record_observation(
            poll_run_id=run_id,
            observed_at=utc_now(),
            momentum={
                "height": self.height,
                "hash": f"hash-{self.height}",
                "timestamp": self.height,
            },
            epoch_data={
                "epoch": epoch,
                "znn_reward": 100,
                "qsr_reward": 20,
                "source_address": "reference",
                "epoch_start_at": epoch_start_at,
                "epoch_start_observed": epoch_start_observed,
                "epoch_start_inferred": (
                    epoch_start_inferred
                    if epoch_start_inferred is not None
                    else bool(epoch_start_at) and not epoch_start_observed
                ),
            },
            pillars=pillars,
            missed_momentums_threshold=3,
            notification_channels=notification_channels,
            pillar_notification_channels=pillar_notification_channels,
            network_notification_channels=network_notification_channels,
        )
        self.database.finish_poll(run_id, "success")
        return result

    def test_initial_snapshot_and_live_duration(self):
        self.record({"z1alpha": make_pillar("Alpha", produced=1, expected=1)})

        overview = self.database.get_overview()
        self.assertEqual(overview["pillar_counts"]["total"], 1)
        self.assertEqual(overview["pillar_counts"]["active"], 1)

        pillar = self.database.get_pillar("z1alpha")
        self.assertIsNotNone(pillar)
        self.assertTrue(pillar["is_present"])
        self.assertEqual(pillar["status"], "active")
        self.assertGreaterEqual(pillar["live_seconds"], 0)
        self.assertLess(pillar["live_seconds"], 5)
        self.assertEqual(len(pillar["history"]), 1)
        self.assertEqual(pillar["events"][0]["event_type"], "pillar_created")

    def test_epoch_start_at_is_persisted_without_changing_observation_times(self):
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=1, expected=1)},
            epoch_start_at="2026-08-23T13:30:00+00:00",
        )

        epoch = self.database.get_epochs(limit=1)[0]
        self.assertEqual(epoch["epoch_start_at"], "2026-08-23T13:30:00+00:00")
        self.assertTrue(epoch["epoch_start_inferred"])
        self.assertNotEqual(epoch["epoch_start_at"], epoch["last_seen_at"])

    def test_observed_epoch_start_overrides_an_estimated_start(self):
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=1, expected=1)},
            epoch=1,
            epoch_start_at="2026-08-25T13:30:00+00:00",
        )
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=2, expected=2)},
            epoch=1,
            epoch_start_at="2026-08-25T13:23:50+00:00",
            epoch_start_observed=True,
        )

        epoch = self.database.get_epochs(limit=1)[0]
        self.assertEqual(epoch["epoch_start_at"], "2026-08-25T13:23:50+00:00")
        self.assertFalse(epoch["epoch_start_inferred"])

    def test_performance_uses_counter_deltas_and_epoch_boundaries(self):
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=1, expected=1)},
            epoch=1,
        )
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=2, expected=2)},
            epoch=1,
        )
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=2, expected=3)},
            epoch=1,
        )
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=0, expected=1)},
            epoch=2,
        )
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=1, expected=2)},
            epoch=2,
        )

        performance = self.database.get_pillar_performance()["z1alpha"]

        self.assertEqual(performance["produced"], 2)
        self.assertEqual(performance["expected"], 3)
        self.assertEqual(performance["intervals"], 3)
        self.assertEqual(performance["percentage"], 66.67)
        self.assertEqual(len(performance["daily"]), 30)
        self.assertEqual(
            sum(point["produced"] for point in performance["daily"]),
            performance["produced"],
        )
        self.assertEqual(
            sum(point["expected"] for point in performance["daily"]),
            performance["expected"],
        )
        self.assertEqual(
            self.database.get_pillars()["items"][0]["performance_last_30_days"],
            performance,
        )

    def test_epoch_reset_does_not_mark_active_pillar_inactive(self):
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=10, expected=10)},
            epoch=1,
        )
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=0, expected=1)},
            epoch=2,
        )

        pillar = self.database.get_pillar("z1alpha")
        self.assertEqual(pillar["status"], "active")
        self.assertEqual(pillar["missed_momentums"], 0)
        self.assertEqual(self.database.get_events(event_type="pillar_inactive"), [])
        self.assertEqual(
            len(self.database.get_events(event_type="epoch_available")),
            1,
        )

    def test_successful_telegram_epoch_notification_sets_announcement_time(self):
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=1, expected=1)},
            epoch=1,
            epoch_start_at="2026-08-23T13:30:00+00:00",
        )
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=2, expected=2)},
            epoch=2,
            epoch_start_at="2026-08-24T13:30:00+00:00",
            notification_channels=("telegram",),
        )

        notification = [
            item
            for item in self.database.get_pending_notifications()
            if item["event_type"] == "epoch_available"
        ][0]
        self.database.mark_notification_sent(notification["id"])

        epoch = self.database.get_epochs(limit=1)[0]
        self.assertEqual(epoch["epoch_start_at"], "2026-08-24T13:30:00+00:00")
        self.assertIsNotNone(epoch["announcement_at"])
        self.assertEqual(epoch["announcement_source"], "telegram")
        self.assertFalse(epoch["announcement_inferred"])

    def test_announcement_backfill_recovers_sent_telegram_notifications(self):
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=1, expected=1)},
            epoch=1,
            epoch_start_at="2026-08-23T13:30:00+00:00",
        )
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=2, expected=2)},
            epoch=2,
            epoch_start_at="2026-08-24T13:30:00+00:00",
            notification_channels=("telegram",),
        )
        notification = [
            item
            for item in self.database.get_pending_notifications()
            if item["event_type"] == "epoch_available"
        ][0]
        self.database.mark_notification_sent(notification["id"])
        with self.database._connect() as connection:
            connection.execute(
                "UPDATE epochs SET announcement_at = NULL WHERE epoch = 2"
            )

        self.assertEqual(
            self.database.backfill_announcement_times_from_notifications(),
            1,
        )
        epoch = self.database.get_epochs(limit=1)[0]
        self.assertIsNotNone(epoch["announcement_at"])
        self.assertEqual(epoch["announcement_source"], "telegram")

    def test_inactive_transition_and_recovery_are_stored_once(self):
        pillar = make_pillar("Alpha", produced=10, expected=10)
        self.record({"z1alpha": pillar})
        for expected in (11, 12, 13):
            self.record(
                {
                    "z1alpha": make_pillar(
                        "Alpha",
                        produced=10,
                        expected=expected,
                    )
                }
            )

        current = self.database.get_pillar("z1alpha")
        self.assertEqual(current["status"], "inactive")
        self.assertEqual(current["missed_momentums"], 3)
        self.assertIsNotNone(current["status_since"])
        self.assertGreaterEqual(current["status_seconds"], 0)
        self.assertIsNotNone(current["status_duration"])
        self.assertEqual(
            len(self.database.get_events(event_type="pillar_inactive")),
            1,
        )

        self.record(
            {"z1alpha": make_pillar("Alpha", produced=11, expected=14)}
        )
        current = self.database.get_pillar("z1alpha")
        self.assertEqual(current["status"], "active")
        self.assertEqual(current["missed_momentums"], 0)
        self.assertEqual(
            len(self.database.get_events(event_type="pillar_active")),
            1,
        )

    def test_pillar_notification_routes_are_added_without_removing_global_route(self):
        route_config = {
            "z1alpha": {
                "pillar_inactive": ("telegram_chat:-100123",),
            }
        }
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=1, expected=1)},
            notification_channels=("telegram",),
            pillar_notification_channels=route_config,
        )
        for expected in (2, 3, 4):
            self.record(
                {"z1alpha": make_pillar("Alpha", produced=1, expected=expected)},
                notification_channels=("telegram",),
                pillar_notification_channels=route_config,
            )

        inactive_notifications = [
            item
            for item in self.database.get_pending_notifications()
            if item["event_type"] == "pillar_inactive"
        ]
        self.assertEqual(
            {item["channel"] for item in inactive_notifications},
            {"telegram", "telegram_chat:-100123"},
        )

    def test_network_event_routes_are_added_without_removing_global_route(self):
        network_routes = {
            "epoch_available": ("telegram_chat:-100epoch",),
        }
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=1, expected=1)},
            epoch=1,
            notification_channels=("telegram",),
            network_notification_channels=network_routes,
        )
        self.record(
            {"z1alpha": make_pillar("Alpha", produced=2, expected=2)},
            epoch=2,
            notification_channels=("telegram",),
            network_notification_channels=network_routes,
        )

        epoch_notifications = [
            item
            for item in self.database.get_pending_notifications()
            if item["event_type"] == "epoch_available"
        ]
        self.assertEqual(
            {item["channel"] for item in epoch_notifications},
            {"telegram", "telegram_chat:-100epoch"},
        )

    def test_dismantle_and_reappearance_keep_history(self):
        alpha = make_pillar("Alpha", produced=1, expected=1)
        beta = make_pillar(
            "Beta",
            produced=1,
            expected=1,
            rank=1,
            owner="z1beta",
        )
        self.record({"z1alpha": alpha, "z1beta": beta})
        self.record({"z1alpha": alpha})

        beta_current = self.database.get_pillar("z1beta")
        self.assertFalse(beta_current["is_present"])
        self.assertEqual(beta_current["status"], "dismantled")
        self.assertEqual(
            len(self.database.get_events(event_type="pillar_dismantled")),
            1,
        )

        self.record({"z1alpha": alpha, "z1beta": beta})
        beta_current = self.database.get_pillar("z1beta")
        self.assertTrue(beta_current["is_present"])
        self.assertEqual(beta_current["status"], "active")
        self.assertEqual(
            len(self.database.get_events(event_type="pillar_created")),
            3,
        )
        self.assertEqual(len(beta_current["history"]), 2)

    def test_epoch_history_backfill_stores_all_entries(self):
        run_id = self.database.begin_poll()
        self.database.record_observation(
            poll_run_id=run_id,
            observed_at=utc_now(),
            momentum={"height": 500},
            epoch_data={
                "epoch": 3,
                "znn_reward": 300,
                "qsr_reward": 30,
                "source_address": "reference",
            },
            epoch_history=[
                {
                    "epoch": 1,
                    "znn_reward": 100,
                    "qsr_reward": 10,
                    "source_address": "reference",
                },
                {
                    "epoch": 2,
                    "znn_reward": 200,
                    "qsr_reward": 20,
                    "source_address": "reference",
                },
                {
                    "epoch": 3,
                    "znn_reward": 300,
                    "qsr_reward": 30,
                    "source_address": "reference",
                },
            ],
            pillars={"z1alpha": make_pillar("Alpha", produced=1, expected=1)},
            notification_channels=(),
        )
        epochs = self.database.get_epochs(limit=10)
        self.assertEqual([row["epoch"] for row in epochs], [3, 2, 1])


if __name__ == "__main__":
    unittest.main()
