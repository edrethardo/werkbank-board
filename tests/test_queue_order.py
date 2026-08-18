"""WB-203: the queue column must SHOW the order the dispatcher will use.

The user clicked "▲ nach oben" and nothing happened. The button was fine — the
ticket really did move up the queue — but the board rendered the column in
ticket-number order while the dispatcher took it in (priority, position)
order. Reproduced on 2026-08-17 in a scratch store: after one click the
dispatcher order was A -> C -> B and the board still showed A -> B -> C.
Dragging inside the column did nothing at all: the drop handler discarded any
drop whose target column equalled the ticket's own status.

The board cannot ask the dispatcher for its sort key, so the key exists twice.
`BoardMatchesDispatcherTest` runs BOTH on the same tickets and compares — the
duplication is allowed to exist, drifting apart is not.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree                       # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from werkbank import dispatch, store                          # noqa: E402

BOARD = (Path(__file__).resolve().parent.parent
         / "src" / "werkbank" / "board.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _order_js() -> str:
    start = BOARD.index("const PRIORITY_ORDER =")
    end = BOARD.index("\nfunction render(", start)
    return BOARD[start:end]


def _dispatcher_order(tickets):
    """Exactly dispatch.Dispatcher.pump_queue's key."""
    queued = [t for t in tickets if t.status == "zu_bearbeiten"]
    queued.sort(key=lambda t: (dispatch.Dispatcher.PRIORITY_ORDER.get(t.priority, 1),
                               store.effective_queue_pos(t),
                               store._ticket_number(t.id)))
    return [t.id for t in queued]


class MoveQueuedToTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self.ids = []
        for n in "ABCD":
            t = store.create_ticket(self.dir, f"Ticket {n}", "x", project="/p")
            store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
            self.ids.append(t.id)

    def tearDown(self):
        remove_tree(self.dir)

    def _order(self):
        return _dispatcher_order(store.load_tickets(self.dir))

    def test_drag_to_the_top(self):
        store.move_queued_to(self.dir, self.ids[3], 0)
        self.assertEqual(self._order(),
                         [self.ids[3], self.ids[0], self.ids[1], self.ids[2]])

    def test_drag_into_the_middle(self):
        store.move_queued_to(self.dir, self.ids[0], 2)
        self.assertEqual(self._order(),
                         [self.ids[1], self.ids[2], self.ids[0], self.ids[3]])

    def test_index_past_the_end_lands_last(self):
        store.move_queued_to(self.dir, self.ids[0], 99)
        self.assertEqual(self._order()[-1], self.ids[0])

    def test_negative_index_lands_first(self):
        store.move_queued_to(self.dir, self.ids[2], -5)
        self.assertEqual(self._order()[0], self.ids[2])

    def test_other_lanes_keep_their_places(self):
        """A lane is (status, project, priority). Reordering one must not
        renumber another — each project has its own worker (WB-183)."""
        other = store.create_ticket(self.dir, "Fremd", "x", project="/anders")
        store.update_ticket(self.dir, other.id, {"status": "zu_bearbeiten"})
        before = [t.queue_pos for t in store.load_tickets(self.dir)
                  if t.id == other.id]
        store.move_queued_to(self.dir, self.ids[3], 0)
        after = [t.queue_pos for t in store.load_tickets(self.dir)
                 if t.id == other.id]
        self.assertEqual(before, after)

    def test_priority_still_wins(self):
        """Dropping a normal ticket above a "hoch" one puts it at the top of
        its OWN priority — the dispatcher sorts by priority first, so any
        other answer would be a promise the board cannot keep."""
        top = store.create_ticket(self.dir, "Wichtig", "x", project="/p",
                                  priority="hoch")
        store.update_ticket(self.dir, top.id, {"status": "zu_bearbeiten"})
        store.move_queued_to(self.dir, self.ids[3], 0)
        self.assertEqual(self._order()[0], top.id)
        self.assertEqual(self._order()[1], self.ids[3])

    def test_only_queued_tickets_move(self):
        store.update_ticket(self.dir, self.ids[0], {"status": "offen"})
        with self.assertRaises(ValueError):
            store.move_queued_to(self.dir, self.ids[0], 0)

    def test_unknown_ticket_raises(self):
        with self.assertRaises(KeyError):
            store.move_queued_to(self.dir, "WB-9999", 0)


