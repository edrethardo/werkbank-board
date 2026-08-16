"""WB-101: ticket numbers must be unique forever — never reissued after a
delete, never doubly assigned by concurrent creators."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import store


class NumberReuseTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_deleted_highest_number_is_not_reused(self):
        store.create_ticket(self.dir, title="eins", description="")
        store.create_ticket(self.dir, title="zwei", description="")
        next(self.dir.glob("WB-2-*")).unlink()
        t = store.create_ticket(self.dir, title="drei", description="")
        self.assertEqual(t.id, "WB-3")

    def test_counter_survives_deleting_everything(self):
        store.create_ticket(self.dir, title="eins", description="")
        for p in self.dir.glob("WB-*.md"):
            p.unlink()
        t = store.create_ticket(self.dir, title="zwei", description="")
        self.assertEqual(t.id, "WB-2")

    def test_legacy_dir_without_counter_still_works(self):
        store.create_ticket(self.dir, title="eins", description="")
        (self.dir / ".highest-id").unlink()  # pre-WB-101 state
        t = store.create_ticket(self.dir, title="zwei", description="")
        self.assertEqual(t.id, "WB-2")

    def test_garbage_counter_is_ignored(self):
        (self.dir / ".highest-id").write_text("kaputt\n")
        t = store.create_ticket(self.dir, title="eins", description="")
        self.assertEqual(t.id, "WB-1")

    def test_duplicate_id_fails_loudly_instead_of_hitting_first_match(self):
        t = store.create_ticket(self.dir, title="original", description="")
        clone = self.dir / "WB-1-zzz-eingeschleppt.md"
        clone.write_text(next(self.dir.glob("WB-1-o*")).read_text(encoding="utf-8"),
                         encoding="utf-8")
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})


CREATE_SNIPPET = """
import sys, time
sys.path.insert(0, {src!r})
from werkbank import store
time.sleep(0.05)  # let both processes reach the lock at the same time
t = store.create_ticket({d!r}, title="race " + sys.argv[1], description="")
print(t.id)
"""


class ConcurrentCreateTest(unittest.TestCase):
    def test_two_processes_never_share_an_id(self):
        d = tempfile.mkdtemp()
        try:
            src = str(Path(__file__).resolve().parent.parent / "src")
            code = CREATE_SNIPPET.format(src=src, d=d)
            procs = [subprocess.Popen([sys.executable, "-c", code, str(i)],
                                      stdout=subprocess.PIPE, text=True)
                     for i in range(2)]
            ids = [p.communicate(timeout=30)[0].strip() for p in procs]
            self.assertTrue(all(p.returncode == 0 for p in procs), ids)
            self.assertEqual(len(set(ids)), 2, f"id doppelt vergeben: {ids}")
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
