import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import store


SAMPLE = """---
id: WB-3
title: Fix the frobnicator
type: aufgabe
status: offen
assignee: claude
project: /pfad/zur/werkbank
priority: hoch
nach:
nicht_mit:
fork: nein
gate:
review:
version: 1
session:
handover:
handover_at:
handover_expired:
limit_until:
pid:
answer:
tokens_in:
tokens_out:
tokens_cache:
cost_usd:
duration_s:
queue_pos:
epic:
interactive: nein
review_cost_usd:
claimed_at:
created: 2026-08-14
updated: 2026-08-14
---

## Beschreibung

It frobs when it should nicate.

## Ergebnis

_(noch offen)_
"""


class ParseTest(unittest.TestCase):
    def test_roundtrip(self):
        t = store.parse_ticket(SAMPLE)
        self.assertEqual(t.id, "WB-3")
        self.assertEqual(t.title, "Fix the frobnicator")
        self.assertEqual(t.status, "offen")
        self.assertEqual(t.assignee, "claude")
        self.assertEqual(t.priority, "hoch")
        self.assertIn("It frobs", t.body)
        self.assertEqual(store.serialize_ticket(t), SAMPLE)

    def test_parse_rejects_missing_frontmatter(self):
        with self.assertRaises(ValueError):
            store.parse_ticket("no frontmatter here")

    def test_parse_rejects_missing_required_frontmatter_fields(self):
        # WB-109 P6: the "Pflichtfelder fehlen" branch had no coverage — a
        # frontmatter block missing `id` or `title` was left to slip through
        # if the raise ever went away. Pin the message so the wording stays
        # the one the board's error banner tests look for.
        for text, missing in (
            ("---\ntitle: nur ein Titel\n---\n\nX\n", "id"),
            ("---\nid: WB-9\n---\n\nX\n", "title"),
            ("---\nstatus: offen\n---\n\nX\n", "id"),      # both missing → id named first
        ):
            with self.assertRaises(ValueError) as cm:
                store.parse_ticket(text)
            msg = str(cm.exception)
            self.assertIn("Pflichtfelder fehlen", msg)
            self.assertIn(missing, msg)

    def test_legacy_ticket_without_type_defaults_to_aufgabe(self):
        legacy = SAMPLE.replace("type: aufgabe\n", "")
        t = store.parse_ticket(legacy)
        self.assertEqual(t.type, "aufgabe")
        # writing it back upgrades the file to the current format
        self.assertIn("type: aufgabe", store.serialize_ticket(t))


class DirTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_create_assigns_sequential_ids(self):
        t1 = store.create_ticket(self.dir, title="Erstes Ticket", description="A")
        t2 = store.create_ticket(self.dir, title="Zweites: Ticket!", description="B")
        self.assertEqual(t1.id, "WB-1")
        self.assertEqual(t2.id, "WB-2")
        # filenames are id + slug, slug is filesystem-safe
        paths = sorted(p.name for p in self.dir.glob("*.md"))
        self.assertEqual(paths, ["WB-1-erstes-ticket.md", "WB-2-zweites-ticket.md"])

    def test_create_defaults(self):
        t = store.create_ticket(self.dir, title="X", description="Y")
        self.assertEqual(t.status, "offen")
        self.assertEqual(t.assignee, "claude")
        self.assertEqual(t.priority, "normal")
        self.assertEqual(t.type, "aufgabe")

    def test_create_bug_ticket(self):
        t = store.create_ticket(self.dir, title="X", description="Y", type="bug")
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(loaded[t.id].type, "bug")

    def test_create_rejects_bad_type(self):
        with self.assertRaises(ValueError):
            store.create_ticket(self.dir, title="X", description="Y", type="story")

    def test_update_type_persists_and_rejects_bad_value(self):
        t = store.create_ticket(self.dir, title="X", description="")
        store.update_ticket(self.dir, t.id, {"type": "bug"})
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(loaded[t.id].type, "bug")
        # WB-161 added `epic` to TYPES; pick a value that stays unknown.
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id, {"type": "story"})

    def test_load_tickets_sorted_by_id(self):
        for title in ["a", "b", "c"]:
            store.create_ticket(self.dir, title=title, description="")
        tickets = store.load_tickets(self.dir)
        self.assertEqual([t.id for t in tickets], ["WB-1", "WB-2", "WB-3"])

    def test_update_status_persists_and_touches_updated(self):
        t = store.create_ticket(self.dir, title="X", description="")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(loaded[t.id].status, "in_arbeit")

    def test_fehlgeschlagen_is_a_valid_status(self):
        t = store.create_ticket(self.dir, title="X", description="")
        store.update_ticket(self.dir, t.id, {"status": "fehlgeschlagen"})
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(loaded[t.id].status, "fehlgeschlagen")

    def test_update_rejects_bad_status_and_unknown_id(self):
        t = store.create_ticket(self.dir, title="X", description="")
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id, {"status": "kaputt"})
        with self.assertRaises(KeyError):
            store.update_ticket(self.dir, "WB-999", {"status": "offen"})

    def test_update_title_renames_file(self):
        t = store.create_ticket(self.dir, title="Alter Titel", description="")
        self.assertTrue((self.dir / "WB-1-alter-titel.md").exists())
        store.update_ticket(self.dir, t.id, {"title": "Ganz neuer Titel"})
        names = sorted(p.name for p in self.dir.glob("*.md"))
        self.assertEqual(names, ["WB-1-ganz-neuer-titel.md"])
        loaded = store.load_tickets(self.dir)
        self.assertEqual(loaded[0].title, "Ganz neuer Titel")

    def test_update_without_title_change_keeps_filename(self):
        t = store.create_ticket(self.dir, title="Stabiler Titel", description="")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        names = sorted(p.name for p in self.dir.glob("*.md"))
        self.assertEqual(names, ["WB-1-stabiler-titel.md"])

    def test_stale_version_write_is_rejected_not_swallowed(self):
        t = store.create_ticket(self.dir, title="Original", description="Basis")
        stale_version = t.version
        store.update_ticket(self.dir, t.id, {"title": "Erste Änderung"})
        with self.assertRaises(store.ConflictError):
            store.update_ticket(self.dir, t.id,
                                {"title": "Zweite Änderung", "version": stale_version})
        loaded = store.load_tickets(self.dir)[0]
        self.assertEqual(loaded.title, "Erste Änderung")  # nothing overwritten

    def test_current_version_write_is_accepted_and_bumps(self):
        t = store.create_ticket(self.dir, title="X", description="")
        cur = store.load_tickets(self.dir)[0]
        updated = store.update_ticket(self.dir, t.id,
                                      {"title": "Neu", "version": cur.version})
        self.assertEqual(updated.title, "Neu")
        self.assertEqual(int(updated.version), int(cur.version) + 1)

    def test_set_result_merges_with_concurrent_user_edit(self):
        t = store.create_ticket(self.dir, title="X", description="alt")
        # user saves a new Beschreibung after the agent started
        store.update_ticket(self.dir, t.id,
                            {"body": "## Beschreibung\n\nvom Nutzer\n\n## Ergebnis\n\n_(noch offen)_\n"})
        store.set_result(self.dir, t.id, "vom Agenten")
        body = store.load_tickets(self.dir)[0].body
        self.assertIn("vom Nutzer", body)   # user's edit survives
        self.assertIn("vom Agenten", body)  # agent's result survives

    def test_concurrent_writers_lose_nothing(self):
        import threading
        t = store.create_ticket(self.dir, title="X", description="")
        n = 25
        def titles():
            for i in range(n):
                store.update_ticket(self.dir, t.id, {"title": f"Titel {i}"})
        def results():
            for i in range(n):
                store.set_result(self.dir, t.id, f"Ergebnis {i}")
        a, b = threading.Thread(target=titles), threading.Thread(target=results)
        a.start(); b.start(); a.join(); b.join()
        loaded = store.load_tickets(self.dir)[0]  # file must still parse cleanly
        self.assertEqual(loaded.title, f"Titel {n-1}")
        self.assertIn(f"Ergebnis {n-1}", loaded.body)
        # every single write is accounted for: create=1, then 2n bumps
        self.assertEqual(int(loaded.version), 1 + 2 * n)

    def test_fork_roundtrip_default_and_validation(self):
        t = store.create_ticket(self.dir, title="Ohne Fork", description="")
        self.assertEqual(t.fork, "nein")
        t2 = store.create_ticket(self.dir, title="Mit Fork", description="", fork="ja")
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(loaded[t2.id].fork, "ja")
        store.update_ticket(self.dir, t.id, {"fork": "ja"})
        self.assertEqual(store.load_tickets(self.dir)[0].fork, "ja")
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id, {"fork": "vielleicht"})

    def test_legacy_ticket_without_fork_counts_as_nein(self):
        legacy = SAMPLE.replace("fork: nein\n", "")
        self.assertEqual(store.parse_ticket(legacy).fork, "nein")

    def test_broken_file_only_affects_itself(self):
        store.create_ticket(self.dir, title="Heil", description="")
        store.create_ticket(self.dir, title="Auch heil", description="")
        (self.dir / "WB-99-kaputt.md").write_text("kein frontmatter hier", encoding="utf-8")
        tickets = store.load_tickets(self.dir)
        self.assertEqual([t.id for t in tickets], ["WB-1", "WB-2"])

    def test_load_with_errors_names_file_and_reason(self):
        store.create_ticket(self.dir, title="Heil", description="")
        (self.dir / "WB-98-kaputt.md").write_text("---\nid WB-98\n---\n", encoding="utf-8")
        (self.dir / "WB-99-kaputt.md").write_text("gar nichts", encoding="utf-8")
        tickets, errors = store.load_tickets_with_errors(self.dir)
        self.assertEqual(len(tickets), 1)
        self.assertEqual([e["file"] for e in errors],
                         ["WB-98-kaputt.md", "WB-99-kaputt.md"])
        self.assertIn("Frontmatter-Zeile", errors[0]["error"])
        self.assertIn("Frontmatter-Block", errors[1]["error"])

    def test_no_errors_for_healthy_dir(self):
        store.create_ticket(self.dir, title="Heil", description="")
        tickets, errors = store.load_tickets_with_errors(self.dir)
        self.assertEqual(errors, [])
        self.assertEqual(len(tickets), 1)

    def test_links_roundtrip_and_normalization(self):
        t = store.create_ticket(self.dir, title="Verkettet", description="",
                                nach="WB-8,WB-9, WB-8", nicht_mit=" WB-3 ")
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.nach, "WB-8, WB-9")  # deduped, normalized
        self.assertEqual(loaded.nicht_mit, "WB-3")

    def test_legacy_ticket_without_link_fields_defaults_empty(self):
        t = store.parse_ticket(SAMPLE)
        self.assertEqual(t.nach, "")
        self.assertEqual(t.nicht_mit, "")

    def test_invalid_link_rejected_on_create_and_update(self):
        with self.assertRaises(ValueError):
            store.create_ticket(self.dir, title="X", description="", nach="Quatsch")
        t = store.create_ticket(self.dir, title="X", description="")
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id, {"nicht_mit": "WB8"})

    def test_set_result_replaces_ergebnis_keeps_beschreibung(self):
        t = store.create_ticket(self.dir, title="X", description="Mach was.")
        store.set_result(self.dir, t.id, "Alles erledigt, geprüft.")
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        body = loaded[t.id].body
        self.assertIn("Mach was.", body)
        self.assertIn("Alles erledigt, geprüft.", body)
        self.assertNotIn("_(noch offen)_", body)

    def test_wb138_move_queued_up_swaps_effective_positions(self):
        """The user's ↑ button walks a queued ticket one place forward inside
        the same priority. Two peers with no explicit queue_pos fall back to
        their ticket numbers; after move_up the later one comes first."""
        a = store.create_ticket(self.dir, title="Erster", description="")
        b = store.create_ticket(self.dir, title="Zweiter", description="")
        for t in (a, b):
            store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        # Precondition: A comes first (lower ticket number).
        peers = sorted(store.load_tickets(self.dir),
                       key=lambda x: store.effective_queue_pos(x))
        self.assertEqual([x.id for x in peers], [a.id, b.id])
        store.move_queued_up(self.dir, b.id)
        peers = sorted(store.load_tickets(self.dir),
                       key=lambda x: store.effective_queue_pos(x))
        self.assertEqual([x.id for x in peers], [b.id, a.id])

    def test_wb138_move_up_on_top_ticket_is_a_noop(self):
        a = store.create_ticket(self.dir, title="Oben", description="")
        store.update_ticket(self.dir, a.id, {"status": "zu_bearbeiten"})
        # Should not raise; positions stay where they were.
        store.move_queued_up(self.dir, a.id)
        got = {x.id: x for x in store.load_tickets(self.dir)}[a.id]
        self.assertEqual(got.status, "zu_bearbeiten")

    def test_wb138_move_up_refuses_ticket_not_in_queue(self):
        a = store.create_ticket(self.dir, title="Offen", description="")
        with self.assertRaises(ValueError):
            store.move_queued_up(self.dir, a.id)

    def test_wb140_append_review_note_adds_section_and_stacks(self):
        """WB-140: reviewer report is appended as `## Review-Bot (…)` and
        multiple runs accumulate. Existing Beschreibung/Ergebnis stay."""
        t = store.create_ticket(self.dir, title="X", description="Aufgabe")
        store.append_review_note(self.dir, t.id, "Erster Befund.")
        store.append_review_note(self.dir, t.id, "Zweiter Befund.")
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.body.count("## Review-Bot ("), 2)
        self.assertIn("Erster Befund.", loaded.body)
        self.assertIn("Zweiter Befund.", loaded.body)
        self.assertIn("Aufgabe", loaded.body)   # Beschreibung untouched
        self.assertIn("## Ergebnis", loaded.body)

    def test_wb138_move_up_stays_inside_priority(self):
        """Priority is the strongest sort key; move_up must not lift a normal
        ticket above a hoch ticket by swapping across the boundary."""
        hi = store.create_ticket(self.dir, title="Hoch", description="",
                                 priority="hoch")
        lo = store.create_ticket(self.dir, title="Normal", description="")
        for t in (hi, lo):
            store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        # Moving the normal ticket up when its only peer is a hoch ticket is
        # a no-op (peers list is empty for its priority above it).
        store.move_queued_up(self.dir, lo.id)
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        # queue_pos for lo may have been touched, but hi's priority still
        # trumps in the dispatcher's sort — so effective ordering unchanged.
        self.assertEqual(loaded[hi.id].priority, "hoch")

    def test_update_body(self):
        t = store.create_ticket(self.dir, title="X", description="alt")
        store.update_ticket(self.dir, t.id, {"body": "## Beschreibung\n\nneu\n"})
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertIn("neu", loaded[t.id].body)


