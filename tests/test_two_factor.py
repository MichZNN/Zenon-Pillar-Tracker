from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from controllers.web_controller import (
    TWO_FACTOR_THROTTLE,
    ApiRateLimiter,
    DashboardHandler,
    DashboardServer,
)
from models.database import Database
from services.auth_service import (
    TOTP_PERIOD_SECONDS,
    generate_recovery_codes,
    hash_password,
    totp_code,
    totp_uri,
    verify_totp,
)


class TotpTestCase(unittest.TestCase):
    def test_rfc6238_vector_and_replay_protection(self):
        # RFC 6238 SHA-1 test key; at T=59s the 8-digit code is 94287082.
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(totp_code(secret, 59 // 30), "287082")
        self.assertEqual(verify_totp(secret, "287 082", now=59), 1)
        # The same step cannot be used twice.
        self.assertIsNone(verify_totp(secret, "287082", now=59, last_step=1))
        self.assertIsNone(verify_totp(secret, "000000", now=59))
        self.assertIsNone(verify_totp(secret, "12345", now=59))

    def test_uri_and_recovery_codes(self):
        uri = totp_uri("ABC", "mich")
        self.assertTrue(uri.startswith("otpauth://totp/Zenon%20Pillar%20Tracker%3Amich?secret=ABC"))
        codes = generate_recovery_codes()
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        self.assertRegex(codes[0], r"^[a-z2-9]{5}-[a-z2-9]{5}$")


class TwoFactorWebTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "tracker.sqlite3")
        self.admin = self.database.create_user(
            username="admin",
            password_hash=hash_password("correct horse battery"),
            role="admin",
        )
        self.user = self.database.create_user(
            username="operator",
            password_hash=hash_password("another correct password"),
        )
        templates_dir = Path(__file__).parents[1] / "templates"
        try:
            self.server = DashboardServer(
                ("127.0.0.1", 0),
                DashboardHandler,
                self.database,
                templates_dir,
                templates_dir.parent / "static",
                ApiRateLimiter(max_requests=0),
                {},
            )
        except PermissionError as exc:
            self.skipTest(f"Local socket creation is unavailable: {exc}")
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}"
        TWO_FACTOR_THROTTLE.reset(self.user["id"])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp_dir.cleanup()

    def request(self, method, path, body=None, session=None, cookies=None):
        headers = {"Content-Type": "application/json"}
        cookie_values = dict(cookies or {})
        if session:
            cookie_values["zenon_tracker_session"] = session["token"]
            cookie_values["zenon_tracker_csrf"] = session["csrf"]
            headers["X-CSRF-Token"] = session["csrf"]
        if cookie_values:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_values.items())
        request = Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read()), response.headers
        except HTTPError as error:
            payload = json.loads(error.read())
            error.close()
            return error.code, payload, error.headers

    @staticmethod
    def set_cookies(headers):
        cookies = {}
        for value in headers.get_all("Set-Cookie") or []:
            name, _, rest = value.partition("=")
            cookies[name] = rest.split(";", 1)[0]
        return cookies

    def session_from(self, payload, headers):
        cookies = self.set_cookies(headers)
        return {"token": cookies["zenon_tracker_session"], "csrf": payload["csrf_token"]}

    def login(self, username, password, code=None, *, trust=False, cookies=None):
        """Run the login steps; returns (status, payload, session, set-cookies)."""
        status, payload, headers = self.request(
            "POST",
            "/api/auth/login",
            {"username": username, "password": password},
            cookies=cookies,
        )
        if status == 200 and payload.get("two_factor_required"):
            if code is None:
                return status, payload, None, {}
            status, payload, headers = self.request(
                "POST",
                "/api/auth/login/verify",
                {"challenge": payload["challenge"], "code": code, "trust_device": trust},
            )
        if status == 200 and payload.get("authenticated"):
            return status, payload, self.session_from(payload, headers), self.set_cookies(headers)
        return status, payload, None, {}

    def current_code(self, secret, offset=0):
        return totp_code(secret, int(time.time() // TOTP_PERIOD_SECONDS) + offset)

    def test_enable_login_recovery_and_disable(self):
        status, _, session, _ = self.login("operator", "another correct password")
        self.assertEqual(status, 200)

        status, setup, _ = self.request("POST", "/api/account/two-factor/setup", {}, session)
        self.assertEqual(status, 200)
        secret = setup["secret"]
        self.assertIn(secret, setup["otpauth_uri"])

        status, payload, _ = self.request(
            "POST", "/api/account/two-factor/enable", {"code": "000000"}, session
        )
        self.assertEqual(status, 400)
        status, enabled, _ = self.request(
            "POST", "/api/account/two-factor/enable",
            {"code": self.current_code(secret)}, session,
        )
        self.assertEqual(status, 200)
        recovery_codes = enabled["recovery_codes"]
        self.assertEqual(len(recovery_codes), 10)

        # The secret and recovery codes never appear in user data.
        status, me, _ = self.request("GET", "/api/auth/me", session=session)
        self.assertTrue(me["user"]["two_factor_enabled"])
        self.assertNotIn(secret, json.dumps(me))
        self.assertNotIn("totp_secret", json.dumps(me))

        # Step 1 only checks the password and hands out a challenge, no session.
        status, payload, session_none, cookies = self.login("operator", "another correct password")
        self.assertEqual(status, 200)
        self.assertTrue(payload["two_factor_required"])
        self.assertTrue(payload["challenge"])
        self.assertNotIn("csrf_token", payload)
        self.assertIsNone(session_none)
        status, payload, _, _ = self.login("operator", "wrong password!!!", "123456")
        self.assertEqual(status, 401)
        status, payload, _, _ = self.login("operator", "another correct password", "123456")
        self.assertEqual(status, 401)
        status, _, session, _ = self.login(
            "operator", "another correct password", self.current_code(secret, 1)
        )
        self.assertEqual(status, 200)
        # An unknown or used challenge asks to start again.
        status, payload, _ = self.request(
            "POST", "/api/auth/login/verify", {"challenge": "nope", "code": "123456"}
        )
        self.assertEqual(status, 401)
        self.assertTrue(payload["restart"])

        # A recovery code works once.
        status, _, _, _ = self.login("operator", "another correct password", recovery_codes[0])
        self.assertEqual(status, 200)
        status, _, _, _ = self.login("operator", "another correct password", recovery_codes[0])
        self.assertEqual(status, 401)
        status, info, _ = self.request("GET", "/api/account/two-factor", session=session)
        self.assertEqual(info, {"enabled": True, "recovery_codes_left": 9, "trusted_devices": []})

        # Turning it off needs the password and a code.
        status, _, _ = self.request(
            "POST", "/api/account/two-factor/disable",
            {"password": "wrong password!!", "code": recovery_codes[1]}, session,
        )
        self.assertEqual(status, 400)
        status, payload, _ = self.request(
            "POST", "/api/account/two-factor/disable",
            {"password": "another correct password", "code": recovery_codes[1]}, session,
        )
        self.assertEqual(status, 200)
        status, payload, _, _ = self.login("operator", "another correct password")
        self.assertTrue(payload.get("authenticated"))

    def test_wrong_codes_are_throttled_and_admin_can_reset(self):
        secret = "JBSWY3DPEHPK3PXP"
        self.database.set_pending_totp_secret(self.user["id"], secret)
        self.database.enable_totp(
            self.user["id"], secret=secret, step=0, recovery_code_hashes=[]
        )
        for _ in range(5):
            status, _, _, _ = self.login("operator", "another correct password", "000000")
            self.assertEqual(status, 401)
        status, payload, _, _ = self.login(
            "operator", "another correct password", self.current_code(secret)
        )
        self.assertEqual(status, 429)

        status, _, admin_session, _ = self.login("admin", "correct horse battery")
        self.assertEqual(status, 200)
        status, updated, _ = self.request(
            "PATCH", f"/api/admin/users/{self.user['id']}",
            {"reset_two_factor": True}, admin_session,
        )
        self.assertEqual(status, 200)
        self.assertFalse(updated["two_factor_enabled"])
        status, payload, _, _ = self.login("operator", "another correct password")
        self.assertTrue(payload.get("authenticated"))

    def test_trusted_device_skips_the_code_until_revoked(self):
        secret = "JBSWY3DPEHPK3PXP"
        self.database.enable_totp(self.user["id"], secret=secret, step=0, recovery_code_hashes=[])

        status, _, session, cookies = self.login(
            "operator", "another correct password", self.current_code(secret), trust=True
        )
        self.assertEqual(status, 200)
        trusted = {"zenon_tracker_trusted": cookies["zenon_tracker_trusted"]}
        self.assertNotIn(cookies["zenon_tracker_trusted"], json.dumps(self.database.list_trusted_devices(self.user["id"])))

        # With the cookie the password is enough; without it a code is asked.
        status, payload, _, _ = self.login("operator", "another correct password", cookies=trusted)
        self.assertTrue(payload.get("authenticated"))
        status, payload, _, _ = self.login("operator", "another correct password")
        self.assertTrue(payload.get("two_factor_required"))
        # The cookie is tied to this user.
        self.database.enable_totp(self.admin["id"], secret=secret, step=0, recovery_code_hashes=[])
        status, payload, _, _ = self.login("admin", "correct horse battery", cookies=trusted)
        self.assertTrue(payload.get("two_factor_required"))

        status, info, _ = self.request("GET", "/api/account/two-factor", session=session)
        self.assertEqual(len(info["trusted_devices"]), 1)
        status, info, _ = self.request("POST", "/api/account/two-factor/forget-devices", {}, session)
        self.assertEqual(info["trusted_devices"], [])
        status, payload, _, _ = self.login("operator", "another correct password", cookies=trusted)
        self.assertTrue(payload.get("two_factor_required"))

        # A password change also ends the trust.
        status, _, session, cookies = self.login(
            "operator", "another correct password", self.current_code(secret, 1), trust=True
        )
        trusted = {"zenon_tracker_trusted": cookies["zenon_tracker_trusted"]}
        self.database.update_user(self.user["id"], password_hash=hash_password("a brand new password"))
        status, payload, _, _ = self.login("operator", "a brand new password", cookies=trusted)
        self.assertTrue(payload.get("two_factor_required"))


if __name__ == "__main__":
    unittest.main()
