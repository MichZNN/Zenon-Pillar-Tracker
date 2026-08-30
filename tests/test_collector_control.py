from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from deploy.bin.collector_control_bridge import (
    BridgeError,
    CollectorControlBridge,
)
from services.collector_control import CollectorControlClient


class CollectorControlTestCase(unittest.TestCase):
    def setUp(self):
        self.bridge = CollectorControlBridge("/srv/zenon-pillar-tracker")

    @staticmethod
    def compose_result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["docker", "compose"],
            returncode,
            stdout=stdout,
            stderr="",
        )

    def test_status_decodes_running_compose_container(self):
        output = '[{"Service":"collector","State":"running","Status":"Up 2 minutes","Name":"tracker-collector-1"}]'
        with patch(
            "deploy.bin.collector_control_bridge.subprocess.run",
            return_value=self.compose_result(output),
        ) as run:
            status = self.bridge.status()

        self.assertTrue(status["running"])
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["name"], "tracker-collector-1")
        self.assertIn("--format", run.call_args.args[0])
        self.assertIn("collector", run.call_args.args[0])

    def test_status_handles_container_that_has_not_been_created(self):
        with patch(
            "deploy.bin.collector_control_bridge.subprocess.run",
            return_value=self.compose_result("[]"),
        ):
            status = self.bridge.status()

        self.assertFalse(status["running"])
        self.assertEqual(status["state"], "not_created")

    def test_lifecycle_forwards_only_the_collector_service(self):
        status_output = '[{"Service":"collector","State":"running","Status":"Up","Name":"tracker-collector-1"}]'
        with patch(
            "deploy.bin.collector_control_bridge.subprocess.run",
            side_effect=[
                self.compose_result(status_output),
                self.compose_result(),
                self.compose_result(status_output),
            ],
        ) as run:
            result = self.bridge.handle({"action": "restart"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["collector"]["state"], "running")
        lifecycle_command = run.call_args_list[1].args[0]
        self.assertEqual(lifecycle_command[-2:], ["restart", "collector"])

    def test_bridge_rejects_arbitrary_actions(self):
        with self.assertRaises(BridgeError):
            self.bridge.handle({"action": "exec", "command": "rm -rf /"})

    def test_client_rejects_arbitrary_actions_before_connecting(self):
        client = CollectorControlClient("/tmp/no-such-collector-control.sock")
        with self.assertRaises(ValueError):
            client.request("exec")


if __name__ == "__main__":
    unittest.main()
