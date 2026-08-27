from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from controllers.collector_controller import Collector
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


if __name__ == "__main__":
    unittest.main()
