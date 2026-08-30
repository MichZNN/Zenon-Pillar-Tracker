from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from functions.subscriptions import normalise_subscription
from services.auth_service import hash_password, verify_password
from services.settings_service import DEFAULT_SETTINGS
from models.database import Database
from controllers.web_controller import DashboardHandler


class AdminFeatureTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "tracker.sqlite3")
        self.database.ensure_settings(DEFAULT_SETTINGS)
        self.admin = self.database.create_user(
            username="admin",
            display_name="Admin",
            password_hash=hash_password("correct horse battery"),
            role="admin",
        )
        self.user = self.database.create_user(
            username="operator",
            display_name="Operator",
            password_hash=hash_password("another correct password"),
        )
    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_auth_and_no_delete_subscription(self):
        session = self.database.create_session(self.admin["id"])
        self.assertTrue(
            self.database.verify_csrf_token(session["token"], session["csrf_token"])
        )
        self.assertFalse(
            self.database.verify_csrf_token(session["token"], "wrong-token")
        )
        self.assertTrue(verify_password("correct horse battery", self.database.get_user_credentials("admin")["password_hash"]))
        subscription = self.database.create_pillar_subscription(
            user_id=self.user["id"],
            channel_id="-100123",
            pillar_owner_addresses=["z1alpha"],
            events=["pillar_inactive"],
        )
        self.assertEqual(len(self.database.get_pillar_subscriptions()), 1)
        updated = self.database.update_pillar_subscription(
            subscription["id"], active=False, changed_by=self.admin["id"]
        )
        self.assertFalse(updated["active"])

    def test_role_scoped_subscription_records_and_no_delete_operation(self):
        subscription = self.database.create_pillar_subscription(
            user_id=self.user["id"],
            channel_id="-100123",
            pillar_owner_addresses=["z1alpha"],
            events=["pillar_inactive"],
        )
        self.assertEqual(
            [item["id"] for item in self.database.get_pillar_subscriptions(user_id=self.user["id"])],
            [subscription["id"]],
        )
        self.assertEqual(len(self.database.get_pillar_subscriptions(user_id=self.admin["id"])), 0)
        changed = self.database.update_pillar_subscription(
            subscription["id"], active=False, changed_by=self.admin["id"]
        )
        self.assertFalse(changed["active"])
        self.assertEqual(len(self.database.get_active_subscription_config()), 0)

    def test_subscription_accepts_telegram_discord_or_both(self):
        discord = "https://discord.com/api/webhooks/123/CaseSensitiveToken"
        self.assertEqual(
            normalise_subscription(
                {
                    "discord_webhook": discord,
                    "pillar_owner_addresses": ["z1alpha"],
                    "events": ["pillar_inactive"],
                }
            )["discord_webhook"],
            discord,
        )
        self.assertEqual(
            normalise_subscription(
                {
                    "channel_id": "-100123",
                    "discord_webhook": discord,
                    "pillar_owner_addresses": ["z1alpha"],
                    "events": ["pillar_inactive"],
                }
            )["channel_id"],
            "-100123",
        )
        with self.assertRaisesRegex(ValueError, "Telegram channel ID"):
            normalise_subscription({"pillar_owner_addresses": ["z1alpha"]})
        with self.assertRaisesRegex(ValueError, "valid HTTPS Discord"):
            normalise_subscription(
                {
                    "discord_webhook": "https://example.com/webhook",
                    "pillar_owner_addresses": ["z1alpha"],
                }
            )

    def test_user_can_update_display_name_and_password_with_current_password(self):
        handler = object.__new__(DashboardHandler)
        handler.server = SimpleNamespace(database=self.database)
        handler._require_csrf = lambda user: True
        handler._audit = lambda *args, **kwargs: None
        response = {}
        handler._send_json = lambda payload: response.update(payload)

        handler._account_update(
            self.user,
            {
                "display_name": "Updated operator",
                "current_password": "another correct password",
                "new_password": "a completely new password",
                "new_password_confirmation": "a completely new password",
            },
        )

        self.assertEqual(response["display_name"], "Updated operator")
        credentials = self.database.get_user_credentials("operator")
        self.assertTrue(
            verify_password("a completely new password", credentials["password_hash"])
        )
        self.assertFalse(
            verify_password("another correct password", credentials["password_hash"])
        )

    def test_user_profile_rejects_wrong_current_password_and_mismatched_passwords(self):
        handler = object.__new__(DashboardHandler)
        handler.server = SimpleNamespace(database=self.database)
        handler._require_csrf = lambda user: True
        handler._audit = lambda *args, **kwargs: None
        handler._send_json = lambda payload: None

        with self.assertRaisesRegex(ValueError, "Current password is incorrect"):
            handler._account_update(
                self.user,
                {
                    "display_name": "Operator",
                    "current_password": "wrong password",
                    "new_password": "a completely new password",
                    "new_password_confirmation": "a completely new password",
                },
            )
        with self.assertRaisesRegex(ValueError, "New passwords do not match"):
            handler._account_update(
                self.user,
                {
                    "display_name": "Operator",
                    "current_password": "another correct password",
                    "new_password": "a completely new password",
                    "new_password_confirmation": "different new password",
                },
            )

    def test_admin_collector_control_requires_csrf_and_audits_action(self):
        class FakeControl:
            def request(self, action):
                self.action = action
                return {
                    "ok": True,
                    "action": action,
                    "collector": {"running": True, "state": "running"},
                }

        control = FakeControl()
        handler = object.__new__(DashboardHandler)
        handler.server = SimpleNamespace(
            database=self.database,
            collector_control=control,
        )
        handler._require_csrf = lambda user: True
        audit = []
        handler._audit = lambda *args, **kwargs: audit.append((args, kwargs))
        response = {}
        handler._send_json = lambda payload: response.update(payload)

        handler._admin_collector_control(self.admin, {"action": "restart"})

        self.assertEqual(control.action, "restart")
        self.assertTrue(response["available"])
        self.assertEqual(response["collector"]["state"], "running")
        self.assertEqual(audit[0][0][1:3], ("collector_restart", "collector"))

    def test_admin_collector_control_rejects_unknown_action(self):
        handler = object.__new__(DashboardHandler)
        handler._require_csrf = lambda user: True
        with self.assertRaisesRegex(ValueError, "start, stop, or restart"):
            handler._admin_collector_control(self.admin, {"action": "exec"})

    def test_collector_diagnostics_are_available_without_control_bridge(self):
        poll_id = self.database.begin_poll()
        self.database.finish_poll(
            poll_id,
            "failed",
            error=(
                "All node RPC endpoints failed: "
                "http://127.0.0.1:35997: returned HTTP 400"
            ),
        )
        self.database.update_node_state(
            height=None,
            momentum_hash=None,
            momentum_timestamp=None,
            health="error",
            stale_count=0,
            last_success_at=None,
        )

        handler = object.__new__(DashboardHandler)
        handler.server = SimpleNamespace(database=self.database)

        diagnostics = handler._collector_diagnostics()

        self.assertEqual(diagnostics["label"], "Collector error")
        self.assertIn("HTTP 400", diagnostics["last_error"])


if __name__ == "__main__":
    unittest.main()
