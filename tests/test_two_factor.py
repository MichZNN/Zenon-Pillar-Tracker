from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from controllers.web_controller import (
    KEY_STATE,
    TWO_FACTOR_THROTTLE,
    TwoFactorThrottle,
    seal_stored_totp_secrets,
    ApiRateLimiter,
    DashboardHandler,
    DashboardServer,
)
from models.database import Database
from services.auth_service import (
    TOTP_PERIOD_SECONDS,
    generate_recovery_codes,
    hash_password,
    hash_recovery_code,
    hash_token,
    totp_code,
    totp_uri,
    verify_recovery_code,
    verify_totp,
)
from services import secret_box
from services.secret_box import SecretKeyError, generate_key, open_secret, seal_secret

TEST_KEY = generate_key()


def with_key(key):
    """Patch the TOTP_ENCRYPTION_KEY lookup for the duration of a block."""
    return patch.object(secret_box, "get_env_value", return_value=key)


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


class RecoveryCodeHashTestCase(unittest.TestCase):
    def test_salted_hash_verifies_only_the_right_code(self):
        stored = hash_recovery_code("k3m9q-2xw7p")
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        # A fresh salt every time: the same code never gives the same hash.
        self.assertNotEqual(stored, hash_recovery_code("k3m9q-2xw7p"))
        self.assertTrue(verify_recovery_code("K3M9Q 2XW7P", stored))
        self.assertFalse(verify_recovery_code("k3m9q-2xw7q", stored))
        self.assertFalse(verify_recovery_code("", stored))

    def test_malformed_stored_hashes_never_match(self):
        for stored in (
            "pbkdf2_sha256$120000$!!!$!!!",
            "md5$120000$c2FsdA$ZGlnZXN0",
            "pbkdf2_sha256$500$c2FsdA$ZGlnZXN0",
            "pbkdf2_sha256$99999999$c2FsdA$ZGlnZXN0",
            "pbkdf2_sha256$x$c2FsdA$ZGlnZXN0",
        ):
            self.assertFalse(verify_recovery_code("k3m9q-2xw7p", stored), stored)

    def test_codes_stored_before_salting_still_work(self):
        legacy = hash_token("k3m9q2xw7p")
        self.assertTrue(verify_recovery_code("k3m9q-2xw7p", legacy))
        self.assertFalse(verify_recovery_code("aaaaa-bbbbb", legacy))


class SecretBoxTestCase(unittest.TestCase):
    def test_without_a_key_secrets_stay_plain(self):
        with with_key(""):
            self.assertEqual(seal_secret("ABCDEF"), "ABCDEF")
            self.assertEqual(open_secret("ABCDEF"), "ABCDEF")

    def test_several_keys_rotate_to_the_first(self):
        old_key, new_key = generate_key(), generate_key()
        with with_key(old_key):
            sealed = seal_secret("ABCDEF")
        with with_key(f"{new_key},{old_key}"):
            self.assertEqual(open_secret(sealed), "ABCDEF")
            resealed = secret_box.reseal_secret(sealed)
        with with_key(new_key):
            self.assertEqual(open_secret(resealed), "ABCDEF")

    def test_with_a_key_secrets_are_encrypted(self):
        with with_key(TEST_KEY):
            sealed = seal_secret("ABCDEF")
            self.assertTrue(sealed.startswith("enc:v1:"))
            self.assertNotIn("ABCDEF", sealed)
            self.assertEqual(open_secret(sealed), "ABCDEF")
            # Plain values from before the key keep working.
            self.assertEqual(open_secret("ABCDEF"), "ABCDEF")
        with with_key(""), self.assertRaises(SecretKeyError):
            open_secret(sealed)
        with with_key(generate_key()), self.assertRaises(SecretKeyError):
            open_secret(sealed)
        with with_key("not a key"), self.assertRaises(SecretKeyError):
            seal_secret("ABCDEF")


