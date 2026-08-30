from __future__ import annotations

import unittest
from datetime import datetime, timezone

from functions.status import collector_liveness_timeout, collector_status


class CollectorStatusTestCase(unittest.TestCase):
    NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

    def node(self, *, health="healthy", updated_at="2026-08-27T11:59:30+00:00", last_success_at=None):
        return {
            "health": health,
            "updated_at": updated_at,
            "last_success_at": last_success_at or updated_at,
        }

    def test_timeout_allows_two_poll_intervals_with_a_two_minute_floor(self):
        self.assertEqual(collector_liveness_timeout(60), 120)
        self.assertEqual(collector_liveness_timeout(90), 180)
        self.assertEqual(collector_liveness_timeout("invalid"), 120)

    def test_recent_healthy_report_is_green(self):
        result = collector_status(self.node(), now=self.NOW, poll_interval_seconds=60)

        self.assertEqual(result["state"], "green")
        self.assertEqual(result["label"], "Running normally")
        self.assertEqual(result["age_seconds"], 30)

    def test_stale_or_reorg_report_needs_attention(self):
        for health in ("stale", "reorg"):
            with self.subTest(health=health):
                result = collector_status(
                    self.node(health=health),
                    now=self.NOW,
                    poll_interval_seconds=60,
                )
                self.assertEqual(result["state"], "orange")
                self.assertEqual(result["label"], "Needs attention")

    def test_old_healthy_report_is_red(self):
        result = collector_status(
            self.node(updated_at="2026-08-27T11:57:00+00:00"),
            now=self.NOW,
            poll_interval_seconds=60,
        )

        self.assertEqual(result["state"], "red")
        self.assertEqual(result["label"], "Tracker offline")
        self.assertEqual(result["age_seconds"], 180)

    def test_error_report_is_red_and_no_report_is_unknown(self):
        error = collector_status(
            self.node(health="error"),
            now=self.NOW,
            poll_interval_seconds=60,
        )
        unknown = collector_status(
            {"health": "unknown", "updated_at": "1970-01-01T00:00:00+00:00"},
            now=self.NOW,
            poll_interval_seconds=60,
        )

        self.assertEqual(error["state"], "red")
        self.assertEqual(unknown["state"], "unknown")
        self.assertEqual(unknown["label"], "Waiting for tracker")

    def test_latest_failed_poll_exposes_the_exact_collector_error(self):
        result = collector_status(
            self.node(health="error"),
            last_run={
                "id": 42,
                "status": "failed",
                "started_at": "2026-08-27T11:59:30+00:00",
                "completed_at": "2026-08-27T11:59:31+00:00",
                "error": (
                    "All node RPC endpoints failed: "
                    "http://127.0.0.1:35997: ledger.getFrontierMomentum "
                    "returned HTTP 400"
                ),
            },
            now=self.NOW,
        )

        self.assertEqual(result["state"], "red")
        self.assertEqual(result["label"], "Collector error")
        self.assertEqual(result["last_attempt_status"], "failed")
        self.assertEqual(result["last_run_id"], 42)
        self.assertIn("HTTP 400", result["last_error"])


if __name__ == "__main__":
    unittest.main()
