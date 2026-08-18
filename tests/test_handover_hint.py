"""WB-198: the card must say when a handover is not being answered.

A handover only works if the target chat session happens to be running its
watcher — a shell loop it has to restart after every ticket. When it is not,
nothing happens and the old card said "wartet auf Übernahme" right up to the
deadline: indistinguishable from a board that hangs. Measured on 2026-08-17 in
the Luna project (WB-188) and, by the same pattern, several times before.

These tests RUN the board's own function under node instead of pinning strings,
so the wording may change without breaking them and the LOGIC cannot.
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree

BOARD = (Path(__file__).resolve().parent.parent
         / "src" / "werkbank" / "board.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _handover_hint_source() -> str:
    """Just the function under test, lifted out of the page."""
    start = BOARD.index("function handoverHint(")
    end = BOARD.index("\nfunction cardEl(", start)
    return BOARD[start:end]


@unittest.skipUnless(NODE, "node nicht installiert — die Board-Logik bleibt ungeprüft")
class HandoverHintTest(unittest.TestCase):
    WINDOW = 300          # five minutes, the shipped default

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def _hint(self, handover_at, now, window=None):
        script = self.dir / "run.js"
        script.write_text(
            _handover_hint_source()
            + f"\nconsole.log(JSON.stringify(handoverHint("
              f'{{handover: "fef2de48-abcd", handover_at: "{handover_at}"}},'
              f" {now}, {window or self.WINDOW})));\n",
            encoding="utf-8")
        # encoding: node prints German text; Windows would otherwise decode
        # it as cp1252 and the umlauts turn into replacement characters.
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_fresh_handover_shows_the_remaining_time_without_alarm(self):
        h = self._hint(1_000_000, 1_000_030)          # 30 s gone of 300
        self.assertFalse(h["warn"])
        self.assertIn("4:30", h["text"])
        self.assertIn("wartet auf Übernahme", h["text"])

    def test_half_the_window_gone_says_the_session_is_not_answering(self):
        h = self._hint(1_000_000, 1_000_200)          # 200 s gone, 100 s left
        self.assertTrue(h["warn"], h)
        self.assertIn("meldet sich nicht", h["text"])
        self.assertIn("1:40", h["text"])
        self.assertIn("zieh dir dein Ticket", h["title"],
                      "the card should say what fixes it")

    def test_expired_says_the_board_takes_over(self):
        h = self._hint(1_000_000, 1_000_400)
        self.assertTrue(h["warn"])
        self.assertIn("Hintergrund-Lauf", h["text"])
        self.assertNotIn("wartet auf Übernahme", h["text"])

    def test_a_missing_timestamp_still_renders(self):
        """Older tickets have a handover but no handover_at."""
        h = self._hint("", 1_000_000)
        self.assertFalse(h["warn"])
        self.assertIn("übergeben", h["text"])

    def test_the_session_is_named_so_you_know_where_to_look(self):
        h = self._hint(1_000_000, 1_000_030)
        self.assertIn("fef2de48", h["text"])


class HandoverHintIsWiredUpTest(unittest.TestCase):
    """The function is worthless if the card does not use it — and this one
    check does not need node."""

    def test_the_card_calls_it_with_the_configured_window(self):
        self.assertIn("handoverHint(t, Math.floor(Date.now() / 1000)", BOARD)
        self.assertIn("chat_handover_minutes", BOARD)

    def test_a_late_handover_is_marked_visually(self):
        self.assertIn("handover-late", BOARD)
        self.assertRegex(BOARD, r"\.handover-late\s*\{")
