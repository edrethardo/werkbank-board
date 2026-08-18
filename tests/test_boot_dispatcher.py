"""WB-229: a board with no browser tab open must still dispatch.

Found in a headless night run by the coding_agent session, confirmed here on
the running service: the Dispatcher was built LAZILY, and `main()` only
reached `get_dispatcher()` when a handover marker happened to survive the
restart. Otherwise the first call came from an AUTHENTICATED request handler —
which, with nobody looking at the board, never happens.

Measured 2026-08-18 on the live service before the fix: the board process had
ONE thread and held NO `.dispatcher.lock`, while a ticket with a valid gate
sat in `zu_bearbeiten` untouched for over twenty minutes. No error anywhere.
During the day a tab is always open, which is why this survived so long.

The second test covers the same stretch of code: `exclusive` was decided once
in `__init__` and never revisited, so a board that lost the lock race at boot
stayed passive for the rest of its life.
"""

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree                      # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from werkbank import dispatch, filelock, server, store       # noqa: E402


class BootBuildsTheDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self._saved = (server.TICKETS_DIR, server.CONFIG, server.DISPATCHER)
        server.TICKETS_DIR = self.dir
        server.CONFIG = {"default_project": str(self.dir), "projects": {},
                         "gates": {}, "nonblocking_review": {},
                         "queue_poll_seconds": 0.2, "state_path": str(self.dir / "state.json")}
        server.DISPATCHER = None
        self.booted = None

    def tearDown(self):
        if self.booted is not None:
            self.booted.stop()
        (server.TICKETS_DIR, server.CONFIG, server.DISPATCHER) = self._saved
        remove_tree(self.dir)

    def test_boot_leaves_a_running_dispatcher_without_any_request(self):
        """THE bug: no request, no dispatcher, no ticker, silent queue."""
        self.assertIsNone(server.DISPATCHER)
        self.booted = server.boot()
        self.assertIsNotNone(server.DISPATCHER)
        self.assertIs(self.booted, server.DISPATCHER)
        self.assertTrue(self.booted.exclusive,
                        "the boot dispatcher must own the board")
        tickers = [t for t in threading.enumerate()
                   if t.name == dispatch.TICKER_THREAD_NAME and t.is_alive()]
        self.assertTrue(tickers, "no queue ticker is running after boot")

    def test_boot_does_not_need_a_handover_to_bother(self):
        """It used to build one ONLY on this path — hence the whole bug."""
        t = store.create_ticket(self.dir, "T", "B", project=str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        self.booted = server.boot()
        self.assertIsNotNone(server.DISPATCHER)

    def test_importing_the_module_still_builds_nothing(self):
        """WB-144 must keep holding: an import may not start a ticker."""
        import importlib
        before = {id(x) for x in threading.enumerate()
                  if x.name == dispatch.TICKER_THREAD_NAME}
        importlib.reload(server)
        after = {id(x) for x in threading.enumerate()
                 if x.name == dispatch.TICKER_THREAD_NAME}
        self.assertEqual(after - before, set())
        # reload() rebound the module globals — put the test's world back.
        server.TICKETS_DIR = self.dir
        server.CONFIG = self._saved[1]
        server.DISPATCHER = None


class LockRetryTest(unittest.TestCase):
    """A board that lost the lock race at boot must be able to take over
    later. Before WB-229 `exclusive` was decided once and never again."""

    def setUp(self):
        self.dir = temp_dir()
        self.dispatchers = []

    def tearDown(self):
        for d in self.dispatchers:
            d.stop()
        remove_tree(self.dir)

    def _dispatcher(self):
        d = dispatch.Dispatcher(self.dir, {"queue_poll_seconds": 0.1,
                                           "default_project": str(self.dir)})
        self.dispatchers.append(d)
        return d

    def test_second_dispatcher_starts_passive(self):
        first = self._dispatcher()
        self.assertTrue(first.exclusive)
        second = self._dispatcher()
        self.assertFalse(second.exclusive,
                         "two owners at once would double-dispatch")

    def test_it_takes_over_once_the_owner_is_gone(self):
        first = self._dispatcher()
        second = self._dispatcher()
        self.assertFalse(second.exclusive)
        first.stop()                       # releases the flock
        self.assertTrue(second._retry_exclusive())
        self.assertTrue(second.exclusive)

    def test_retry_does_not_steal_a_held_lock(self):
        first = self._dispatcher()
        second = self._dispatcher()
        self.assertFalse(second._retry_exclusive())
        self.assertFalse(second.exclusive)
        self.assertTrue(first.exclusive, "the owner must keep the board")

    def test_retry_is_a_no_op_for_the_owner(self):
        first = self._dispatcher()
        fd = first._lock_fd
        self.assertTrue(first._retry_exclusive())
        self.assertIs(first._lock_fd, fd, "the owner must not reopen its lock")

    def test_the_ticker_is_what_calls_the_retry(self):
        """Pins the wiring: a retry nobody calls would fix nothing."""
        src = (Path(__file__).resolve().parent.parent
               / "src" / "werkbank" / "dispatch.py").read_text(encoding="utf-8")
        tick = src[src.index("    def _tick(self):"):]
        self.assertIn("self._retry_exclusive()", tick[:600])


if __name__ == "__main__":
    unittest.main()
