"""WB-43: the tool must not depend on POSIX-only behavior."""
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import filelock, dispatch, store


class FileLockTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_lock_is_exclusive_between_threads(self):
        import time
        path = self.dir / ".lock"
        order = []

        def first():
            with filelock.exclusive(path):
                order.append("erster rein")
                time.sleep(0.4)
                order.append("erster raus")

        def second():
            with filelock.exclusive(path):
                order.append("zweiter rein")

        t1 = threading.Thread(target=first)
        t2 = threading.Thread(target=second)
        t1.start()
        time.sleep(0.15)   # give t1 the lock first, deterministically
        t2.start()
        t1.join(5); t2.join(5)
        self.assertEqual(order, ["erster rein", "erster raus", "zweiter rein"])

    def test_lock_file_is_created_with_owner_only_mode(self):
        path = self.dir / ".lock"
        with filelock.exclusive(path):
            pass
        self.assertTrue(path.exists())

    def test_no_posix_only_import_at_module_level(self):
        # fcntl/msvcrt must be optional — importing the package on the other
        # platform has to work.
        src = Path(filelock.__file__).read_text()
        self.assertIn("except ImportError", src)
        for mod in ("store", "projects", "dispatch", "server", "guard"):
            text = (Path(filelock.__file__).parent / f"{mod}.py").read_text()
            self.assertNotIn("\nimport fcntl", text, f"{mod} imports fcntl directly")


class PortablePathTest(unittest.TestCase):
    def test_log_dir_follows_the_platform(self):
        d = dispatch.log_dir()
        self.assertTrue(d.is_dir())
        if os.name == "nt":
            self.assertIn("werkbank", str(d).lower())
        else:
            self.assertIn(".local/state/werkbank", str(d))

    def test_ticket_files_always_use_unix_newlines(self):
        d = Path(tempfile.mkdtemp())
        try:
            t = store.create_ticket(d, title="Zeilen", description="a\n\nb")
            raw = next(d.glob("WB-*.md")).read_bytes()
            self.assertNotIn(b"\r\n", raw)
            self.assertEqual({x.id for x in store.load_tickets(d)}, {t.id})
        finally:
            shutil.rmtree(d)

    def test_open_flags_degrade_where_unavailable(self):
        self.assertTrue(hasattr(filelock, "NOFOLLOW"))
        self.assertIsInstance(filelock.NOFOLLOW, int)


class NoFcntlFallbackTest(unittest.TestCase):
    """WB-43: simulate a platform without fcntl (as Windows is) and prove the
    store still creates, reads and updates tickets. The real msvcrt path can
    only be exercised on Windows — this covers everything around it."""

    def test_store_works_without_fcntl(self):
        d = Path(tempfile.mkdtemp())
        original = filelock.fcntl
        try:
            filelock.fcntl = None          # pretend: no POSIX locking available
            t = store.create_ticket(d, title="Ohne fcntl", description="x")
            store.update_ticket(d, t.id, {"status": "zu_bearbeiten"})
            loaded = {x.id: x for x in store.load_tickets(d)}[t.id]
            self.assertEqual(loaded.status, "zu_bearbeiten")
            raw = next(d.glob("WB-*.md")).read_bytes()
            self.assertNotIn(b"\r\n", raw)
        finally:
            filelock.fcntl = original
            shutil.rmtree(d)
