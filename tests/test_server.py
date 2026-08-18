"""WB-108: the board's HTTP handler as a COMPOSITION.

Every other test in the suite exercises the pure functions (auth.verify_password,
guard.check_write, store.*). None of them ever built a real request path through
the real handler — so if the auth check disappears from server.do_GET, or the
browse roots widen to `/`, the pure-function tests stay green. This file plugs
that hole: one ThreadingHTTPServer on an OS-picked port, module-level globals
swapped to an isolated temp Werkbank, and the two documented seams get
red-when-broken tests. Kept small: it is about the WIRING (auth-gate + guard +
route dispatch), not about every endpoint.
"""

import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import auth, dispatch, server, store  # noqa: E402

# WB-144: importing server must NOT spawn a Dispatcher (that used to give
# every test file a background ticker against the real tickets dir). The
# module now constructs it lazily in server.main(); DISPATCHER stays None
# after import, setUpClass installs one scoped to the tempdir.
if getattr(server, "DISPATCHER", None) is not None:
    server.DISPATCHER.stop()   # bootstrap safety if the import ever regresses


class LazyDispatcherTest(unittest.TestCase):
    """WB-144: importing server.py must not start a Dispatcher — a ticker
    running against the real repo the second a test file imports the module
    is exactly how the swarm night's rogue helpers got a foothold."""

    def test_import_alone_does_not_leave_a_ticker(self):
        import importlib, threading
        # Snapshot tickers OTHER tests may have running (this file runs
        # alongside test_dispatch.py, whose helpers spawn dispatchers).
        before = {id(t) for t in threading.enumerate()
                  if t.name == dispatch.TICKER_THREAD_NAME and t.is_alive()}
        srv = importlib.reload(server)
        try:
            self.assertIsNone(getattr(srv, "DISPATCHER", "sentinel"),
                              "importing server must leave DISPATCHER at None")
            after = {id(t) for t in threading.enumerate()
                     if t.name == dispatch.TICKER_THREAD_NAME and t.is_alive()}
            new = after - before
            self.assertEqual(new, set(),
                             "reload(server) alone must not spawn a queue ticker "
                             f"(new tickers: {len(new)})")
        finally:
            # Never leave a live dispatcher for the next test file to trip over.
            if getattr(srv, "DISPATCHER", None) is not None:
                srv.DISPATCHER.stop()


