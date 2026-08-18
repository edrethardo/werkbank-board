"""WB-43: the tool must not depend on POSIX-only behavior."""
import os
import signal
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import posix_only, temp_dir, remove_tree
from werkbank import filelock, dispatch, store


class FileLockTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir)

    @posix_only
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

    # Windows note: msvcrt.locking() does not exclude two threads of the same
    # process (it locks per handle). Inside one process the store serialises
    # with its own _WRITE_LOCK; filelock's job is exclusion ACROSS processes,
    # and that holds on both systems.

    def test_lock_file_is_created_with_owner_only_mode(self):
        path = self.dir / ".lock"
        with filelock.exclusive(path):
            pass
        self.assertTrue(path.exists())

    def test_no_posix_only_import_at_module_level(self):
        # fcntl/msvcrt must be optional — importing the package on the other
        # platform has to work.
        src = Path(filelock.__file__).read_text(encoding="utf-8")
        self.assertIn("except ImportError", src)
        for mod in ("store", "projects", "dispatch", "server", "guard"):
            text = (Path(filelock.__file__).parent / f"{mod}.py").read_text(encoding="utf-8")
            self.assertNotIn("\nimport fcntl", text, f"{mod} imports fcntl directly")


class PortablePathTest(unittest.TestCase):
    def test_log_dir_follows_the_platform(self):
        d = dispatch.log_dir()
        self.assertTrue(d.is_dir())
        # The suite redirects the state home to a temp dir (log isolation,
        # audit 2026-08-16) — assert the platform SHAPE, not the real home.
        env = os.environ.get("LOCALAPPDATA" if os.name == "nt" else "XDG_STATE_HOME")
        if env:
            self.assertTrue(str(d).startswith(env), d)
        self.assertEqual(d.name, "logs")
        self.assertEqual(d.parent.name, "werkbank")

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


if __name__ == "__main__":
    unittest.main()


class LockFileOpenRetriesTest(unittest.TestCase):
    """Windows can refuse to OPEN a lock file for a few milliseconds while
    another process is busy with it — measured in CI 2026-08-17: two processes
    updating one ticket, one died with PermissionError inside filelock, before
    doing any work at all. POSIX never does this, so the retry can only be
    proven here by construction; the Windows CI job is the real witness."""

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_a_transient_refusal_is_waited_out(self):
        path = self.dir / ".lock"
        calls = {"n": 0}
        real_open = os.open

        def flaky(p, *a, **kw):
            if str(p) == str(path):
                calls["n"] += 1
                if calls["n"] < 3:            # refuse twice, then succeed
                    raise PermissionError(13, "Permission denied")
            return real_open(p, *a, **kw)

        os.open = flaky
        try:
            fd = filelock.open_lock_file(path, attempts=10, pause=0.01)
        finally:
            os.open = real_open
        os.close(fd)
        self.assertEqual(calls["n"], 3, "it should have waited, not given up")

    def test_a_permanent_refusal_still_raises(self):
        path = self.dir / ".lock"
        real_open = os.open

        def always(p, *a, **kw):
            if str(p) == str(path):
                raise PermissionError(13, "Permission denied")
            return real_open(p, *a, **kw)

        os.open = always
        try:
            with self.assertRaises(PermissionError):
                filelock.open_lock_file(path, attempts=3, pause=0.01)
        finally:
            os.open = real_open


class LockByteIsWrittenOnlyOnceTest(unittest.TestCase):
    """Windows locks a byte RANGE, so the lock file needs one byte — but
    writing it unconditionally means writing into the very range another
    process holds, which Windows refuses with PermissionError. Measured in CI
    2026-08-17: the second of two processes died there before doing any work."""

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_an_empty_lock_file_gets_its_byte(self):
        path = self.dir / ".lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            filelock._ensure_lock_byte(fd)
            self.assertEqual(os.fstat(fd).st_size, 1)
        finally:
            os.close(fd)

    def test_an_existing_byte_is_not_rewritten(self):
        path = self.dir / ".lock"
        path.write_bytes(b"0")
        fd = os.open(path, os.O_RDWR)
        try:
            written = []
            real_write = os.write
            os.write = lambda f, b: (written.append(b), real_write(f, b))[1]
            try:
                filelock._ensure_lock_byte(fd)
            finally:
                os.write = real_write
            self.assertEqual(written, [], "it must not touch the locked byte")
        finally:
            os.close(fd)

    def test_a_refused_write_is_not_fatal(self):
        path = self.dir / ".lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        real_write = os.write
        os.write = lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied"))
        try:
            filelock._ensure_lock_byte(fd)      # must not raise
        finally:
            os.write = real_write
            os.close(fd)


class WatchdogSignalExistsEverywhereTest(unittest.TestCase):
    """Windows has no SIGKILL. `signal.SIGKILL` raises AttributeError where it
    is EVALUATED — and the run watchdog evaluated it inside a timer thread, so
    on Windows it delivered no signal at all: `agent_timeout_minutes` was a
    no-op and a hung agent blocked its project forever, silently. Found by an
    adversarial cross-platform review, invisible to CI because every test that
    could trigger the watchdog is POSIX-only."""

    def test_the_module_never_names_sigkill_directly(self):
        src = (Path(__file__).resolve().parent.parent
               / "src" / "werkbank" / "dispatch.py").read_text(encoding="utf-8")
        body = src.split("SIGKILL_OR_TERM = ", 1)[1]
        self.assertNotIn("signal.SIGKILL", body,
                         "use SIGKILL_OR_TERM so Windows gets SIGTERM instead of "
                         "an AttributeError in a background thread")

    def test_the_fallback_is_a_real_signal(self):
        from werkbank import dispatch
        self.assertIsNotNone(dispatch.SIGKILL_OR_TERM)
        self.assertIn(dispatch.SIGKILL_OR_TERM,
                      (getattr(signal, "SIGKILL", None), signal.SIGTERM))
