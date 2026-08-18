"""WB-200: the reviewer's report must be findable, and must survive an edit.

The on-demand adversarial reviewer (WB-140) appends its report to the ticket
body as a `## Review-Bot (stamp)` section. Until this ticket the board showed
that section only as the tail of the "Ergebnis" box — 14rem high, scrolling
inside itself. On WB-193 the paid report sat behind 130 lines of the agent's
own result: the user clicked the button, was billed $0.1784, and could not
find the report at all.

Splitting the reports out of `result` creates a second, sharper risk: the
detail dialog and the "Ablehnen" dialog rebuild the whole body from their
parts, so an editor that forgets the reports DELETES work the user paid for.
That is what the round-trip tests below are for.

The JS runs under node — the board's own functions, not a re-implementation.
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
from werkbank import server, store                            # noqa: E402

BOARD = (Path(__file__).resolve().parent.parent
         / "src" / "werkbank" / "board.html").read_text(encoding="utf-8")
NODE = shutil.which("node")

STAMP = "2026-08-17 17:23"
BODY = ("## Beschreibung\n\nMach das Ding.\n\n"
        "## Ergebnis\n\nGemacht, 401 Tests gruen.\n\n"
        f"## Review-Bot ({STAMP})\n\n"
        "- main.cpp:304 — der Startlog luegt.\n\n_\U0001f4b0 $0.1784 · 2 in_\n")


def _board_js() -> str:
    """The body-splitting block, lifted out of the page."""
    start = BOARD.index("const REVIEW_SECTION")
    end = BOARD.index("\nfunction projectName(", start)
    return BOARD[start:end]


@unittest.skipUnless(NODE, "node nicht installiert — die Board-Logik bleibt ungeprueft")
class SplitBodyTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def _run(self, expr, body=BODY):
        script = self.dir / "run.js"
        script.write_text(_board_js()
                          + "\nconst BODY = " + json.dumps(body) + ";\n"
                          + "console.log(JSON.stringify(" + expr + "));\n",
                          encoding="utf-8")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_report_is_lifted_out_of_the_result(self):
        parts = self._run("splitBody(BODY)")
        self.assertEqual(parts["description"], "Mach das Ding.")
        # THE bug: the report used to be the tail of this string.
        self.assertEqual(parts["result"], "Gemacht, 401 Tests gruen.")
        self.assertNotIn("Review-Bot", parts["result"])
        self.assertEqual(len(parts["reviews"]), 1)
        self.assertEqual(parts["reviews"][0]["stamp"], STAMP)
        self.assertIn("der Startlog luegt", parts["reviews"][0]["text"])

    def test_several_clicks_stay_separate_and_in_order(self):
        body = BODY + "\n## Review-Bot (2026-08-18 09:00)\n\nZweiter Bericht.\n"
        revs = self._run("splitBody(BODY).reviews", body)
        self.assertEqual([r["stamp"] for r in revs],
                         [STAMP, "2026-08-18 09:00"])
        self.assertEqual(revs[1]["text"], "Zweiter Bericht.")

    def test_body_without_any_review_is_unchanged(self):
        plain = "## Beschreibung\n\nA\n\n## Ergebnis\n\nB\n"
        parts = self._run("splitBody(BODY)", plain)
        self.assertEqual(parts["result"], "B")
        self.assertEqual(parts["reviews"], [])

    def test_saving_the_detail_dialog_keeps_the_report(self):
        """The detail editor's exact expression — description edited by hand."""
        rebuilt = self._run(
            'joinBody("Neuer Text", splitBody(BODY).result, splitBody(BODY).reviews)')
        self.assertIn(f"## Review-Bot ({STAMP})", rebuilt)
        self.assertIn("der Startlog luegt", rebuilt)
        self.assertIn("\U0001f4b0 $0.1784", rebuilt)
        self.assertIn("Neuer Text", rebuilt)
        # And it round-trips: splitting the rebuilt body gives the same report.
        self.assertEqual(self._run("splitBody(BODY).reviews", rebuilt)[0]["text"],
                         self._run("splitBody(BODY).reviews")[0]["text"])

    def test_rejecting_a_ticket_keeps_the_report(self):
        rebuilt = self._run(
            'joinBody(splitBody(BODY).description + "\\n\\n**Ablehnung (2026-08-17):** '
            'Nicht gut.", splitBody(BODY).result, splitBody(BODY).reviews)')
        self.assertIn("**Ablehnung (2026-08-17):** Nicht gut.", rebuilt)
        self.assertIn(f"## Review-Bot ({STAMP})", rebuilt)

    def test_round_trip_is_byte_for_byte_what_the_store_writes(self):
        """joinBody must produce exactly what store.append_review_note wrote,
        or every save would churn the file and the diff would lie."""
        rebuilt = self._run(
            'joinBody(splitBody(BODY).description, splitBody(BODY).result,'
            ' splitBody(BODY).reviews)')
        self.assertEqual(rebuilt, BODY)


class ReviewsRunningTest(unittest.TestCase):
    """The running state has to come from the SERVER: the board rebuilds its
    cards every five seconds and threw the button's own state away."""

    def tearDown(self):
        with server._REVIEWS_LOCK:
            server._REVIEWS_RUNNING.clear()

    def test_empty_by_default(self):
        self.assertEqual(server.reviews_running(), [])

    def test_reports_running_tickets_sorted(self):
        with server._REVIEWS_LOCK:
            server._REVIEWS_RUNNING.update({"WB-9", "WB-2"})
        self.assertEqual(server.reviews_running(), ["WB-2", "WB-9"])

    def test_start_review_marks_it_and_refuses_a_second_one(self):
        with server._REVIEWS_LOCK:
            server._REVIEWS_RUNNING.add("WB-5")     # pretend a thread is up
        self.assertFalse(server.start_review("WB-5"))
        self.assertIn("WB-5", server.reviews_running())


class AppendedReportShapeTest(unittest.TestCase):
    """The board parses the section heading the store writes. If either side
    changes the format alone, the report silently stops being findable."""

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_store_heading_matches_what_the_board_looks_for(self):
        t = store.create_ticket(self.dir, "T", "Beschreibung", project=str(self.dir))
        store.append_review_note(self.dir, t.id, "Ein Befund.",
                                 usage={"cost_usd": 0.1784, "tokens_in": 2,
                                        "tokens_out": 3941, "tokens_cache": 31805})
        body = store.load_tickets(self.dir)[0].body
        import re
        m = re.search(r"^## Review-Bot \(([^)\n]*)\)[ \t]*$", body, re.M)
        self.assertIsNotNone(m, f"board regex no longer matches:\n{body}")
        self.assertRegex(m.group(1), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertIn("\U0001f4b0 $0.1784", body)


if __name__ == "__main__":
    unittest.main()
