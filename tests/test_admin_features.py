from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.auth_service import hash_password, verify_password
from services.settings_service import DEFAULT_SETTINGS
from models.database import Database


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


if __name__ == "__main__":
    unittest.main()