@unittest.skipUnless(NODE, "node nicht installiert — die Board-Logik bleibt ungeprueft")
class BoardMatchesDispatcherTest(unittest.TestCase):
    """The board's sort and the dispatcher's sort, on the same tickets."""

    def setUp(self):
        self.dir = temp_dir()
        self.js = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)
        remove_tree(self.js)

    def _board_order(self, tickets):
        payload = [{"id": t.id, "priority": t.priority, "queue_pos": t.queue_pos,
                    "project": t.project, "status": t.status} for t in tickets]
        script = self.js / "run.js"
        script.write_text(
            _order_js()
            + "\nconst T = " + json.dumps(payload) + ";\n"
            + 'console.log(JSON.stringify(queueOrder('
              'T.filter(t => t.status === "zu_bearbeiten"), "zu_bearbeiten")'
              ".map(t => t.id)));\n", encoding="utf-8")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_the_click_that_started_this_ticket(self):
        """Three queued tickets, one click on the last: the board must now
        show what the dispatcher does. Before WB-203 this assertion failed."""
        ids = []
        for n in "ABC":
            t = store.create_ticket(self.dir, f"Ticket {n}", "x", project="/p")
            store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
            ids.append(t.id)
        store.move_queued_up(self.dir, ids[2])
        tickets = store.load_tickets(self.dir)
        self.assertEqual(_dispatcher_order(tickets), [ids[0], ids[2], ids[1]])
        self.assertEqual(self._board_order(tickets), _dispatcher_order(tickets))

    def test_mixed_priorities_and_projects_agree(self):
        prios = ["normal", "hoch", "niedrig", "hoch", "normal"]
        projects = ["/a", "/b", "/a", "/a", "/b"]
        ids = []
        for i, (pr, pj) in enumerate(zip(prios, projects)):
            t = store.create_ticket(self.dir, f"T{i}", "x", project=pj, priority=pr)
            store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
            ids.append(t.id)
        store.move_queued_to(self.dir, ids[4], 0)
        store.move_queued_to(self.dir, ids[3], 0)
        tickets = store.load_tickets(self.dir)
        self.assertEqual(self._board_order(tickets), _dispatcher_order(tickets))

    def test_non_queue_columns_keep_their_incoming_order(self):
        """Only the queue is a queue. Reordering Review would be a lie."""
        script = self.js / "keep.js"
        script.write_text(
            _order_js()
            + '\nconst T = [{id: "WB-9", priority: "niedrig", queue_pos: "", '
              'project: "/p", status: "review"}, {id: "WB-1", priority: "hoch", '
              'queue_pos: "", project: "/p", status: "review"}];\n'
              'console.log(JSON.stringify(queueOrder(T, "review").map(t => t.id)));\n',
            encoding="utf-8")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), ["WB-9", "WB-1"])


@unittest.skipUnless(NODE, "node nicht installiert — die Board-Logik bleibt ungeprueft")
class DropIndexTest(unittest.TestCase):
    """Where a drop lands. Peers = same project AND priority, because that is
    the lane the store reorders; a card of another lane in between must not
    shift the answer."""

    def setUp(self):
        self.js = temp_dir()

    def tearDown(self):
        remove_tree(self.js)

    def _index(self, cards, moved_id, before_id):
        script = self.js / "run.js"
        script.write_text(
            _order_js()
            + "\nconst C = " + json.dumps(cards) + ";\n"
            + "const T = C.find(x => x.id === " + json.dumps(moved_id) + ");\n"
            + "console.log(JSON.stringify(dropIndex(C, T, "
            + json.dumps(before_id) + ")));\n", encoding="utf-8")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def _lane(self):
        return [{"id": f"WB-{i}", "project": "/p", "priority": "normal"}
                for i in (1, 2, 3)]

    def test_drop_on_the_first_card_means_index_zero(self):
        self.assertEqual(self._index(self._lane(), "WB-3", "WB-1"), 0)

    def test_drop_below_everything_means_the_end(self):
        self.assertEqual(self._index(self._lane(), "WB-1", None), 2)

    def test_drop_in_the_middle(self):
        self.assertEqual(self._index(self._lane(), "WB-1", "WB-3"), 1)

    def test_cards_of_another_lane_do_not_count(self):
        cards = [{"id": "WB-1", "project": "/p", "priority": "normal"},
                 {"id": "WB-2", "project": "/anders", "priority": "normal"},
                 {"id": "WB-3", "project": "/p", "priority": "normal"}]
        # WB-3 dropped onto the foreign card: one PEER is above it.
        self.assertEqual(self._index(cards, "WB-3", "WB-2"), 1)


class MoveToEndpointTest(unittest.TestCase):
    """The route itself: a drop has to survive the trip through the handler.
    The store tests above would all stay green if /move-to were never wired."""

    def setUp(self):
        import http.client, threading
        from http.server import ThreadingHTTPServer
        from werkbank import server
        self.server_mod = server
        self.dir = temp_dir()
        self._saved = (server.TICKETS_DIR, server.CONFIG, server.DISPATCHER)
        server.TICKETS_DIR = self.dir
        server.CONFIG = {"host": "127.0.0.1", "port": 0, "projects": {},
                         "gates": {}, "default_project": str(self.dir),
                         "nonblocking_review": {}}
        server.DISPATCHER = dispatch.Dispatcher(self.dir, server.CONFIG)
        server.DISPATCHER.stop()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        server.CONFIG["port"] = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = http.client

    def tearDown(self):
        self.httpd.shutdown()
        (self.server_mod.TICKETS_DIR, self.server_mod.CONFIG,
         self.server_mod.DISPATCHER) = self._saved
        remove_tree(self.dir)

    def _post(self, path, payload):
        c = self.client.HTTPConnection("127.0.0.1", self.server_mod.CONFIG["port"],
                                       timeout=10)
        c.request("POST", path, json.dumps(payload),
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_drop_reaches_the_store(self):
        ids = []
        for n in "ABC":
            t = store.create_ticket(self.dir, f"Ticket {n}", "x", project="/p")
            store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
            ids.append(t.id)
        status, _ = self._post(f"/api/tickets/{ids[2]}/move-to", {"index": 0})
        self.assertEqual(status, 200)
        self.assertEqual(_dispatcher_order(store.load_tickets(self.dir))[0], ids[2])

    def test_unknown_ticket_is_404(self):
        self.assertEqual(self._post("/api/tickets/WB-9999/move-to",
                                    {"index": 0})[0], 404)

    def test_a_ticket_that_is_not_queued_is_400(self):
        t = store.create_ticket(self.dir, "Offen", "x", project="/p")
        self.assertEqual(self._post(f"/api/tickets/{t.id}/move-to",
                                    {"index": 0})[0], 400)

    def test_a_nonsense_index_is_400_not_a_crash(self):
        t = store.create_ticket(self.dir, "T", "x", project="/p")
        store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        self.assertEqual(self._post(f"/api/tickets/{t.id}/move-to",
                                    {"index": "oben"})[0], 400)


if __name__ == "__main__":
    unittest.main()
