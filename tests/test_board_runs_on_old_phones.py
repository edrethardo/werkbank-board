"""WB-253: the board page must PARSE on the owner's phone.

He reported the board blank on his phone one day after WB-203 landed. Blank is
the worst symptom this project produces: no console on a phone, nothing to
distinguish "broken" from "still loading", and the desktop tab kept working
from a cached copy — so the failure was invisible on the only machine that
could have diagnosed it.

The cause was one operator. `??` (nullish coalescing) needs iOS Safari 13.4;
older browsers fail to PARSE the entire script block, so nothing in it runs at
all — not the rendering, and not the error reporter that was supposed to make
this visible, because it lived in the same block.

Two rules come out of that, and both are tested here:
  1. no syntax younger than ES2019 in the page,
  2. the error reporter lives in its OWN block, BEFORE the main one, so a parse
     error in the main block still reaches the user.
"""

import re
import unittest
from pathlib import Path

BOARD = (Path(__file__).resolve().parent.parent
         / "src" / "werkbank" / "board.html")


class NoSyntaxYoungerThanEs2019Test(unittest.TestCase):
    """Checked by pattern, not by a browser — this project ships no build
    step and has no browser to test in, so the honest guard is a list of the
    constructs that break an older phone outright."""

    # (name, pattern, the browser version that first understood it)
    MODERN = [
        ("?? (nullish coalescing)", r"\?\?[^=]", "iOS Safari 13.4"),
        ("??= (nullish assignment)", r"\?\?=", "iOS Safari 14"),
        ("?. (optional chaining)", r"\?\.", "iOS Safari 13.4"),
        ("||= / &&=", r"(\|\||&&)=", "iOS Safari 14"),
        (".at()", r"\.at\(", "iOS Safari 15.4"),
        (".replaceAll()", r"\.replaceAll\(", "iOS Safari 13.4"),
        ("Object.hasOwn", r"Object\.hasOwn\(", "iOS Safari 15.4"),
        ("structuredClone", r"\bstructuredClone\(", "iOS Safari 15.4"),
        ("Array.prototype.findLast", r"\.findLast\(", "iOS Safari 15.4"),
    ]

    def setUp(self):
        page = BOARD.read_text(encoding="utf-8")
        # Only the code — a comment mentioning `??` is not a syntax error.
        self.code = "\n".join(
            re.sub(r"^\s*//.*$", "", block, flags=re.M)
            for block in re.findall(r"<script>(.*?)</script>", page, re.S))

    def test_no_construct_that_an_older_phone_cannot_parse(self):
        for name, pattern, since in self.MODERN:
            hit = re.search(pattern, self.code)
            self.assertIsNone(
                hit,
                f"{name} needs {since}. On anything older the whole script "
                f"block fails to parse and the board is a BLANK PAGE — that is "
                f"how WB-253 reached the owner's phone. Found: "
                f"{self.code[max(0, hit.start() - 40):hit.start() + 40]!r}"
                if hit else "")

    def test_the_replacement_for_the_operator_is_still_there(self):
        """Whoever 'simplifies' prioRank back into `??` reintroduces the bug."""
        self.assertIn("function prioRank(", self.code)


class ErrorReporterSurvivesAParseErrorTest(unittest.TestCase):
    """A reporter inside the block that fails to parse never registers."""

    def setUp(self):
        self.page = BOARD.read_text(encoding="utf-8")
        self.blocks = re.findall(r"<script>(.*?)</script>", self.page, re.S)

    def test_the_reporter_is_in_a_block_of_its_own(self):
        holding = [i for i, b in enumerate(self.blocks)
                   if 'addEventListener("error"' in b]
        self.assertEqual(len(holding), 1, "expected exactly one error reporter")
        block = self.blocks[holding[0]]
        self.assertNotIn("function render(", block,
                         "the reporter shares a block with the board code — a "
                         "parse error there takes the reporter with it")

    def test_it_comes_BEFORE_the_block_it_has_to_report_on(self):
        reporter = next(i for i, b in enumerate(self.blocks)
                        if 'addEventListener("error"' in b)
        main = next(i for i, b in enumerate(self.blocks) if "function render(" in b)
        self.assertLess(reporter, main,
                        "a reporter after the failing block never runs")

    def test_it_also_catches_rejected_promises(self):
        reporter = next(b for b in self.blocks if 'addEventListener("error"' in b)
        self.assertIn("unhandledrejection", reporter)

    def test_it_says_what_to_do_with_the_message(self):
        reporter = next(b for b in self.blocks if 'addEventListener("error"' in b)
        self.assertIn("Assistenten", reporter)


if __name__ == "__main__":
    unittest.main()
