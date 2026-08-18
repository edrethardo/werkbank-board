"""WB-231: one session's report must not silently delete another's.

`store.set_result` REPLACES the whole `## Ergebnis` section. That is right for
the board form, where the user sees what they are overwriting, and wrong for
everyone else — and several sessions plus dispatched runs now write the same
board. Measured 2026-08-18: one write removed a peer session's 49-line review,
another a 73-line report. Neither produced an error; both were found only
through `git diff`.

`append_result` does the read and the write under the SAME lock. That matters
more than it looks: the read-then-write a caller does by hand has exactly the
race this is meant to prevent, which is why the rule lives in the store and
not only in a skill.
"""

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree                      # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from werkbank import store                                   # noqa: E402


class AppendResultTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(self.dir, "T", "Die Beschreibung",
                                     project="/p")

    def tearDown(self):
        remove_tree(self.dir)

    def _result(self):
        t = [x for x in store.load_tickets(self.dir) if x.id == self.t.id][0]
        return t.body.partition("## Ergebnis\n\n")[2].strip()

    def test_the_incident_itself(self):
        """A review, then a peer's report. Both must be readable afterwards."""
        store.append_result(self.dir, self.t.id, "BEFUND 1: die Vorbelegung "
                                                 "greift nur einmal.")
        store.append_result(self.dir, self.t.id, "Umgesetzt und gemergt.")
        result = self._result()
        self.assertIn("BEFUND 1", result)
        self.assertIn("Umgesetzt und gemergt", result)
        self.assertLess(result.index("BEFUND 1"), result.index("Umgesetzt"))

    def test_the_placeholder_is_replaced_not_appended_to(self):
        self.assertEqual(self._result(), store.PLACEHOLDER_RESULT)
        store.append_result(self.dir, self.t.id, "Erster Bericht.")
        self.assertEqual(self._result(), "Erster Bericht.")

    def test_a_heading_separates_the_voices(self):
        store.append_result(self.dir, self.t.id, "Der Lauf lief.")
        store.append_result(self.dir, self.t.id, "Zwei Fehler gefunden.",
                            heading="Review der Werkbank-Session")
        self.assertIn("## Review der Werkbank-Session", self._result())

    def test_the_description_is_never_touched(self):
        store.append_result(self.dir, self.t.id, "Bericht.")
        t = [x for x in store.load_tickets(self.dir) if x.id == self.t.id][0]
        self.assertIn("Die Beschreibung", t.body)
        self.assertLess(t.body.index("Die Beschreibung"),
                        t.body.index("## Ergebnis"))

    def test_set_result_still_replaces_on_purpose(self):
        """The board form overwrites knowingly — that must keep working."""
        store.append_result(self.dir, self.t.id, "Alt.")
        store.set_result(self.dir, self.t.id, "Neu.")
        self.assertEqual(self._result(), "Neu.")

    def test_concurrent_appends_all_survive(self):
        """Eight threads, eight reports. A hand-written read-then-write loses
        some of these; the lock inside append_result is the whole point."""
        def write(n):
            store.append_result(self.dir, self.t.id, f"Bericht {n}.")
        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        result = self._result()
        for n in range(8):
            self.assertIn(f"Bericht {n}.", result,
                          f"Bericht {n} wurde von einem anderen Schreiber "
                          f"ueberschrieben")

    def test_version_is_bumped_like_any_other_write(self):
        before = [x for x in store.load_tickets(self.dir)
                  if x.id == self.t.id][0].version
        store.append_result(self.dir, self.t.id, "Bericht.")
        after = [x for x in store.load_tickets(self.dir)
                 if x.id == self.t.id][0].version
        self.assertNotEqual(before, after)

    def test_unknown_ticket_raises(self):
        with self.assertRaises(KeyError):
            store.append_result(self.dir, "WB-9999", "x")


class SkillsTellSessionsToAppendTest(unittest.TestCase):
    """The rule is only real if the snippet sessions COPY does the right
    thing. Prose above a wrong example loses every time."""

    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent

    def _skill(self, name):
        return (self.root / ".claude" / "skills" / name
                / "SKILL.md").read_text(encoding="utf-8")

    def test_the_delivered_pull_skill_appends(self):
        text = self._skill("_user-level/werkbank-pull-ticket")
        self.assertIn("append_result", text)

    def test_the_work_ticket_skill_appends(self):
        text = self._skill("werkbank-work-ticket")
        self.assertIn("append_result", text)


if __name__ == "__main__":
    unittest.main()