class TwoFactorWebTestCase(unittest.TestCase):
    def setUp(self):
        # The slow recovery-code hash is tested on its own above. Here the
        # minimum iteration count keeps the HTTP tests fast, also under the
        # emulated ARM64 runner in CI (QEMU is many times slower).
        iterations = patch("services.auth_service.RECOVERY_CODE_ITERATIONS", 10_000)
        iterations.start()
        self.addCleanup(iterations.stop)
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
            with urlopen(request, timeout=30) as response:
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
        self.assertEqual(
            info,
            {
                "enabled": True,
                "recovery_codes_left": 9,
                "recovery_codes_outdated": False,
                "trusted_devices": [],
            },
        )

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


    def stored_secret(self, user_id):
        with self.database._connect() as connection:
            return connection.execute(
                "SELECT totp_secret FROM users WHERE id = ?", (user_id,)
            ).fetchone()[0]

    def test_secret_is_stored_encrypted_when_a_key_is_set(self):
        with with_key(TEST_KEY):
            status, _, session, _ = self.login("operator", "another correct password")
            status, setup, _ = self.request("POST", "/api/account/two-factor/setup", {}, session)
            secret = setup["secret"]
            status, enabled, _ = self.request(
                "POST", "/api/account/two-factor/enable",
                {"code": self.current_code(secret)}, session,
            )
            self.assertEqual(status, 200)
            stored = self.stored_secret(self.user["id"])
            self.assertTrue(stored.startswith("enc:v1:"))
            self.assertNotIn(secret, stored)
            status, _, session, _ = self.login(
                "operator", "another correct password", self.current_code(secret, 1)
            )
            self.assertEqual(status, 200)
        # Without the key the authenticator code cannot be checked, but a
        # recovery code still lets the user in.
        with with_key(""):
            status, _, _, _ = self.login(
                "operator", "another correct password", self.current_code(secret, 2)
            )
            self.assertEqual(status, 401)
            status, _, _, _ = self.login(
                "operator", "another correct password", enabled["recovery_codes"][0]
            )
            self.assertEqual(status, 200)

    def test_plain_secrets_are_encrypted_at_startup_with_a_key(self):
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.database.enable_totp(self.user["id"], secret=secret, step=0, recovery_code_hashes=[])
        with with_key(""):
            self.assertEqual(seal_stored_totp_secrets(self.database), 0)
        self.assertEqual(self.stored_secret(self.user["id"]), secret)
        with with_key(TEST_KEY):
            self.assertEqual(seal_stored_totp_secrets(self.database), 1)
            stored = self.stored_secret(self.user["id"])
            self.assertTrue(stored.startswith("enc:v1:"))
            self.assertEqual(open_secret(stored), secret)
            # Running it again changes nothing.
            self.assertEqual(seal_stored_totp_secrets(self.database), 0)


    def test_invalid_key_does_not_stop_startup(self):
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.database.enable_totp(self.user["id"], secret=secret, step=0, recovery_code_hashes=[])
        with with_key("not a key"):
            self.assertEqual(seal_stored_totp_secrets(self.database), 0)
        self.assertEqual(self.stored_secret(self.user["id"]), secret)

    def test_setup_with_an_invalid_key_gives_a_clear_error(self):
        status, _, session, _ = self.login("operator", "another correct password")
        with with_key("not a key"):
            status, payload, _ = self.request("POST", "/api/account/two-factor/setup", {}, session)
        self.assertEqual(status, 400)
        self.assertIn("unavailable", payload["error"])

    def test_setup_is_refused_while_encrypted_secrets_lack_the_key(self):
        with with_key(TEST_KEY):
            self.database.enable_totp(
                self.admin["id"], secret=seal_secret("ABCDEFGH"), step=0, recovery_code_hashes=[]
            )
        status, _, session, _ = self.login("operator", "another correct password")
        with with_key(""):
            status, payload, _ = self.request("POST", "/api/account/two-factor/setup", {}, session)
        self.assertEqual(status, 400)
        self.assertIn("unavailable", payload["error"])

    def test_old_unsalted_recovery_codes_work_once_and_are_flagged(self):
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.database.enable_totp(
            self.user["id"], secret=secret, step=0,
            recovery_code_hashes=[hash_token("k3m9q2xw7p")],
        )
        status, _, session, _ = self.login("operator", "another correct password", "k3m9q-2xw7p")
        self.assertEqual(status, 200)
        status, _, _, _ = self.login("operator", "another correct password", "k3m9q-2xw7p")
        self.assertEqual(status, 401)
        self.database.set_recovery_codes(self.user["id"], [hash_token("aaaaabbbbb")])
        status, info, _ = self.request("GET", "/api/account/two-factor", session=session)
        self.assertTrue(info["recovery_codes_outdated"])

    def test_checking_recovery_codes_does_not_hold_the_write_lock(self):
        import sqlite3
        self.database.set_recovery_codes(self.user["id"], [hash_recovery_code("k3m9q-2xw7p")])
        writable = []

        def matches(stored):
            # While a code is being checked, another writer must get through.
            other = sqlite3.connect(self.database.path, timeout=0.2)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.rollback()
                writable.append(True)
            except sqlite3.OperationalError:
                writable.append(False)
            finally:
                other.close()
            return verify_recovery_code("k3m9q-2xw7p", stored)

        self.assertTrue(self.database.consume_recovery_code(self.user["id"], matches))
        self.assertEqual(writable, [True])
        self.assertFalse(self.database.consume_recovery_code(self.user["id"], matches))


    def reset_key_state(self):
        KEY_STATE["mismatch"] = False

    def test_all_digit_recovery_code_is_accepted(self):
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.database.enable_totp(
            self.user["id"], secret=secret, step=0,
            recovery_code_hashes=[hash_recovery_code("23456-78923")],
        )
        status, _, _, _ = self.login("operator", "another correct password", "23456-78923")
        self.assertEqual(status, 200)

    def test_wrong_key_writes_nothing_and_refuses_setup(self):
        self.addCleanup(self.reset_key_state)
        with with_key(TEST_KEY):
            self.database.enable_totp(
                self.admin["id"], secret=seal_secret("ABCDEFGH"), step=0, recovery_code_hashes=[]
            )
        plain = base64.b32encode(b"12345678901234567890").decode()
        self.database.enable_totp(self.user["id"], secret=plain, step=0, recovery_code_hashes=[])
        with with_key(generate_key()):
            self.assertEqual(seal_stored_totp_secrets(self.database), 0)
            self.assertEqual(self.stored_secret(self.user["id"]), plain)
            self.assertTrue(KEY_STATE["mismatch"])
        # With the right key back, everything opens and is encrypted.
        with with_key(TEST_KEY):
            self.assertEqual(seal_stored_totp_secrets(self.database), 1)
            self.assertFalse(KEY_STATE["mismatch"])
            self.assertEqual(open_secret(self.stored_secret(self.user["id"])), plain)

    def test_corrupted_value_does_not_stop_startup(self):
        self.database.enable_totp(
            self.user["id"], secret="enc:v1:\u00e9\u00e9", step=0, recovery_code_hashes=[]
        )
        self.addCleanup(self.reset_key_state)
        with with_key(TEST_KEY):
            self.assertEqual(seal_stored_totp_secrets(self.database), 0)

    def test_two_keys_reencrypt_once(self):
        old_key, new_key = generate_key(), generate_key()
        with with_key(old_key):
            self.database.enable_totp(
                self.user["id"], secret=seal_secret("ABCDEFGH"), step=0, recovery_code_hashes=[]
            )
        with with_key(f"{new_key},{old_key}"):
            self.assertEqual(seal_stored_totp_secrets(self.database), 1)
            self.assertEqual(seal_stored_totp_secrets(self.database), 0)
        with with_key(new_key):
            self.assertEqual(open_secret(self.stored_secret(self.user["id"])), "ABCDEFGH")

    def test_concurrent_use_of_one_recovery_code_succeeds_once(self):
        self.database.set_recovery_codes(self.user["id"], [hash_recovery_code("k3m9q-2xw7p")])
        results = []
        threads = [
            Thread(target=lambda: results.append(self.database.consume_recovery_code(
                self.user["id"], lambda stored: verify_recovery_code("k3m9q-2xw7p", stored)
            )))
            for _ in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(True), 1)


class TwoFactorThrottleTestCase(unittest.TestCase):
    def test_parallel_attempts_cannot_exceed_the_limit(self):
        throttle = TwoFactorThrottle(max_failures=5, window_seconds=600)
        allowed = []
        threads = [Thread(target=lambda: allowed.append(throttle.try_attempt(7))) for _ in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(allowed.count(True), 5)
        throttle.reset(7)
        self.assertTrue(throttle.try_attempt(7))


if __name__ == "__main__":
    unittest.main()
