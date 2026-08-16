"""WB-44: password login and LAN exposure rules."""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import auth, guard, setup


class PasswordTest(unittest.TestCase):
    def test_hash_verifies_only_the_right_password(self):
        h = auth.hash_password("Korrekt-Pferd-Batterie")
        self.assertTrue(auth.verify_password("Korrekt-Pferd-Batterie", h))
        self.assertFalse(auth.verify_password("falsch", h))

    def test_hash_is_salted_and_never_stores_the_password(self):
        h1 = auth.hash_password("gleich")
        h2 = auth.hash_password("gleich")
        self.assertNotEqual(h1, h2)          # different salt each time
        self.assertNotIn("gleich", h1)

    def test_empty_password_refused(self):
        with self.assertRaises(ValueError):
            auth.hash_password("   ")


class TokenTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.secret = auth.load_or_create_secret(self.dir / "secret")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_token_roundtrip(self):
        t = auth.make_token(self.secret, ttl_seconds=60)
        self.assertTrue(auth.check_token(t, self.secret))

    def test_tampered_token_is_refused(self):
        t = auth.make_token(self.secret, ttl_seconds=60)
        self.assertFalse(auth.check_token(t[:-2] + "xx", self.secret))

    def test_expired_token_is_refused(self):
        t = auth.make_token(self.secret, ttl_seconds=-1)
        self.assertFalse(auth.check_token(t, self.secret))

    def test_token_from_another_secret_is_refused(self):
        other = auth.load_or_create_secret(self.dir / "secret2")
        self.assertFalse(auth.check_token(auth.make_token(other, 60), self.secret))

    def test_secret_survives_restart(self):
        again = auth.load_or_create_secret(self.dir / "secret")
        self.assertEqual(again, self.secret)


class ThrottleTest(unittest.TestCase):
    def test_failed_logins_are_slowed_down(self):
        gate = auth.LoginGate(max_attempts=3, window_seconds=60)
        for _ in range(3):
            gate.record_failure("1.2.3.4")
        self.assertTrue(gate.is_locked("1.2.3.4"))
        self.assertFalse(gate.is_locked("5.6.7.8"))
        gate.record_success("1.2.3.4")
        self.assertFalse(gate.is_locked("1.2.3.4"))


class LanGuardTest(unittest.TestCase):
    """With LAN mode on, private hosts are allowed but CSRF/rebinding are not."""

    LAN = {"lan": True, "port": 8765}

    def test_private_host_allowed_in_lan_mode(self):
        ok, _ = guard.check_read({"Host": "10.77.0.50:8765"}, 8765, lan=True)
        self.assertTrue(ok)

    def test_public_or_named_host_still_refused(self):
        for host in ("evil.test:8765", "8.8.8.8:8765"):
            ok, _ = guard.check_read({"Host": host}, 8765, lan=True)
            self.assertFalse(ok, host)

    def test_origin_must_match_the_host(self):
        ok, _ = guard.check_write({"Host": "10.77.0.50:8765",
                                   "Origin": "http://10.77.0.50:8765",
                                   "Content-Type": "application/json"}, 8765, lan=True)
        self.assertTrue(ok)
        ok, _ = guard.check_write({"Host": "10.77.0.50:8765",
                                   "Origin": "http://10.77.0.99:8765",
                                   "Content-Type": "application/json"}, 8765, lan=True)
        self.assertFalse(ok)

    def test_lan_host_refused_when_lan_mode_off(self):
        ok, _ = guard.check_read({"Host": "10.77.0.50:8765"}, 8765)
        self.assertFalse(ok)


class ConfigCommandTest(unittest.TestCase):
    """WB-50: a stranger must be able to set the password and switch LAN mode
    without writing Python."""

    def setUp(self):
        import json, tempfile
        from pathlib import Path
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = self.dir / "config.json"
        self.cfg.write_text(json.dumps({"port": 8765, "default_project": "/tmp"}))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_set_password_stores_only_the_hash(self):
        from werkbank import auth, setup
        setup.set_password(self.cfg, "Korrekt-Pferd-Batterie")
        import json
        cfg = json.loads(self.cfg.read_text())
        self.assertNotIn("Korrekt", json.dumps(cfg))
        self.assertTrue(auth.verify_password("Korrekt-Pferd-Batterie",
                                             cfg["password_hash"]))
        self.assertEqual(cfg["port"], 8765)          # nothing else touched

    def test_empty_password_refused(self):
        from werkbank import setup
        with self.assertRaises(ValueError):
            setup.set_password(self.cfg, "   ")

    def test_lan_toggle_needs_a_password_first(self):
        from werkbank import setup
        with self.assertRaises(ValueError) as cm:
            setup.set_lan(self.cfg, True)
        self.assertIn("Passwort", str(cm.exception))

    def test_lan_on_and_off(self):
        import json
        from werkbank import setup
        setup.set_password(self.cfg, "geheim-genug-123")
        msg = setup.set_lan(self.cfg, True)
        cfg = json.loads(self.cfg.read_text())
        self.assertTrue(cfg["lan"])
        self.assertEqual(cfg["host"], "0.0.0.0")
        self.assertIn(":8765", msg)                  # tells the phone address
        setup.set_lan(self.cfg, False)
        cfg = json.loads(self.cfg.read_text())
        self.assertFalse(cfg["lan"])
        self.assertEqual(cfg["host"], "127.0.0.1")


class ChangingThePasswordEndsSessionsTest(unittest.TestCase):
    """A 30-day cookie on a lost phone is a working key to a board whose tickets
    run shell commands. Changing the password is exactly what someone does in
    that situation — so it must actually take the key away. Before this, the
    signing secret survived a password change and every issued cookie kept
    working for its full time to live."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = self.dir / "config.json"
        self.cfg.write_text('{"port": 8765}', encoding="utf-8")
        self.secret_path = self.dir / "session-secret"

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_an_old_cookie_stops_working(self):
        secret = auth.load_or_create_secret(self.secret_path)
        token = auth.make_token(secret)
        self.assertTrue(auth.check_token(token, secret))

        setup.set_password(self.cfg, "neues-passwort", secret_path=self.secret_path)

        new_secret = auth.load_or_create_secret(self.secret_path)
        self.assertNotEqual(secret, new_secret, "the signing secret must change")
        self.assertFalse(auth.check_token(token, new_secret),
                         "the cookie from before the change must not verify")

    def test_a_fresh_login_works_afterwards(self):
        setup.set_password(self.cfg, "neues-passwort", secret_path=self.secret_path)
        secret = auth.load_or_create_secret(self.secret_path)
        self.assertTrue(auth.check_token(auth.make_token(secret), secret))

    def test_the_message_says_devices_were_logged_out(self):
        msg = setup.set_password(self.cfg, "pw", secret_path=self.secret_path)
        self.assertIn("abgemeldet", msg)

    def test_the_new_secret_is_private(self):
        setup.set_password(self.cfg, "pw", secret_path=self.secret_path)
        self.assertEqual(oct(self.secret_path.stat().st_mode)[-3:], "600")
