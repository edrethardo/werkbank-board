import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import store


SAMPLE = """---
id: WB-3
title: Fix the frobnicator
type: aufgabe
status: offen
assignee: claude
project: ~/code/werkbank
priority: hoch
nach:
nicht_mit:
fork: nein
version: 1
session:
handover:
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

    def test_legacy_ticket_without_type_defaults_to_aufgabe(self):
        legacy = SAMPLE.replace("type: aufgabe\n", "")
        t = store.parse_ticket(legacy)
        self.assertEqual(t.type, "aufgabe")
        # writing it back upgrades the file to the current format
        self.assertIn("type: aufgabe", store.serialize_ticket(t))


class DirTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir)

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
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id, {"type": "epic"})

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
        self.dir = Path(tempfile.mkdtemp())
        self.t = store.create_ticket(self.dir, title="Wettlauf", description="Basis")

    def tearDown(self):
        shutil.rmtree(self.dir)

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
        d = Path(tempfile.mkdtemp())
        try:
            t = store.create_ticket(d, title="Prozessrennen", description="")
            src = Path(__file__).resolve().parent.parent / "src"
            script = (
                "import sys; sys.path.insert(0, %r)\n"
                "from werkbank import store\n"
                "for i in range(15):\n"
                "    store.update_ticket(%r, %r, {'status': 'in_arbeit' if i %% 2 else 'offen'})\n"
            ) % (str(src), str(d), t.id)
            procs = [subprocess.Popen(["python3", "-c", script]) for _ in range(2)]
            self.assertEqual([p.wait() for p in procs], [0, 0])
            after = {x.id: x for x in store.load_tickets(d)}[t.id]
            self.assertEqual(int(after.version), 1 + 30)
        finally:
            shutil.rmtree(d)


class SessionFieldTest(unittest.TestCase):
    def test_session_field_roundtrip_and_update(self):
        d = Path(tempfile.mkdtemp())
        try:
            t = store.create_ticket(d, title="S", description="")
            self.assertEqual(t.session, "")
            store.update_ticket(d, t.id, {"session": "abc-123"})
            after = {x.id: x for x in store.load_tickets(d)}[t.id]
            self.assertEqual(after.session, "abc-123")
        finally:
            shutil.rmtree(d)


class DeleteTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir)

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
        d = Path(tempfile.mkdtemp())
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
            shutil.rmtree(d)
