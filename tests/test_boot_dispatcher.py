"""WB-229: a board with no browser tab open must still dispatch.

Found in a headless night run by a peer session, confirmed here on
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


class WindowsHandoverTest(unittest.TestCase):
    """WB-263 round 2: the Windows path had no test at all.

    `messaging.deliver()` returns NO_SOCKET_SUPPORT where there is no AF_UNIX,
    and the dispatcher must treat it like "nobody is there" — straight to a
    background run. Deleting that one entry from the tuple left all 642 tests
    green, which a reviewer demonstrated; on Windows it would mean every
    handover waits five minutes for a claim that cannot arrive.
    """

    def test_the_result_exists_and_is_distinct(self):
        from werkbank import messaging
        self.assertNotEqual(messaging.DeliveryResult.NO_SOCKET_SUPPORT,
                            messaging.DeliveryResult.ERROR)

    def test_deliver_returns_it_when_the_platform_has_no_unix_socket(self):
        import socket as real_socket
        from werkbank import messaging

        class NoUnixSocket:
            """Everything the module uses, minus AF_UNIX."""
            SOCK_STREAM = real_socket.SOCK_STREAM

        saved = messaging._socket
        try:
            messaging._socket = NoUnixSocket()
            out = messaging.deliver("irgendeine-id", "text",
                                    sessions_dir=self._sessions_dir())
            self.assertEqual(out, messaging.DeliveryResult.NO_SOCKET_SUPPORT)
        finally:
            messaging._socket = saved

    def _sessions_dir(self):
        """A directory with one session file pointing at a socket path, so
        `find_session` succeeds and the AF_UNIX check is what decides."""
        import json
        d = temp_dir()
        self.addCleanup(remove_tree, d)
        (d / "s.json").write_text(json.dumps({
            "sessionId": "irgendeine-id",
            "messagingSocketPath": str(d / "sock"),
            "peerProtocol": messaging_protocol()}), encoding="utf-8")
        return d

    def test_the_dispatcher_treats_it_as_nobody_there(self):
        """Pinned as source, because building a full dispatch here would test
        the harness rather than the branch: the tuple that skips the marker
        path must contain all three 'no chat reachable' outcomes."""
        src = (Path(__file__).resolve().parent.parent
               / "src" / "werkbank" / "dispatch.py").read_text(encoding="utf-8")
        start = src.index("if delivery in (messaging.DeliveryResult.NO_SESSION_FILE")
        block = src[start:start + 400]
        for name in ("NO_SESSION_FILE", "DEAD_SOCKET", "NO_SOCKET_SUPPORT"):
            self.assertIn(name, block,
                          f"{name} no longer skips the pointless five-minute wait")


def messaging_protocol():
    from werkbank import messaging
    return messaging.SUPPORTED_PROTOCOL


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