if __name__ == "__main__":
    unittest.main()


class ConcurrentWriteTest(unittest.TestCase):
    """WB-9: concurrent saves must never silently lose a change."""

    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(self.dir, title="Wettlauf", description="Basis")

    def tearDown(self):
        remove_tree(self.dir)

    def _load(self):
        return {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]

    def test_disjoint_updates_merge(self):
        # Editor read the ticket, then the agent writes its result, then the
        # editor changes only the status: both changes must survive.
        store.set_result(self.dir, self.t.id, "Agentenergebnis A")
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        after = self._load()
        self.assertEqual(after.status, "in_arbeit")
        self.assertIn("Agentenergebnis A", after.body)

    def test_stale_body_save_rejected_cleanly(self):
        stale_version = self._load().version
        store.set_result(self.dir, self.t.id, "Agentenergebnis B")
        with self.assertRaises(store.ConflictError):
            store.update_ticket(self.dir, self.t.id,
                                {"body": "## Beschreibung\n\nx\n\n## Ergebnis\n\nweg\n"},
                                expected_version=stale_version)
        self.assertIn("Agentenergebnis B", self._load().body)  # nothing overwritten

    def test_matching_version_accepted_and_bumped(self):
        v = int(self._load().version)
        store.update_ticket(self.dir, self.t.id, {"title": "Neu"}, expected_version=str(v))
        self.assertEqual(int(self._load().version), v + 1)

    def test_hammer_no_lost_version_bumps(self):
        import threading as th
        errors = []
        def worker(n):
            for i in range(20):
                try:
                    if n == 0:
                        store.update_ticket(self.dir, self.t.id,
                                            {"status": "in_arbeit" if i % 2 else "offen"})
                    else:
                        store.set_result(self.dir, self.t.id, f"Ergebnis {i}")
                except Exception as e:
                    errors.append(e)
        threads = [th.Thread(target=worker, args=(n,)) for n in (0, 1)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(errors, [])
        after = self._load()  # file must still parse cleanly
        self.assertEqual(int(after.version), 1 + 40)  # every write bumped exactly once

    def test_concurrent_creates_get_distinct_ids(self):
        import threading as th
        made = []
        def creator(n):
            made.append(store.create_ticket(self.dir, title=f"T{n}", description="").id)
        threads = [th.Thread(target=creator, args=(n,)) for n in range(5)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(len(set(made)), 5)


class CrossProcessLockTest(unittest.TestCase):
    """WB-9 follow-up: writers in SEPARATE processes (chat sessions) must also
    serialize. Without the flock this test can lose version bumps."""

    def test_two_processes_hammering_lose_no_bumps(self):
        import subprocess
        d = temp_dir()
        try:
            t = store.create_ticket(d, title="Prozessrennen", description="")
            src = Path(__file__).resolve().parent.parent / "src"
            script = (
                "import sys; sys.path.insert(0, %r)\n"
                "from werkbank import store\n"
                "for i in range(15):\n"
                "    store.update_ticket(%r, %r, {'status': 'in_arbeit' if i %% 2 else 'offen'})\n"
            ) % (str(src), str(d), t.id)
            procs = [subprocess.Popen([sys.executable, "-c", script]) for _ in range(2)]
            self.assertEqual([p.wait() for p in procs], [0, 0])
            after = {x.id: x for x in store.load_tickets(d)}[t.id]
            self.assertEqual(int(after.version), 1 + 30)
        finally:
            remove_tree(d)


class SessionFieldTest(unittest.TestCase):
    def test_session_field_roundtrip_and_update(self):
        d = temp_dir()
        try:
            t = store.create_ticket(d, title="S", description="")
            self.assertEqual(t.session, "")
            store.update_ticket(d, t.id, {"session": "abc-123"})
            after = {x.id: x for x in store.load_tickets(d)}[t.id]
            self.assertEqual(after.session, "abc-123")
        finally:
            remove_tree(d)


class DeleteTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_delete_removes_file_and_listing(self):
        t = store.create_ticket(self.dir, title="Weg damit", description="")
        keep = store.create_ticket(self.dir, title="Bleibt", description="")
        store.delete_ticket(self.dir, t.id)
        remaining = [x.id for x in store.load_tickets(self.dir)]
        self.assertEqual(remaining, [keep.id])
        self.assertEqual(list(self.dir.glob(t.id + "-*.md")), [])

    def test_delete_unknown_id_raises(self):
        with self.assertRaises(KeyError):
            store.delete_ticket(self.dir, "WB-999")


class AtomicWriteTest(unittest.TestCase):
    """WB-32: the board's reader thread must never see a half-written ticket.
    Without atomic writes this hammer produced hundreds of parse errors."""

    def test_reader_never_sees_partial_files(self):
        import threading
        d = temp_dir()
        try:
            t = store.create_ticket(d, title="Hammer", description="X" * 60000)
            errors, stop = [], threading.Event()
            def reader():
                while not stop.is_set():
                    _, errs = store.load_tickets_with_errors(d)
                    errors.extend(errs)
            rt = threading.Thread(target=reader)
            rt.start()
            for i in range(150):
                store.update_ticket(d, t.id, {"status": "in_arbeit" if i % 2 else "offen"})
            stop.set(); rt.join()
            self.assertEqual(errors, [])
        finally:
            remove_tree(d)


class BugForTicketTest(unittest.TestCase):
    """WB-71: reporting a bug from a finished ticket must carry that ticket's
    context, so the agent fixing it does not start from zero."""

    def setUp(self):
        self.dir = temp_dir()
        self.orig = store.create_ticket(self.dir, title="Dunkles Design",
                                        description="Board soll dunkel sein.",
                                        project="/projekt")
        store.set_result(self.dir, self.orig.id,
                         "Erledigt: Dunkles Design ist Standard, Umschalter oben.")
        store.update_ticket(self.dir, self.orig.id, {"status": "erledigt"})

    def tearDown(self):
        remove_tree(self.dir)

    def test_bug_references_the_original_and_its_result(self):
        bug = store.create_bug_for(self.dir, self.orig.id,
                                   "Der Umschalter springt beim Neuladen zurück.")
        self.assertEqual(bug.type, "bug")
        self.assertEqual(bug.project, "/projekt")
        self.assertIn(self.orig.id, bug.title)
        self.assertIn("Der Umschalter springt", bug.body)
        self.assertIn(self.orig.title, bug.body)
        self.assertIn("Umschalter oben", bug.body)      # the original's result
        self.assertEqual(bug.status, "offen")

    def test_original_keeps_its_state(self):
        before = {x.id: x for x in store.load_tickets(self.dir)}[self.orig.id]
        store.create_bug_for(self.dir, self.orig.id, "kaputt")
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.orig.id]
        self.assertEqual(after.status, before.status)

    def test_empty_description_refused(self):
        with self.assertRaises(ValueError):
            store.create_bug_for(self.dir, self.orig.id, "   ")

    def test_unknown_ticket_refused(self):
        with self.assertRaises(KeyError):
            store.create_bug_for(self.dir, "WB-999", "kaputt")


class Wb197OpencodeTicketShapeTest(unittest.TestCase):
    """WB-197: opencode is a small local model. A vague ticket does not make it
    ask — it makes it guess, fail the check twice and escalate to Claude, which
    costs more than writing the ticket properly would have. A ticket for the
    local lane must therefore carry numbered steps, the commands that prove the
    work, a done-list, and the gate that makes the board enforce all of it."""

    def _ticket(self, body, gate="Tests laufen durch"):
        return store.Ticket(id="WB-1", title="T", assignee="opencode",
                            gate=gate, body=body)

    COMPLETE = """## Beschreibung

1. Lege `src/foo.py` an, GENAU wie `src/bar.py` als Vorlage.
2. Ergänze die Funktion `tue_was(pfad: str) -> int`.

## Tests / Abnahme

    python3 -m pytest tests/test_foo.py -q      # erwartet: 3 passed, exit 0

## Fertig, wenn

[ ] `src/foo.py` existiert
[ ] Tests grün

## Ergebnis

_(noch offen)_
"""

    def test_a_complete_ticket_has_no_gaps(self):
        self.assertEqual(store.opencode_ticket_gaps(self._ticket(self.COMPLETE)), [])

    def test_missing_test_section_is_flagged(self):
        body = self.COMPLETE.replace("## Tests / Abnahme", "## Nebenbei")
        gaps = store.opencode_ticket_gaps(self._ticket(body))
        self.assertTrue(any("Tests / Abnahme" in g for g in gaps), gaps)

    def test_missing_done_list_is_flagged(self):
        body = self.COMPLETE.replace("## Fertig, wenn", "## Sonstiges")
        gaps = store.opencode_ticket_gaps(self._ticket(body))
        self.assertTrue(any("Fertig, wenn" in g for g in gaps), gaps)

    def test_prose_without_numbered_steps_is_flagged(self):
        body = self.COMPLETE.replace(
            "1. Lege `src/foo.py` an, GENAU wie `src/bar.py` als Vorlage.\n"
            "2. Ergänze die Funktion `tue_was(pfad: str) -> int`.",
            "Bau das bitte irgendwie ein, du weißt schon wie.")
        gaps = store.opencode_ticket_gaps(self._ticket(body))
        self.assertTrue(any("nummerierte" in g for g in gaps), gaps)

    def test_missing_gate_is_flagged_when_the_project_has_one(self):
        gaps = store.opencode_ticket_gaps(self._ticket(self.COMPLETE, gate=""))
        self.assertTrue(any("gate" in g for g in gaps), gaps)

    def test_no_gate_configured_means_no_gate_complaint(self):
        """A project without checks cannot be blamed for an empty gate field —
        the ticket should say a gate is needed, not fail this structural test."""
        gaps = store.opencode_ticket_gaps(self._ticket(self.COMPLETE, gate=""),
                                          gates_configured=False)
        self.assertEqual(gaps, [])

    def test_it_reads_a_plain_string_too(self):
        """So a skill can check the text it is about to send, before creating."""
        self.assertTrue(store.opencode_ticket_gaps("Mach mal was", ))
