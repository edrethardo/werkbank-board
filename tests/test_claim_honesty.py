"""WB-204: "In Arbeit" must not claim more than the board actually knows.

A board run is verifiable — there is a process, a log, an idle counter. A CHAT
claim is one field in a file, and the card stated it as fact: "wird sichtbar in
Chat-Session 66268d15… bearbeitet". Measured on 2026-08-17: WB-203 said exactly
that for several minutes while the session that had claimed it was finishing a
different ticket. The user saw it and said "ich glaub das nicht" — correctly.

There is no honest way to see into a chat session from here, so the card now
reports what IS known (how long the claim has stood) and calls it out when that
gets long. `release_claim` is the way back.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree                       # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from werkbank import store                                    # noqa: E402

BOARD = (Path(__file__).resolve().parent.parent
         / "src" / "werkbank" / "board.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _claim_hint_source() -> str:
    start = BOARD.index("function claimHint(")
    end = BOARD.index("\nfunction handoverHint(", start)
    return BOARD[start:end]


@unittest.skipUnless(NODE, "node nicht installiert — die Board-Logik bleibt ungeprueft")
class ClaimHintTest(unittest.TestCase):
    WARN = 600          # ten minutes, the shipped default

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def _hint(self, claimed_at, now, warn=None):
        script = self.dir / "run.js"
        script.write_text(
            _claim_hint_source()
            + "\nconsole.log(JSON.stringify(claimHint("
              f'{{session: "66268d15-abcd", claimed_at: "{claimed_at}"}},'
              f" {now}, {warn or self.WARN})));\n", encoding="utf-8")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_fresh_claim_is_not_an_accusation(self):
        h = self._hint(1000, 1060)                      # one minute in
        self.assertFalse(h["warn"])
        self.assertFalse(h["stale"])
        self.assertIn("1 min", h["text"])

    def test_the_card_no_longer_asserts_that_work_happens(self):
        """The old wording promised visible work. Nothing here may."""
        h = self._hint(1000, 1060)
        self.assertNotIn("sichtbar", h["text"])
        self.assertIn("nicht, was im Chat passiert", h["title"])

    def test_a_standing_claim_is_called_out(self):
        h = self._hint(1000, 1000 + 12 * 60)
        self.assertTrue(h["warn"])
        self.assertTrue(h["stale"])       # this is what shows the requeue button
        self.assertIn("12 min", h["text"])
        self.assertIn("kein Ergebnis", h["text"])

    def test_exactly_at_the_threshold_it_warns(self):
        self.assertTrue(self._hint(1000, 1000 + 600)["warn"])
        self.assertFalse(self._hint(1000, 1000 + 599)["warn"])

    def test_a_claim_without_a_stamp_says_so_instead_of_guessing(self):
        h = self._hint("", 5000)
        self.assertIn("Zeitpunkt unbekannt", h["text"])
        self.assertFalse(h["stale"])      # no stamp is not evidence of stalling

    def test_the_window_is_configurable(self):
        self.assertTrue(self._hint(1000, 1000 + 120, warn=60)["warn"])


class ReleaseClaimTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(self.dir, "T", "x", project="/p")

    def tearDown(self):
        remove_tree(self.dir)

    def test_release_undoes_exactly_what_claim_did(self):
        store.claim_ticket(self.dir, self.t.id, "session-abc")
        claimed = store.load_tickets(self.dir)[0]
        self.assertEqual(claimed.status, "in_arbeit")
        self.assertTrue(claimed.claimed_at)
        released = store.release_claim(self.dir, self.t.id)
        self.assertEqual(released.status, "zu_bearbeiten")
        self.assertEqual(released.session, "")
        self.assertEqual(released.claimed_at, "")

    def test_a_handover_marker_goes_too(self):
        """Otherwise the requeued ticket would be handed straight back to the
        session that was not working it."""
        store.update_ticket(self.dir, self.t.id,
                            {"status": "in_arbeit", "handover": "s1",
                             "handover_at": "123"})
        released = store.release_claim(self.dir, self.t.id)
        self.assertEqual((released.handover, released.handover_at), ("", ""))

    def test_only_in_arbeit_can_be_released(self):
        with self.assertRaises(ValueError):
            store.release_claim(self.dir, self.t.id)          # still offen

    def test_unknown_ticket_raises(self):
        with self.assertRaises(KeyError):
            store.release_claim(self.dir, "WB-9999")


class RequeueEndpointTest(unittest.TestCase):
    """The route, including the promise it must keep: a ticket with a LIVE
    board run is really being worked and must not be yanked out from under it."""

    def setUp(self):
        import http.client, threading
        from http.server import ThreadingHTTPServer
        from werkbank import dispatch, server
        self.server_mod, self.client = server, http.client
        self.dir = temp_dir()
        self._saved = (server.TICKETS_DIR, server.CONFIG, server.DISPATCHER)
        server.TICKETS_DIR = self.dir
        server.CONFIG = {"host": "127.0.0.1", "port": 0, "projects": {},
                         "gates": {}, "default_project": str(self.dir),
                         "nonblocking_review": {}}
        self.dispatcher = dispatch.Dispatcher(self.dir, server.CONFIG)
        self.dispatcher.stop()
        server.DISPATCHER = self.dispatcher
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        server.CONFIG["port"] = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        (self.server_mod.TICKETS_DIR, self.server_mod.CONFIG,
         self.server_mod.DISPATCHER) = self._saved
        remove_tree(self.dir)

    def _post(self, path, payload=None):
        c = self.client.HTTPConnection("127.0.0.1", self.server_mod.CONFIG["port"],
                                       timeout=10)
        c.request("POST", path, json.dumps(payload or {}),
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_a_stalled_chat_claim_goes_back_to_the_queue(self):
        """And the queue is pumped straight away: putting it back has to mean
        it gets worked, not that it waits for the next 15s ticker."""
        pumped = []
        self.dispatcher.pump_queue = lambda: pumped.append(1)
        t = store.create_ticket(self.dir, "T", "x", project="/p")
        store.claim_ticket(self.dir, t.id, "session-abc")
        status, _ = self._post(f"/api/tickets/{t.id}/requeue")
        self.assertEqual(status, 200)
        back = store.load_tickets(self.dir)[0]
        self.assertEqual((back.status, back.session, back.claimed_at),
                         ("zu_bearbeiten", "", ""))
        self.assertEqual(len(pumped), 1)

    def test_a_live_board_run_is_refused(self):
        t = store.create_ticket(self.dir, "T", "x", project="/p")
        store.claim_ticket(self.dir, t.id, "session-abc")
        self.dispatcher.active_runs = lambda: {t.id: {"started": "18:00"}}
        status, body = self._post(f"/api/tickets/{t.id}/requeue")
        self.assertEqual(status, 409)
        self.assertIn("wirklich", body["error"])
        self.assertEqual(store.load_tickets(self.dir)[0].status, "in_arbeit")

    def test_unknown_ticket_is_404(self):
        self.assertEqual(self._post("/api/tickets/WB-9999/requeue")[0], 404)

    def test_a_ticket_that_is_not_in_arbeit_is_400(self):
        t = store.create_ticket(self.dir, "T", "x", project="/p")
        self.assertEqual(self._post(f"/api/tickets/{t.id}/requeue")[0], 400)


if __name__ == "__main__":
    unittest.main()
