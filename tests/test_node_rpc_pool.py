from __future__ import annotations

import unittest

from utils.node_rpc_pool import NodeRpcPool
from utils.node_rpc_wrapper import NodeRpcError


def pillar_data():
    return {
        "pillars": {
            "alpha": {
                "name": "Alpha",
                "ownerAddress": "alpha",
                "currentStats": {
                    "producedMomentums": 10,
                    "expectedMomentums": 10,
                },
            }
        }
    }


class FakeRpcNode:
    def __init__(
        self,
        url: str,
        *,
        height: int = 100,
        sync_state: int = 2,
        fail_frontier: bool = False,
    ):
        self.node_url = url
        self.height = height
        self.sync_state = sync_state
        self.fail_frontier = fail_frontier

    def get_latest_momentum(self):
        if self.fail_frontier:
            raise NodeRpcError("node is offline")
        return {
            "height": self.height,
            "hash": f"hash-{self.height}",
            "timestamp": self.height,
        }

    def get_sync_info(self):
        return {
            "state": self.sync_state,
            "currentHeight": self.height,
            "targetHeight": self.height,
        }

    def get_all_pillars(self):
        return pillar_data()

    def get_reward_epoch(self, address):
        return {
            "epoch": 1,
            "znn_reward": 100,
            "qsr_reward": 10,
            "source_address": address,
        }


class NodeRpcPoolTestCase(unittest.TestCase):
    def collect(self, pool, previous_height=None, previous_hash=None):
        return pool.collect_snapshot(
            reference_reward_address="reference",
            previous_height=previous_height,
            previous_hash=previous_hash,
            previous_epoch=1,
            reward_page_size=100,
            allow_empty_pillars=False,
        )

    def test_offline_primary_uses_backup(self):
        primary = FakeRpcNode("http://primary", fail_frontier=True)
        backup = FakeRpcNode("http://backup", height=101)
        pool = NodeRpcPool(
            [primary, backup],
            failure_cooldown_seconds=120,
        )

        result = self.collect(pool)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.node_url, "http://backup")
        self.assertEqual(pool.active_node_url, "http://backup")

    def test_unsynchronized_primary_uses_backup(self):
        primary = FakeRpcNode("http://primary", sync_state=1)
        backup = FakeRpcNode("http://backup", height=101)
        pool = NodeRpcPool([primary, backup])

        result = self.collect(pool)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.node_url, "http://backup")

    def test_same_height_hash_change_is_reported_as_reorg(self):
        node = FakeRpcNode("http://node", height=100)
        pool = NodeRpcPool([node])

        result = self.collect(
            pool,
            previous_height=100,
            previous_hash="different-hash",
        )

        self.assertEqual(result.status, "reorg")
        self.assertEqual(result.latest_momentum["height"], 100)


if __name__ == "__main__":
    unittest.main()
