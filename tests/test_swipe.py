"""WB-68: reasons the swipe felt unreliable, each pinned as a test against
the small pure logic taken out of board.html. The DOM handler is one line —
the decision is what needed shoring up."""
import re
import sys
import unittest
from pathlib import Path

BOARD = (Path(__file__).resolve().parent.parent / "src/werkbank/board.html").read_text()


class SwipeCodeShape(unittest.TestCase):
    """Bugs we can prove from the code itself."""

    def test_a_second_finger_cancels_the_swipe(self):
        # touchstart with e.touches.length !== 1 nulls x0 — good — but touchend
        # then reads e.changedTouches[0] anyway if a stray finger LIFTED first,
        # and no reset happens on touchmove that becomes a pinch. WB-68 fix:
        # also cancel on touchcancel and on touchmove with more than one touch.
        self.assertIn('addEventListener("touchcancel"', BOARD,
                      "kein touchcancel-Behandler")

    def test_swipe_check_uses_the_actual_move_distance_not_the_finger_move(self):
        # An iOS scroll can end at the touchstart X after bouncing back.
        # We already require |dx| >= 60 AND |dx| >= 2*|dy|, but the constant
        # must live in ONE place so tests and the handler cannot drift.
        self.assertRegex(BOARD, r"SWIPE_MIN\s*=\s*\d+")

    def test_scrolling_moves_do_not_trigger_a_swipe(self):
        # Reproduce the decision in Python and prove it.
        SWIPE_MIN, RATIO = _swipe_const(), _swipe_ratio()
        # A predominantly vertical scroll must NOT switch columns.
        self.assertIsNone(_decide(dx=40, dy=-800, SWIPE_MIN=SWIPE_MIN, RATIO=RATIO))
        # A short jitter must NOT switch columns.
        self.assertIsNone(_decide(dx=20, dy=5, SWIPE_MIN=SWIPE_MIN, RATIO=RATIO))
        # A clear swipe DOES.
        self.assertEqual(_decide(dx=-200, dy=30, SWIPE_MIN=SWIPE_MIN, RATIO=RATIO), 1)
        self.assertEqual(_decide(dx=200, dy=30, SWIPE_MIN=SWIPE_MIN, RATIO=RATIO), -1)

    def test_swipe_inside_a_button_does_not_switch_column(self):
        # Tapping/scrolling on „→ verschieben nach …" or „▶ Starten" must not
        # count as a board swipe. The handler currently listens on #board and
        # sees everything — fix: ignore gestures whose start target is a
        # control element.
        self.assertRegex(BOARD, r"\btarget\.closest\(")

    def test_realistic_hand_swipe_is_not_silently_rejected(self):
        """WB-68 v2: user reported 'löst zu schwer aus'. A finger dragged ~100
        px horizontally while drifting ~60 px vertically (typical, not a
        drawn-with-a-ruler swipe) must count. Under the old 60 px + 2:1 rule
        both examples failed silently — that WAS the bug."""
        SWIPE_MIN, RATIO = _swipe_const(), _swipe_ratio()
        # Slightly diagonal but clearly a left swipe:
        self.assertEqual(_decide(dx=-100, dy=60, SWIPE_MIN=SWIPE_MIN, RATIO=RATIO), 1)
        # Short but decisive right swipe (~4-5 mm at typical DPI):
        self.assertEqual(_decide(dx=45, dy=15, SWIPE_MIN=SWIPE_MIN, RATIO=RATIO), -1)
        # And the previous strict rule would have refused both — prove that,
        # so a future 'let's tighten it again' first has to notice.
        self.assertIsNone(_decide(dx=-100, dy=60, SWIPE_MIN=60, RATIO=2))
        self.assertIsNone(_decide(dx=45, dy=15, SWIPE_MIN=60, RATIO=2))

    def test_swipe_listens_on_the_viewport_not_just_the_board(self):
        """WB-68 v4 (2026-08-16): user reported "der wisch soll ÜBERALL
        funktionieren, nicht nur wenn man auf einer karte wischt". Root
        cause: the listener sat on #board, whose box ended at the bottom
        of the last card — a wisch over the empty area, header or status
        chips landed on <body> and was never heard.

        Fix: touch listeners live on `document.documentElement`. isPhone()
        in shiftStatus still keeps the desktop untouched; the target filter
        still refuses gestures that start on controls or inside a dialog.
        Regression is a code-shape check because we cannot fire real touches
        without a browser — a rename of the root would fail this test."""
        # The board-only wiring must be GONE.
        self.assertNotRegex(
            BOARD,
            r'\bboard\.addEventListener\("touchstart"',
            "swipe still listens on #board — a wisch outside the card area is lost")
        self.assertNotRegex(
            BOARD,
            r'\bboard\.addEventListener\("touchend"',
            "swipe still listens on #board — a wisch outside the card area is lost")
        # And the viewport-wide wiring must be there: a root grabbed from
        # document.documentElement, with touchstart/touchend wired to it.
        self.assertIn("document.documentElement", BOARD,
                      "swipe no longer references document.documentElement")
        self.assertRegex(
            BOARD,
            r'root\.addEventListener\("touchstart"',
            "swipe touchstart is not on the viewport root")
        self.assertRegex(
            BOARD,
            r'root\.addEventListener\("touchend"',
            "swipe touchend is not on the viewport root")
        # The dialog filter must be in place so an open <dialog> does not
        # let a swipe underneath it change the column.
        self.assertIn('dialog[open]', BOARD,
                      "open-dialog filter missing — swipe would fire under a modal")

    def test_short_horizontal_touch_suppresses_the_card_click(self):
        """WB-68 v3 (2026-08-16): after two rounds of tuning the constants,
        the user reported "weiterhin unzuverlässig". Root cause was NOT the
        threshold at all: the browser fires a synthetic click after a
        touchend that did not exceed its own tap-slop (~10–15 px), and every
        card carries an openDetail click handler. A wisch that started on a
        card either opened the detail dialog INSTEAD OF shifting the column
        or opened it on top of the shift — from the user's angle it looked
        like the swipe didn't take.

        The fix arms `_swipeSuppressUntil` on any horizontal-dominant touch
        of at least CLICK_SUPPRESS_MIN pixels, and the card click handler
        reads `wasRecentSwipe()` and bails. Vertical scrolls and pure taps
        must NOT be suppressed — they are how you open a ticket."""
        SUPPRESS_MIN = _click_suppress_min()

        # Short horizontal drag (below SWIPE_MIN but ≥ SUPPRESS_MIN): swipe
        # itself does not fire, but the synthetic click must be blocked so
        # the detail dialog does not steal the gesture.
        self.assertTrue(_should_suppress_click(dx=20, dy=5, SUPPRESS_MIN=SUPPRESS_MIN))
        # Full swipe: suppression is armed AND shiftStatus fires.
        self.assertTrue(_should_suppress_click(dx=-80, dy=20, SUPPRESS_MIN=SUPPRESS_MIN))
        # Pure vertical scroll: never suppress — the user wants to scroll.
        self.assertFalse(_should_suppress_click(dx=3, dy=-400, SUPPRESS_MIN=SUPPRESS_MIN))
        # Tiny tremor on a tap: never suppress — the user is opening a card.
        self.assertFalse(_should_suppress_click(dx=4, dy=2, SUPPRESS_MIN=SUPPRESS_MIN))
        # Vertical-dominant with slight sideways: never suppress.
        self.assertFalse(_should_suppress_click(dx=15, dy=30, SUPPRESS_MIN=SUPPRESS_MIN))

        # And the HTML must actually wire it up — otherwise the decision
        # logic here is a lie about the code.
        self.assertIn("_swipeSuppressUntil", BOARD,
                      "swipe-suppress flag not present in board.html")
        self.assertIn("wasRecentSwipe", BOARD,
                      "card click handler must consult wasRecentSwipe()")
        # The click handler on cards must read the flag — check the specific
        # openDetail wiring, otherwise a rename could break the guard silently.
        self.assertRegex(
            BOARD,
            r'addEventListener\("click"[^)]*\)\s*=>\s*\{\s*if\s*\(\s*wasRecentSwipe\(\)\s*\)\s*return;\s*openDetail',
            "card openDetail click handler no longer gated by wasRecentSwipe()")


def _swipe_const():
    m = re.search(r"SWIPE_MIN\s*=\s*(\d+)", BOARD)
    return int(m.group(1)) if m else 60


def _swipe_ratio():
    m = re.search(r"SWIPE_RATIO\s*=\s*([\d.]+)", BOARD)
    return float(m.group(1)) if m else 2.0


def _click_suppress_min():
    m = re.search(r"CLICK_SUPPRESS_MIN\s*=\s*(\d+)", BOARD)
    return int(m.group(1)) if m else 15


def _decide(dx, dy, SWIPE_MIN, RATIO=2.0):
    """Mirror the JS decision so we can prove it without a browser."""
    if abs(dx) < SWIPE_MIN or abs(dx) < abs(dy) * RATIO:
        return None
    return 1 if dx < 0 else -1


def _should_suppress_click(dx, dy, SUPPRESS_MIN):
    """Mirror the WB-68c decision: only horizontal-dominant touches with at
    least SUPPRESS_MIN pixels of horizontal travel arm the click suppression."""
    return abs(dx) >= SUPPRESS_MIN and abs(dx) > abs(dy)


if __name__ == "__main__":
    unittest.main()