class BoardCompositionTest(unittest.TestCase):
    """The wired handler under a real HTTP server, LAN + password ON so the
    auth check is meaningful (auth_required() is False without both)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="werkbank-server-test-"))
        cls.tickets_dir = cls.dir / "tickets"; cls.tickets_dir.mkdir()
        cls.upload_dir = cls.dir / "docs" / "images"; cls.upload_dir.mkdir(parents=True)

        cls.password = "geheim-nur-fuer-den-test"
        cls.pw_hash = auth.hash_password(cls.password)
        cfg = {
            "port": 0, "host": "127.0.0.1", "lan": True,
            "password_hash": cls.pw_hash,
            "default_project": str(cls.dir),
            "projects": {"Test": str(cls.dir)},
            "nonblocking_review": {}, "gates": {},
            "state_path": str(cls.dir / "state.json"),
            "config_exists": True, "repo_root": str(cls.dir),
        }
        # Write it to disk too, so _hot_reload's stat/parse succeeds silently.
        (cls.dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

        # Snapshot module-level names so tearDownClass restores them cleanly.
        cls._saved = {name: getattr(server, name) for name in
                      ("CONFIG", "LAN", "TICKETS_DIR", "UPLOAD_DIR",
                       "REPO_ROOT", "SECRET", "LOGIN_GATE", "DISPATCHER")}
        server.CONFIG.clear(); server.CONFIG.update(cfg)
        server.LAN = True
        server.TICKETS_DIR = cls.tickets_dir
        server.UPLOAD_DIR = cls.upload_dir
        server.REPO_ROOT = cls.dir
        server.SECRET = b"unit-test-signing-secret-32-byte"
        server.LOGIN_GATE = auth.LoginGate()
        # A fresh dispatcher against the test dir; stopped in tearDownClass.
        server.DISPATCHER = dispatch.Dispatcher(cls.tickets_dir, server.CONFIG)

        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.http.server_address[1]
        # The guard compares Host header against CONFIG["port"] — must match
        # the port the OS actually gave us, not the "0" placeholder.
        server.CONFIG["port"] = cls.port
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join(timeout=5)
        server.DISPATCHER.stop()
        for name, value in cls._saved.items():
            setattr(server, name, value)
        shutil.rmtree(cls.dir, ignore_errors=True)

    # ---- helpers --------------------------------------------------------

    def _host(self):
        return f"127.0.0.1:{self.port}"

    def _request(self, method, path, *, cookie=None, body=None, host=None,
                 origin=None, content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host or self._host()}
        if cookie:
            headers["Cookie"] = cookie
        if body is not None:
            headers["Content-Type"] = content_type
        if origin is not None:
            headers["Origin"] = origin
        payload = None if body is None else (
            body if isinstance(body, (bytes, bytearray))
            else json.dumps(body).encode("utf-8"))
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        cookie = resp.getheader("Set-Cookie")
        conn.close()
        return resp.status, data, cookie

    def _json(self, data):
        return json.loads(data.decode("utf-8"))

    def _login(self, password):
        # Same-origin write: Host + Origin + application/json (the guard's F1).
        return self._request("POST", "/api/login", body={"password": password},
                             origin=f"http://{self._host()}")

    def _cookie(self):
        status, _, set_cookie = self._login(self.password)
        self.assertEqual(status, 200, "login precondition failed")
        # `werkbank_session=...; Path=/; Max-Age=...; HttpOnly; SameSite=Strict`
        return set_cookie.split(";", 1)[0]

    # ---- seams that must exist ------------------------------------------

    def test_unauthenticated_read_is_rejected(self):
        """WB-108 acceptance A: remove the `_authenticated()` check in do_GET
        and this test goes red (proving auth is really wired, not just tested)."""
        status, body, _ = self._request("GET", "/api/tickets")
        self.assertEqual(status, 401)
        self.assertEqual(self._json(body).get("error"), "Bitte am Board anmelden.")

    def test_unauthenticated_write_is_rejected(self):
        status, body, _ = self._request(
            "POST", "/api/tickets", body={"title": "Fremd"},
            origin=f"http://{self._host()}")
        self.assertEqual(status, 401)

    def test_wrong_password_is_rejected(self):
        status, body, _ = self._login("falsch")
        self.assertEqual(status, 401)
        self.assertEqual(self._json(body).get("error"), "Falsches Passwort.")

    def test_login_grants_cookie_and_opens_board(self):
        status, _, set_cookie = self._login(self.password)
        self.assertEqual(status, 200)
        self.assertIn("werkbank_session=", set_cookie or "")
        self.assertIn("HttpOnly", set_cookie); self.assertIn("SameSite=Strict", set_cookie)
        cookie = set_cookie.split(";", 1)[0]
        status, body, _ = self._request("GET", "/api/tickets", cookie=cookie)
        self.assertEqual(status, 200)
        payload = self._json(body)
        self.assertIn("tickets", payload); self.assertIn("config", payload)
        # public_config drops the password hash — never leaked to a browser.
        self.assertNotIn("password_hash", payload["config"])

    def test_browse_outside_of_configured_roots_is_refused(self):
        """WB-108 acceptance B: extend the browse roots to `/` and this test
        goes red — the guard on which paths may be listed lives in server.py's
        do_GET wiring, not in projects.list_dirs's own tests."""
        cookie = self._cookie()
        status, body, _ = self._request(
            "GET", "/api/browse?path=/etc", cookie=cookie)
        self.assertEqual(status, 400)
        # projects.list_dirs raises a German message; the wiring must forward it.
        self.assertIn("error", self._json(body))

    def test_browse_inside_a_configured_root_is_allowed(self):
        cookie = self._cookie()
        status, body, _ = self._request(
            "GET", f"/api/browse?path={self.dir}", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("dirs", self._json(body))

    def test_guard_refuses_a_foreign_host_header(self):
        """The guard runs BEFORE the auth check — a request from evil.com is
        turned away regardless of credentials (F2, DNS rebinding)."""
        status, body, _ = self._request(
            "GET", "/api/tickets", host="evil.example.com:8765")
        self.assertEqual(status, 403)
        self.assertIn("error", self._json(body))

    def test_guard_refuses_a_foreign_origin_on_write(self):
        """A hostile page trying to POST — allowed Host, but Origin from
        elsewhere (F1, CSRF). The Content-Type check catches the form-encoded
        variant; the Origin check catches the JSON one."""
        status, body, _ = self._request(
            "POST", "/api/login", body={"password": self.password},
            origin="http://evil.example.com")
        self.assertEqual(status, 403)
        self.assertIn("Fremde Herkunft", self._json(body).get("error", ""))

    def test_wb140_review_endpoint_accepts_and_appends(self):
        """WB-140: POST /review returns 202 immediately and the reviewer
        thread appends a `## Review-Bot (…)` section on the ticket. The
        heavy call (opencode.adversarial_review) is monkey-patched — this
        test asserts the wiring, not the model."""
        import time as _time
        from unittest import mock
        cookie = self._cookie()
        # Create a ticket to review.
        status, body, _ = self._request(
            "POST", "/api/tickets",
            body={"title": "Test", "description": "was zu prüfen",
                  "assignee": "claude"},
            cookie=cookie, origin=f"http://{self._host()}")
        self.assertEqual(status, 200, body)
        tid = self._json(body)["id"]
        # Move to review so the button would show — endpoint accepts any status.
        status, _, _ = self._request("POST", "/api/tickets/" + tid,
                                     body={"status": "review"},
                                     cookie=cookie,
                                     origin=f"http://{self._host()}")
        self.assertEqual(status, 200)
        # Patch the reviewer so we do not spawn a real claude.
        # WB-170: adversarial_review now returns (text, truncated, usage).
        fake_usage = {"cost_usd": 0.42, "tokens_in": 100,
                      "tokens_out": 50, "tokens_cache": 200}
        with mock.patch.object(server.opencode, "adversarial_review",
                               return_value=("VERDICT: PROBLEM\nBeispiel-Kritik.",
                                             False, fake_usage)), \
             mock.patch.object(server.subprocess, "check_output",
                               return_value="dummy-diff"):
            status, body, _ = self._request(
                "POST", "/api/tickets/" + tid + "/review", body={},
                cookie=cookie, origin=f"http://{self._host()}")
            self.assertEqual(status, 202, body)
            self.assertEqual(self._json(body)["ticket"], tid)
            # Wait briefly for the background thread to append.
            for _ in range(30):
                fresh = store.load_tickets(server.TICKETS_DIR)
                t = next((x for x in fresh if x.id == tid), None)
                if t and "## Review-Bot" in t.body:
                    break
                _time.sleep(0.05)
            self.assertIn("## Review-Bot", t.body)
            self.assertIn("Beispiel-Kritik", t.body)

    def test_wb140_second_review_of_same_ticket_is_rejected_while_running(self):
        from unittest import mock
        import threading as _th
        cookie = self._cookie()
        status, body, _ = self._request(
            "POST", "/api/tickets", body={"title": "Y", "description": "Y"},
            cookie=cookie, origin=f"http://{self._host()}")
        tid = self._json(body)["id"]
        release = _th.Event()
        def slow_review(*a, **kw):
            release.wait(timeout=5)
            return ("verdict", False, None)  # WB-170: 3-tuple
        with mock.patch.object(server.opencode, "adversarial_review",
                               side_effect=slow_review), \
             mock.patch.object(server.subprocess, "check_output",
                               return_value=""):
            try:
                s1, _, _ = self._request("POST", "/api/tickets/" + tid + "/review",
                                         body={}, cookie=cookie,
                                         origin=f"http://{self._host()}")
                self.assertEqual(s1, 202)
                s2, b2, _ = self._request("POST", "/api/tickets/" + tid + "/review",
                                          body={}, cookie=cookie,
                                          origin=f"http://{self._host()}")
                self.assertEqual(s2, 429)
                self.assertIn("läuft", self._json(b2)["error"])
            finally:
                release.set()

    def test_ticket_crud_roundtrip_through_the_real_handler(self):
        cookie = self._cookie()
        status, body, _ = self._request(
            "POST", "/api/tickets",
            body={"title": "Kompositionstest", "description": "Round-Trip",
                  "assignee": "claude"},
            cookie=cookie, origin=f"http://{self._host()}")
        self.assertEqual(status, 200, body)
        created = self._json(body)
        self.assertTrue(created["id"].startswith("WB-"))
        status, body, _ = self._request("GET", "/api/tickets", cookie=cookie)
        self.assertEqual(status, 200)
        ids = [t["id"] for t in self._json(body)["tickets"]]
        self.assertIn(created["id"], ids)

    # ---- WB-150: frozen source statuses cannot be moved via the API ----

    def _make_ticket(self, cookie, status=None):
        s, body, _ = self._request(
            "POST", "/api/tickets",
            body={"title": "WB-150", "description": "Test", "assignee": "claude"},
            cookie=cookie, origin=f"http://{self._host()}")
        self.assertEqual(s, 200, body)
        tid = self._json(body)["id"]
        if status:
            # Bypass the API deliberately — the dispatcher/store is the one
            # allowed writer for the frozen states, and going through
            # `update_ticket` mimics exactly that.
            store.update_ticket(server.TICKETS_DIR, tid, {"status": status})
        return tid

    def test_wb150_in_arbeit_cannot_be_dragged_away(self):
        cookie = self._cookie()
        tid = self._make_ticket(cookie, status="in_arbeit")
        for target in ("offen", "review", "erledigt", "fehlgeschlagen",
                       "zu_bearbeiten"):
            s, body, _ = self._request(
                "POST", "/api/tickets/" + tid,
                body={"status": target},
                cookie=cookie, origin=f"http://{self._host()}")
            self.assertEqual(s, 409, (target, body))
            self.assertIn("In Arbeit", self._json(body).get("error", ""))
        fresh = next(x for x in store.load_tickets(server.TICKETS_DIR)
                     if x.id == tid)
        self.assertEqual(fresh.status, "in_arbeit")

    def test_wb150_rueckfrage_cannot_be_dragged_away(self):
        from unittest import mock
        cookie = self._cookie()
        tid = self._make_ticket(cookie, status="rueckfrage")
        s, body, _ = self._request(
            "POST", "/api/tickets/" + tid,
            body={"status": "offen"},
            cookie=cookie, origin=f"http://{self._host()}")
        self.assertEqual(s, 409, body)
        self.assertIn("Rückfrage", self._json(body).get("error", ""))
        # The answer endpoint is the legit exit — bypasses this rule because
        # it goes through /answer, not through the generic PATCH. Its
        # dispatcher.dispatch call is mocked so no claude subprocess spawns.
        with mock.patch.object(server.DISPATCHER.__class__, "dispatch",
                               lambda self, id: None):
            s, body, _ = self._request(
                "POST", "/api/tickets/" + tid + "/answer",
                body={"answer": "ja, mach weiter"},
                cookie=cookie, origin=f"http://{self._host()}")
        self.assertEqual(s, 200, body)

    def test_wb150_legit_transitions_still_pass(self):
        from unittest import mock
        cookie = self._cookie()
        # review -> erledigt (accept), review -> offen (reject),
        # fehlgeschlagen -> in_arbeit (retry), offen -> zu_bearbeiten (queue).
        # The retry path calls dispatcher.dispatch(); mock it so the test
        # does not actually spawn a claude subprocess.
        with mock.patch.object(server.DISPATCHER.__class__, "dispatch",
                               lambda self, id: None):
            for src, dst in (("review", "erledigt"), ("review", "offen"),
                             ("fehlgeschlagen", "in_arbeit"),
                             ("offen", "zu_bearbeiten")):
                tid = self._make_ticket(cookie, status=src)
                s, body, _ = self._request(
                    "POST", "/api/tickets/" + tid,
                    body={"status": dst},
                    cookie=cookie, origin=f"http://{self._host()}")
                self.assertEqual(s, 200, (src, dst, body))

    def test_wb150_non_status_patch_from_in_arbeit_still_works(self):
        """A frozen ticket can still be edited (title, priority etc.) — the
        guard is scoped to status changes, not the whole PATCH."""
        cookie = self._cookie()
        tid = self._make_ticket(cookie, status="in_arbeit")
        s, body, _ = self._request(
            "POST", "/api/tickets/" + tid,
            body={"priority": "hoch"},
            cookie=cookie, origin=f"http://{self._host()}")
        self.assertEqual(s, 200, body)
        fresh = next(x for x in store.load_tickets(server.TICKETS_DIR)
                     if x.id == tid)
        self.assertEqual(fresh.priority, "hoch")
        self.assertEqual(fresh.status, "in_arbeit")


if __name__ == "__main__":
    unittest.main()
