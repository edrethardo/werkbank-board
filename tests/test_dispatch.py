import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Isolate BEFORE importing werkbank code: this module writes agent logs, and
# dispatch.log_dir() reads the env at call time. Without this, a suite run
# wrote into the REAL ~/.local/state/werkbank/logs — the hardcoded ids below
# (WB-77/80/90/99) collide with live tickets, and WB-90's real log was
# destroyed that way (external audit, 2026-08-16). Set here, in the writing
# module itself, so it holds under every runner (unittest discover with and
# without -t, pytest, direct execution); tests/test_log_isolation.py pins it.
_TEST_STATE = tempfile.mkdtemp(prefix="werkbank-test-state-")
os.environ["XDG_STATE_HOME"] = _TEST_STATE
os.environ["LOCALAPPDATA"] = _TEST_STATE

from werkbank import dispatch, store
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import (temp_dir, remove_tree, sh_stub, sh_path, sleeper_command,
                   posix_only, linux_only,
                   stop_before_teardown, WINDOWS)


def wait_until(condition, timeout=5.0, interval=0.02):
    """Wait for something to BECOME true, up to a generous ceiling.

    WB-93: these tests used to sleep a fixed 50 ms and then assert. On a loaded
    machine the background thread does not get scheduled inside 50 ms, so the
    assertion failed while the code was perfectly correct — seven tests went red
    under load and green again on a quiet machine, which is the worst kind of
    test: it teaches you to ignore red. Waiting for the condition costs nothing
    when it is already true and does not lie when the machine is busy."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


def force_marker_handover(test):
    """WB-258: `messaging.deliver` now scans the user's .claude/sessions directory for a real
    live chat — a test session id will not be there, so my new "no session
    file" branch skips the marker and runs the background instead. Tests
    that assert the marker-based WB-22 path must opt out of the direct
    delivery attempt; this returns WRONG_PROTOCOL so the code keeps the
    old marker + fallback path."""
    from unittest import mock
    patcher = mock.patch.object(dispatch.messaging, "deliver",
                                return_value=dispatch.messaging.DeliveryResult.WRONG_PROTOCOL)
    m = patcher.start()
    test.addCleanup(patcher.stop)
    return m


def make_dispatcher(test, *args, **kwargs):
    """A Dispatcher whose ticker is ALWAYS stopped when the test ends.

    WB-93: 21 dispatchers were created in this file and 6 stopped, so 15 ticker
    threads kept scanning already-deleted temp dirs for the rest of the run —
    the suite generated the very load its timing-sensitive tests could not
    survive."""
    d = dispatch.Dispatcher(*args, **kwargs)
    test.addCleanup(d.stop)          # belt: runs even if tearDown is skipped
    stop_before_teardown(test, d)    # braces: Windows cannot delete open files
    return d


def tearDownModule():
    """No ticker may outlive this module. Without this the leak just comes
    back the next time somebody adds a test in a hurry."""
    def no_strays():
        return not [t for t in threading.enumerate()
                    if t.name == dispatch.TICKER_THREAD_NAME and t.is_alive()]
    # daemon threads exit at interpreter shutdown, so a stray is only visible
    # here — fail loudly rather than let the suite slowly re-acquire the bug.
    # A STOPPED ticker still needs a moment of scheduling to actually die
    # (WB-92 round 2 hit this as a flake), so wait briefly: a real leak never
    # dies and still fails; a stopped thread just needs the grace period.
    assert wait_until(no_strays, timeout=3.0), (
        "queue ticker(s) outlived the tests — "
        "use make_dispatcher() so stop() is registered")


class SlugTest(unittest.TestCase):
    def test_project_slug_matches_claude_projects_layout(self):
        # A neutral path on purpose: this used to spell out the owner's real
        # checkout, so the export's redaction rewrote the input but not the
        # expected slug and the shipped suite failed (WB-236 round 2).
        self.assertEqual(
            dispatch.project_slug("/home/USER/code/mein-projekt"),
            "-home-USER-code-mein-projekt",
        )

    def test_has_history_true_only_with_jsonl(self):
        root = temp_dir()
        try:
            self.assertFalse(dispatch.project_has_history("/some/proj", root))
            d = root / "-some-proj"
            d.mkdir()
            self.assertFalse(dispatch.project_has_history("/some/proj", root))
            (d / "abc.jsonl").write_text("{}", encoding="utf-8")
            self.assertTrue(dispatch.project_has_history("/some/proj", root))
        finally:
            remove_tree(root)


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.ticket = store.Ticket(
            id="WB-7", title="Testaufgabe", body="## Beschreibung\n\nTu was.\n\n## Ergebnis\n\n_(noch offen)_\n"
        )
        self.cfg = {"agent_permission_mode": "acceptEdits", "agent_allowed_tools": "Bash"}

    def test_resume_without_fork_continues_session_in_place(self):
        # fork defaults to "nein": the remembered ticket session grows on.
        cmd = dispatch.build_command("claude", self.ticket, "resume", self.cfg,
                                     resume_id="sess-123")
        self.assertEqual(cmd[0], "claude")
        i = cmd.index("--resume")
        self.assertEqual(cmd[i + 1], "sess-123")
        self.assertNotIn("--fork-session", cmd)
        self.assertNotIn("--continue", cmd)
        self.assertIn("acceptEdits", cmd)
        self.assertIn("Bash", cmd)
        self.assertIn("stream-json", cmd)  # live events (WB-37)
        self.assertIn("WB-7", cmd[-1])
        self.assertIn("Testaufgabe", cmd[-1])

    def test_resume_with_fork_ja_forks_the_remembered_session(self):
        forky = store.Ticket(id="WB-7", title="T", fork="ja")
        cmd = dispatch.build_command("claude", forky, "resume", self.cfg,
                                     resume_id="sess-123")
        self.assertIn("--resume", cmd)
        self.assertIn("--fork-session", cmd)

    def test_continue_mode_always_forks_even_with_fork_nein(self):
        # Safety rule: without a remembered ticket session the run would grow
        # some arbitrary foreign conversation (e.g. the user's chat) — fork.
        for fork in ("nein", "ja"):
            t = store.Ticket(id="WB-7", title="T", fork=fork)
            cmd = dispatch.build_command("claude", t, "continue", self.cfg)
            self.assertIn("--continue", cmd)
            self.assertIn("--fork-session", cmd, f"fork={fork}")
            self.assertNotIn("--resume", cmd)

    def test_command_fresh_starts_new_session(self):
        cmd = dispatch.build_command("claude", self.ticket, "fresh", self.cfg)
        self.assertNotIn("--continue", cmd)
        self.assertNotIn("--resume", cmd)
        self.assertNotIn("--fork-session", cmd)

    def test_bug_ticket_prompt_demands_debugging_discipline(self):
        # For FOREIGN projects (no Werkbank skill there) the discipline stays
        # inline. For Werkbank tickets it lives in werkbank-work-ticket now
        # (WB-70) — see WerkbankPromptTest.
        bug = store.Ticket(id="WB-9", title="Kaputt", type="bug",
                           project="/tmp/fremd",
                           body="## Beschreibung\n\nX\n")
        prompt = dispatch.build_prompt(bug)
        self.assertIn("Bug-Ticket", prompt)
        self.assertIn("Regressionstest", prompt)


class SessionStateTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"

    def tearDown(self):
        remove_tree(self.dir)

    def test_save_and_load_roundtrip_per_project(self):
        dispatch.save_last_session("/proj/a", "sess-a", self.state)
        dispatch.save_last_session("/proj/b", "sess-b", self.state)
        self.assertEqual(dispatch.load_last_session("/proj/a", self.state), "sess-a")
        self.assertEqual(dispatch.load_last_session("/proj/b", self.state), "sess-b")

    def test_missing_or_corrupt_state_yields_none(self):
        self.assertIsNone(dispatch.load_last_session("/proj/a", self.state))
        self.state.write_text("{kaputt", encoding="utf-8")
        self.assertIsNone(dispatch.load_last_session("/proj/a", self.state))


class AttemptModesTest(unittest.TestCase):
    def test_remembered_session_first_then_continue_then_fresh(self):
        self.assertEqual(
            dispatch.attempt_modes("sess-1", True),
            [("resume", "sess-1"), ("continue", None), ("fresh", None)])

    def test_no_remembered_session_continue_then_fresh(self):
        self.assertEqual(
            dispatch.attempt_modes(None, True),
            [("continue", None), ("fresh", None)])

    def test_no_state_no_history_fresh_only(self):
        self.assertEqual(dispatch.attempt_modes(None, False), [("fresh", None)])

    def test_remembered_session_without_history_still_falls_back_to_fresh(self):
        self.assertEqual(
            dispatch.attempt_modes("sess-1", False),
            [("resume", "sess-1"), ("fresh", None)])


class InteractiveRegistrationTest(unittest.TestCase):
    """WB-19: chat sessions register as last ticket session; interactive
    lineages are ALWAYS forked (never write into an open conversation)."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"

    def tearDown(self):
        remove_tree(self.dir)

    def test_register_marks_interactive_and_roundtrips(self):
        dispatch.register_ticket_session("/proj/a", "sess-chat", self.state)
        entry = dispatch.load_last_entry("/proj/a", self.state)
        self.assertEqual(entry, {"id": "sess-chat", "interactive": True})
        # legacy accessor still answers with the plain id
        self.assertEqual(dispatch.load_last_session("/proj/a", self.state), "sess-chat")

    def test_register_rejects_empty_id_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            dispatch.register_ticket_session("/proj/a", "", self.state)
        with self.assertRaises(ValueError):
            dispatch.register_ticket_session("/proj/a", None, self.state)
        self.assertIsNone(dispatch.load_last_entry("/proj/a", self.state))

    def test_legacy_string_entry_counts_as_non_interactive(self):
        self.state.write_text(json.dumps({"/proj/a": "sess-old"}), encoding="utf-8")
        entry = dispatch.load_last_entry("/proj/a", self.state)
        self.assertEqual(entry, {"id": "sess-old", "interactive": False})

    def test_board_save_stays_non_interactive(self):
        dispatch.save_last_session("/proj/a", "sess-board", self.state)
        entry = dispatch.load_last_entry("/proj/a", self.state)
        self.assertEqual(entry["interactive"], False)

    def test_wb144_background_save_does_not_demote_chat_claim(self):
        """WB-144: a chat session claimed a project as interactive; a later
        background run for the SAME project must not silently degrade the
        entry to non-interactive — that removed the sweep/adoption guard and
        let a duplicate run start on top of the chat's claim (2026-08-16)."""
        dispatch.register_ticket_session("/proj/a", "sess-chat", self.state)
        self.assertIn("sess-chat", dispatch._interactive_ids(self.state))
        # A finished background run writes back the (possibly forked) id.
        dispatch.save_last_session("/proj/a", "sess-bg-fork", self.state)
        self.assertIn("sess-chat", dispatch._interactive_ids(self.state),
                      "chat session dropped out of the interactive set")

    def test_wb144_re_register_moves_chat_claim_to_new_id(self):
        """If the chat session itself hands off (rare, but the mechanism
        must not pin a dead session id forever): re-registering another id
        replaces the interactive claim rather than accumulating stale ones."""
        dispatch.register_ticket_session("/proj/a", "sess-chat-1", self.state)
        dispatch.register_ticket_session("/proj/a", "sess-chat-2", self.state)
        interactive = dispatch._interactive_ids(self.state)
        self.assertIn("sess-chat-2", interactive)
        self.assertNotIn("sess-chat-1", interactive)


class ForcedForkCommandTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {"agent_permission_mode": "acceptEdits", "agent_allowed_tools": "Bash"}

    def test_interactive_lineage_forces_fork_despite_fork_nein(self):
        t = store.Ticket(id="WB-1", title="T", fork="nein")
        cmd = dispatch.build_command("claude", t, "resume", self.cfg,
                                     resume_id="sess-chat", force_fork=True)
        self.assertIn("--resume", cmd)
        self.assertIn("--fork-session", cmd)

    def test_board_lineage_checkbox_decides(self):
        for fork, expected in (("nein", False), ("ja", True)):
            t = store.Ticket(id="WB-1", title="T", fork=fork)
            cmd = dispatch.build_command("claude", t, "resume", self.cfg,
                                         resume_id="sess-board", force_fork=False)
            self.assertEqual("--fork-session" in cmd, expected, f"fork={fork}")


class RunClaudeInteractiveTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self.project = self.dir / "proj"
        self.project.mkdir()
        self.state = self.dir / "state.json"
        self.bin = self.dir / "fake-claude"
        self.ticket = store.Ticket(id="WB-99", title="Test", project=str(self.project),
                                   fork="nein")
        self.cfg = {"claude_bin": str(self.bin), "state_path": str(self.state),
                    "agent_timeout_minutes": 1}

    def tearDown(self):
        remove_tree(self.dir)

    def _write_fake_claude(self, script: str):
        self.bin = Path(sh_stub(self.bin.parent, self.bin.name, script))
        self.cfg["claude_bin"] = str(self.bin)

    def test_interactive_session_is_resumed_forked_even_with_fork_nein(self):
        dispatch.register_ticket_session(str(self.project), "sess-chat", self.state)
        # fake claude only succeeds when BOTH --resume sess-chat AND
        # --fork-session are present — proving the forced fork end to end
        self._write_fake_claude(
            'case "$*" in *"--resume sess-chat"*"--fork-session"*) '
            'echo \'{"result": "geforkt", "session_id": "sess-fork"}\';; '
            '*) exit 1;; esac\n')
        self.assertEqual(dispatch.run_claude(self.ticket, self.cfg), ("geforkt", "sess-fork"))
        # the fork registered by the board run is non-interactive again
        entry = dispatch.load_last_entry(str(self.project), self.state)
        self.assertEqual(entry, {"id": "sess-fork", "interactive": False})


class RunClaudeStateTest(unittest.TestCase):
    """run_claude against a fake claude binary: session memory + fallback."""

    def setUp(self):
        self.dir = temp_dir()
        self.project = self.dir / "proj"
        self.project.mkdir()
        self.state = self.dir / "state.json"
        self.bin = self.dir / "fake-claude"
        self.ticket = store.Ticket(id="WB-99", title="Test", project=str(self.project))
        self.cfg = {"claude_bin": str(self.bin), "state_path": str(self.state),
                    "agent_timeout_minutes": 1}

    def tearDown(self):
        remove_tree(self.dir)

    def _write_fake_claude(self, script: str):
        self.bin = Path(sh_stub(self.bin.parent, self.bin.name, script))
        self.cfg["claude_bin"] = str(self.bin)

    def test_successful_run_remembers_its_session(self):
        self._write_fake_claude(
            'echo \'{"result": "OK", "session_id": "sess-new"}\'\n')
        result = dispatch.run_claude(self.ticket, self.cfg)
        self.assertEqual(result, ("OK", "sess-new"))
        self.assertEqual(
            dispatch.load_last_session(str(self.project), self.state), "sess-new")

    def test_stale_remembered_session_falls_back_and_reremembers(self):
        dispatch.save_last_session(str(self.project), "sess-dead", self.state)
        self._write_fake_claude(
            'case "$*" in *--resume*) exit 1;; esac\n'
            'echo \'{"result": "OK ohne resume", "session_id": "sess-new"}\'\n')
        result = dispatch.run_claude(self.ticket, self.cfg)
        self.assertEqual(result, ("OK ohne resume", "sess-new"))
        self.assertEqual(
            dispatch.load_last_session(str(self.project), self.state), "sess-new")

    def test_remembered_session_is_actually_resumed(self):
        dispatch.save_last_session(str(self.project), "sess-alt", self.state)
        self._write_fake_claude(
            'case "$*" in *"--resume sess-alt"*) '
            'echo \'{"result": "resumed", "session_id": "sess-neu"}\';; '
            '*) exit 1;; esac\n')
        self.assertEqual(dispatch.run_claude(self.ticket, self.cfg), ("resumed", "sess-neu"))


class BlockingTest(unittest.TestCase):
    """The three WB-12 acceptance criteria at the logic layer used by server
    (drag check) and dispatcher (queue recheck)."""

    def setUp(self):
        self.dir = temp_dir()
        self.blocker = store.create_ticket(self.dir, title="Blocker", description="")
        self.dep = store.create_ticket(self.dir, title="Abhängig", description="",
                                       nach=self.blocker.id)

    def tearDown(self):
        remove_tree(self.dir)

    def _reasons(self, t, **kw):
        return store.blocking_reasons(store.load_tickets(self.dir), t, **kw)

    def test_blocked_ticket_does_not_start(self):
        dep = {x.id: x for x in store.load_tickets(self.dir)}[self.dep.id]
        reasons = self._reasons(dep)
        self.assertEqual(len(reasons), 1)
        self.assertIn(self.blocker.id, reasons[0])
        self.assertIn("noch offen", reasons[0])

    def test_after_blocker_erledigt_it_starts(self):
        store.update_ticket(self.dir, self.blocker.id, {"status": "erledigt"})
        dep = {x.id: x for x in store.load_tickets(self.dir)}[self.dep.id]
        self.assertEqual(self._reasons(dep), [])

    def test_exclusion_blocks_both_directions_while_in_arbeit(self):
        a = store.create_ticket(self.dir, title="A", description="",
                                nicht_mit="WB-4")
        b = store.create_ticket(self.dir, title="B", description="")
        self.assertEqual(b.id, "WB-4")
        store.update_ticket(self.dir, a.id, {"status": "in_arbeit"})
        # b never declared a; a's declaration must still block b
        b_now = {x.id: x for x in store.load_tickets(self.dir)}[b.id]
        reasons = self._reasons(b_now)
        self.assertEqual(len(reasons), 1)
        self.assertIn(a.id, reasons[0])
        # dispatcher-side recheck ignores exclusion (runs are serialized)
        self.assertEqual(self._reasons(b_now, include_exclusion=False), [])

    def test_unknown_reference_does_not_block(self):
        t = store.create_ticket(self.dir, title="X", description="", nach="WB-999")
        self.assertEqual(self._reasons(t), [])

    def test_queued_blocked_ticket_bounces_to_offen_without_running(self):
        store.update_ticket(self.dir, self.dep.id, {"status": "in_arbeit"})
        calls = []
        d = make_dispatcher(self, self.dir, runner=lambda t: calls.append(t.id) or "lief")
        d.dispatch(self.dep.id)
        d.join(timeout=5)
        self.assertEqual(calls, [])
        dep = {x.id: x for x in store.load_tickets(self.dir)}[self.dep.id]
        self.assertEqual(dep.status, "offen")
        self.assertIn("Nicht gestartet", dep.body)
        self.assertIn(self.blocker.id, dep.body)


class DispatcherTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self.t1 = store.create_ticket(self.dir, title="Eins", description="")
        self.t2 = store.create_ticket(self.dir, title="Zwei", description="")
        store.update_ticket(self.dir, self.t1.id, {"status": "in_arbeit"})
        store.update_ticket(self.dir, self.t2.id, {"status": "in_arbeit"})

    def tearDown(self):
        remove_tree(self.dir)

    def test_runs_serially_writes_review_and_result(self):
        seen, active = [], []
        lock = threading.Lock()

        def runner(ticket):
            with lock:
                active.append(ticket.id)
                self.assertEqual(len(active), 1)  # never two at once
            time.sleep(0.05)
            seen.append(ticket.id)
            with lock:
                active.remove(ticket.id)
            return f"Ergebnis für {ticket.id}"

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.dispatch(self.t2.id)
        d.join(timeout=5)
        # WB-100 discipline: join can return (or time out) before the worker has
        # written the result, so wait for the state instead of asserting the
        # instant it returns. This one still slipped through as "flaky".
        # WB-183 restored the ordering guarantee (one FIFO worker per project),
        # so this pins order again — it was temporarily relaxed to serialisation
        # only while the bug was open.
        wait_until(lambda: seen == ["WB-1", "WB-2"])
        self.assertEqual(seen, ["WB-1", "WB-2"])
        wait_until(lambda: {x.id: x for x in store.load_tickets(self.dir)}
                   ["WB-1"].status == "review")
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(loaded["WB-1"].status, "review")
        self.assertIn("Ergebnis für WB-1", loaded["WB-1"].body)

    def test_wb137_tokens_and_cost_land_on_the_ticket(self):
        """The runner emits events through on_event; the dispatcher must
        capture cost + tokens from the CLI's `result` event and persist them
        to the ticket. Before this fix, only 'tokens' was tracked live for
        the running card — nothing survived into the finished ticket."""
        def runner(ticket, on_start=None, on_event=None, on_pid=None):
            if on_start: on_start({"parent": None, "forked": False, "mode": "fresh"})
            # Shape mirrors what dispatch._consume_event handles (WB-137).
            progress = {"steps": 0, "last_tool": None, "tokens": 0,
                        "session": "sess-137", "error": None,
                        "started": "12:00", "last": "12:00:00", "last_ts": 0}
            dispatch._consume_event({
                "type": "result", "subtype": "success", "is_error": False,
                "session_id": "sess-137", "total_cost_usd": 0.6321,
                "usage": {"input_tokens": 800, "output_tokens": 4500,
                          "cache_creation_input_tokens": 1200,
                          "cache_read_input_tokens": 60000},
                "result": "fertig",
            }, progress)
            if on_event: on_event(dict(progress))
            return "fertig", "sess-137"

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.join(timeout=5)
        t = {x.id: x for x in store.load_tickets(self.dir)}[self.t1.id]
        self.assertEqual(t.status, "review")
        self.assertEqual(t.tokens_in, "800")
        self.assertEqual(t.tokens_out, "4500")
        self.assertEqual(t.tokens_cache, "61200")   # 1200 + 60000
        self.assertEqual(t.cost_usd, "0.6321")

    def test_wb139_duration_seconds_is_recorded_on_finished_ticket(self):
        """WB-139: the dispatcher itself must persist the wall-clock — the
        file mtime is not reliable (user acceptance overwrites it). Small
        sleep in the runner; assert duration_s >= it."""
        def runner(ticket, on_start=None, on_event=None, on_pid=None):
            time.sleep(0.25)
            return "fertig", "sess-d1"

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.join(timeout=5)
        t = {x.id: x for x in store.load_tickets(self.dir)}[self.t1.id]
        self.assertEqual(t.status, "review")
        self.assertTrue(t.duration_s, "duration_s must be set after a run")
        self.assertGreaterEqual(int(t.duration_s), 0)
        # A field with only integer seconds parses cleanly.
        int(t.duration_s)   # must not raise

    def test_wb139_duration_recorded_even_when_the_run_fails(self):
        """Failure carries the same field so opencode's failed attempts show
        up in the benchmark — otherwise 'nothing measured' would silently
        skip the interesting cases."""
        def runner(ticket, on_start=None, on_event=None, on_pid=None):
            time.sleep(0.1)
            raise dispatch.DispatchError("Beispielausfall")

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.join(timeout=5)
        t = {x.id: x for x in store.load_tickets(self.dir)}[self.t1.id]
        self.assertEqual(t.status, "fehlgeschlagen")
        self.assertTrue(t.duration_s, "even a failure must record duration_s")

    def test_wb137_board_html_shows_cost_and_tokens_on_finished_cards(self):
        """Pin the shape the way test_swipe pins the swipe handler — a rename
        or removal here must consciously touch this test."""
        board = (Path(__file__).resolve().parent.parent
                 / "src/werkbank/board.html").read_text(encoding="utf-8")
        for needle in (
            't.cost_usd',           # card reads the frontmatter field
            't.tokens_in', 't.tokens_out', 't.tokens_cache',
            't.duration_s',         # WB-139
            '["review", "erledigt", "fehlgeschlagen"].includes(t.status)',
            '/api/tickets/" + t.id + "/move-up',    # WB-138
            '.card-move-up',
            '/api/tickets/" + t.id + "/review',     # WB-140
            "🔍 Review-Bot",
        ):
            self.assertIn(needle, board, f"WB-137/138/139/140 card shape lost: {needle}")

    def test_wb137_runner_without_events_leaves_ticket_fields_empty(self):
        """A runner that never emits a result event (opencode, tests) must
        not fabricate zeros — empty means 'not measured' on the board."""
        def runner(ticket, on_start=None, on_event=None, on_pid=None):
            return "fertig", "sess-none"

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.join(timeout=5)
        t = {x.id: x for x in store.load_tickets(self.dir)}[self.t1.id]
        self.assertEqual(t.status, "review")
        self.assertEqual(t.tokens_in, "")
        self.assertEqual(t.cost_usd, "")

    def test_duplicate_dispatch_ignored_while_pending(self):
        calls = []

        def runner(ticket):
            time.sleep(0.05)
            calls.append(ticket.id)
            return "ok"

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.dispatch(self.t1.id)
        d.join(timeout=5)
        self.assertEqual(calls, ["WB-1"])

    def test_runner_failure_lands_in_fehlgeschlagen_with_reason(self):
        def runner(ticket):
            raise dispatch.DispatchError("kein claude gefunden")

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.join(timeout=5)
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(loaded["WB-1"].status, "fehlgeschlagen")
        self.assertIn("kein claude gefunden", loaded["WB-1"].body)

    def test_internal_error_lands_in_fehlgeschlagen(self):
        def runner(ticket):
            raise RuntimeError("völlig unerwartet")

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.join(timeout=5)
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(loaded["WB-1"].status, "fehlgeschlagen")
        self.assertIn("völlig unerwartet", loaded["WB-1"].body)


if __name__ == "__main__":
    unittest.main()


class SweepOrphanedTest(unittest.TestCase):
    """WB-17: a board restart during a run loses the finalization step, leaving
    the ticket forever in in_arbeit. The startup sweep must surface that."""

    def setUp(self):
        self.dir = temp_dir()
        self.open_t = store.create_ticket(self.dir, title="Offen bleibt", description="")
        self.orphan = store.create_ticket(self.dir, title="Verwaist", description="")
        self.review_t = store.create_ticket(self.dir, title="Review bleibt", description="")
        store.update_ticket(self.dir, self.orphan.id, {"status": "in_arbeit"})
        store.update_ticket(self.dir, self.review_t.id, {"status": "in_arbeit"})
        store.update_ticket(self.dir, self.review_t.id, {"status": "review"})

    def tearDown(self):
        remove_tree(self.dir)

    def test_sweep_moves_orphaned_in_arbeit_to_fehlgeschlagen(self):
        swept = dispatch.sweep_orphaned(self.dir)
        self.assertEqual(swept, [self.orphan.id])
        loaded = {t.id: t for t in store.load_tickets(self.dir)}
        self.assertEqual(loaded[self.orphan.id].status, "fehlgeschlagen")
        self.assertIn("neu gestartet", loaded[self.orphan.id].body)
        self.assertEqual(loaded[self.open_t.id].status, "offen")
        self.assertEqual(loaded[self.review_t.id].status, "review")

    def test_sweep_on_clean_board_changes_nothing(self):
        dispatch.sweep_orphaned(self.dir)  # first sweep clears the orphan
        self.assertEqual(dispatch.sweep_orphaned(self.dir), [])


class SweepKillsOrphanProcessTest(unittest.TestCase):
    """WB-75 → WB-230: a claude process outlives its board when the board
    restarts. Sweep must FIND it (via the PID we recorded, cmdline check
    guards against PID reuse and blind name-kills). WB-230 changed the
    outcome: the sweep no longer kills a live matching process — it
    marks the ticket `orphaned=ja` and lets the user decide via the
    card's Beenden-Knopf. Decoys stay untouched either way."""

    def setUp(self):
        self.dir = temp_dir()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            try:
                p.kill()
            except OSError:
                pass
            try:
                p.wait(timeout=2)
            except Exception:
                pass
        remove_tree(self.dir)

    def _spawn(self, *extra_argv):
        # argv extras land in the process's command line verbatim — that is what
        # the match helper reads (/proc on Linux, `ps` elsewhere). `python -c`
        # swallows the extras (they show up as sys.argv), so the sleep simply
        # hangs until the test kills it.
        #
        # ONE LINE on purpose: the program used to contain a newline, which
        # /proc reports verbatim but `ps` on macOS does not — the match then
        # failed there and the test blamed the sweep. A real `claude` command
        # line has no newlines either, so the stand-in should not have one.
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(3600)",
             *extra_argv])
        self.procs.append(p)
        return p

    @posix_only
    def test_kills_matching_orphan_and_spares_decoys(self):
        target = store.create_ticket(self.dir, title="Verwaist", description="")
        store.update_ticket(self.dir, target.id, {"status": "in_arbeit"})
        # matching process: cmdline contains BOTH the ticket id and 'claude'
        orphan = self._spawn("claude", "-p", f"prompt for {target.id}")
        # decoy 1: 'claude' in cmdline but different ticket id
        decoy_claude = self._spawn("claude", "-p", "prompt for WB-999999")
        # decoy 2: same ticket id string, but no 'claude'
        decoy_named = self._spawn("some-other-tool", target.id)
        store.update_ticket(self.dir, target.id, {"pid": str(orphan.pid)})

        # If this fails on a platform without /proc, the useful fact is what
        # `ps` actually returned — guessing cost a CI round trip once already.
        seen = dispatch._read_cmdline(orphan.pid)
        swept = dispatch.sweep_orphaned(self.dir)
        self.assertEqual(swept, [target.id], f"cmdline gelesen: {seen!r}")

        # WB-230: live matching process must STAY alive — the "silent kill"
        # was the very cost the bug ticket protested (up to an hour of
        # on-disk work would be lost with no user confirmation).
        self.assertIsNone(
            orphan.poll(),
            f"orphan was killed by sweep_orphaned; WB-230 says leave it. "
            f"_read_cmdline said: {seen!r}")
        # Decoys still running either way.
        self.assertIsNone(decoy_claude.poll())
        self.assertIsNone(decoy_named.poll())

        loaded = {t.id: t for t in store.load_tickets(self.dir)}[target.id]
        # New shape: still in_arbeit but flagged, with the German note that
        # names the Beenden route.
        self.assertEqual(loaded.status, "in_arbeit")
        self.assertEqual(loaded.orphaned, "ja")
        self.assertIn("Verwaister Lauf", loaded.body)
        self.assertIn(str(orphan.pid), loaded.body)

    def test_stale_pid_without_matching_cmdline_is_not_killed(self):
        target = store.create_ticket(self.dir, title="PID recycelt",
                                     description="")
        store.update_ticket(self.dir, target.id, {"status": "in_arbeit"})
        # Same PID could now belong to something unrelated after a reboot.
        stranger = self._spawn("some-other-tool", "unrelated")
        store.update_ticket(self.dir, target.id, {"pid": str(stranger.pid)})

        swept = dispatch.sweep_orphaned(self.dir)
        self.assertEqual(swept, [target.id])
        self.assertIsNone(stranger.poll(),
                          "stranger with same PID must not be killed")
        loaded = {t.id: t for t in store.load_tickets(self.dir)}[target.id]
        self.assertEqual(loaded.status, "fehlgeschlagen")
        self.assertEqual(loaded.pid, "")
        # Falls back to the classic 'lost' message; does NOT claim we killed it.
        self.assertNotIn("beendet", loaded.body)
        self.assertIn("verloren", loaded.body)

    def test_run_records_pid_and_clears_it_on_finalize(self):
        """The on_pid callback wires run_claude -> store: the pid is visible in
        the ticket file while a run is active, and cleared on completion."""
        t = store.create_ticket(self.dir, title="PID-Zyklus", description="")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        seen_pid = threading.Event()

        def runner(ticket, on_pid=None):
            if on_pid:
                on_pid(424242)
            # Give the writer thread a moment before returning.
            seen_pid.wait(2)
            return ("fertig", None)

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(t.id)
        # Wait for the pid to show up in the ticket file.
        end = time.time() + 3
        while time.time() < end:
            loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
            if loaded.pid == "424242":
                break
            time.sleep(0.05)
        self.assertEqual(loaded.pid, "424242",
                         "run_claude did not persist the pid")
        seen_pid.set()
        d.join(timeout=5)
        d.stop()
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.status, "review")
        self.assertEqual(loaded.pid, "",
                         "pid must be cleared once the run is finalized")


class NoRestartRuleTest(unittest.TestCase):
    def test_prompt_forbids_board_restart(self):
        t = store.Ticket(id="WB-99", title="X", body="## Beschreibung\n\nY\n\n## Ergebnis\n\n_(noch offen)_\n")
        self.assertIn("niemals das Werkbank-Board neu", dispatch.build_prompt(t))


class RunVisibilityTest(unittest.TestCase):
    """WB-20: while a run is active the dispatcher publishes what it knows;
    afterwards the run's real session id is persisted into the ticket."""

    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(self.dir, title="Sichtbar", description="")
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})

    def tearDown(self):
        remove_tree(self.dir)

    def test_active_run_is_published_and_cleared(self):
        started = threading.Event()
        release = threading.Event()

        def runner(t, on_start=None):
            if on_start:
                on_start({"parent": "eltern-123", "forked": True})
            started.set()
            release.wait(5)
            return "fertig", "sess-456"

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(self.t.id)
        self.assertTrue(started.wait(5))
        info = d.active_runs().get(self.t.id)
        self.assertIsNotNone(info)
        self.assertEqual(info["parent"], "eltern-123")
        self.assertTrue(info["forked"])
        self.assertIn("started", info)
        release.set()
        d.join(timeout=5)
        self.assertEqual(d.active_runs(), {})

    def test_session_id_persisted_into_ticket(self):
        def runner(t, on_start=None):
            return "fertig", "sess-789"

        d = make_dispatcher(self, self.dir, runner=runner)
        d.dispatch(self.t.id)
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.status, "review")
        self.assertEqual(after.session, "sess-789")

    def test_plain_string_runner_still_works(self):
        d = make_dispatcher(self, self.dir, runner=lambda t, on_start=None: "nur text")
        d.dispatch(self.t.id)
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.status, "review")
        self.assertEqual(after.session, "")

    def test_run_claude_reports_start_and_returns_session(self):
        stub = Path(sh_stub(self.dir, "fake-claude",
                            "echo '{\"result\": \"ok\", \"session_id\": \"s-neu\"}'\n"))
        state = self.dir / "state.json"
        state.write_text(json.dumps({str(self.dir): "eltern-abc"}),
                         encoding="utf-8")
        seen = []
        t = store.Ticket(id="WB-77", title="X", status="in_arbeit",
                         project=str(self.dir), body="## Beschreibung\n\nx\n\n## Ergebnis\n\n_(noch offen)_\n")
        result, session = dispatch.run_claude(
            t, {"claude_bin": str(stub), "state_path": str(state),
                "agent_timeout_minutes": 1},
            on_start=seen.append)
        self.assertEqual((result, session), ("ok", "s-neu"))
        self.assertEqual(seen[0]["parent"], "eltern-abc")
        self.assertFalse(seen[0]["forked"])  # fork default nein, non-interactive entry


class HandoverTest(unittest.TestCase):
    """WB-22: interactive lineage + fork nein => hand the ticket to the live
    chat session via a handover marker instead of spawning a background run;
    unclaimed handovers fall back to the normal run after a deadline."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        self.t = store.create_ticket(self.dir, title="Uebergabe", description="")
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        self.calls = []
        force_marker_handover(self)

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self, timeout_min):
        def runner(t, on_start=None):
            self.calls.append(t.id)
            return "hintergrund", "sess-bg"
        return make_dispatcher(self, 
            self.dir, cfg={"state_path": str(self.state),
                           "default_project": str(self.dir),
                           "chat_handover_minutes": timeout_min},
            runner=runner)

    def _load(self):
        return {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]

    def test_interactive_lineage_sets_marker_instead_of_running(self):
        dispatch.register_ticket_session(str(self.dir), "chat-111", self.state)
        d = self._dispatcher(timeout_min=10)
        d.dispatch(self.t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [])  # no background run
        after = self._load()
        self.assertEqual(after.handover, "chat-111")
        self.assertEqual(after.status, "in_arbeit")

    def test_fork_ja_skips_handover(self):
        dispatch.register_ticket_session(str(self.dir), "chat-111", self.state)
        store.update_ticket(self.dir, self.t.id, {"fork": "ja"})
        d = self._dispatcher(timeout_min=10)
        d.dispatch(self.t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [self.t.id])
        self.assertEqual(self._load().handover, "")

    def test_opencode_ticket_is_never_handed_to_the_chat_session(self):
        # WB-103: the assignee names the worker — an opencode ticket goes to
        # the opencode lane even when an interactive lineage is registered.
        from types import SimpleNamespace
        from unittest import mock
        o = store.create_ticket(self.dir, title="Lokalarbeit", description="",
                                assignee="opencode", gate="Tests laufen durch")
        store.update_ticket(self.dir, o.id, {"status": "in_arbeit"})
        dispatch.register_ticket_session(str(self.dir), "chat-111", self.state)
        oc_calls = []

        def fake_work_ticket(t, cfg, on_progress=None, **_):
            oc_calls.append(t.id)
            return SimpleNamespace(result="ok", status="review", changes={})

        d = self._dispatcher(timeout_min=10)
        with mock.patch.object(dispatch.opencode, "work_ticket", fake_work_ticket):
            d.dispatch(o.id)
            d.join(timeout=5)
        self.assertEqual(oc_calls, [o.id])          # the local lane ran it
        after = {x.id: x for x in store.load_tickets(self.dir)}[o.id]
        self.assertEqual(after.handover, "")        # and no chat handover happened
        self.assertEqual(after.status, "review")

    def test_unclaimed_handover_falls_back_to_background_run(self):
        dispatch.register_ticket_session(str(self.dir), "chat-111", self.state)
        d = self._dispatcher(timeout_min=0.001)  # ~60 ms deadline
        d.dispatch(self.t.id)
        deadline = time.time() + 5
        while time.time() < deadline and not self.calls:
            time.sleep(0.05)
        d.join(timeout=5)
        self.assertEqual(self.calls, [self.t.id])
        after = self._load()
        self.assertEqual(after.handover, "")
        self.assertEqual(after.status, "review")

    def test_claimed_handover_is_left_alone(self):
        dispatch.register_ticket_session(str(self.dir), "chat-111", self.state)
        d = self._dispatcher(timeout_min=0.001)
        d.dispatch(self.t.id)
        # the chat session claims immediately: clears marker, notes itself
        deadline = time.time() + 5
        while time.time() < deadline and self._load().handover != "chat-111":
            time.sleep(0.01)
        store.update_ticket(self.dir, self.t.id,
                            {"handover": "", "session": "chat-111"})
        time.sleep(0.3)  # let the (disarmed) fallback timer fire
        self.assertEqual(self.calls, [])
        after = self._load()
        self.assertEqual(after.status, "in_arbeit")
        self.assertEqual(after.session, "chat-111")

    def test_sweep_spares_handover_and_live_chat_claims(self):
        dispatch.register_ticket_session(str(self.dir), "chat-111", self.state)
        store.update_ticket(self.dir, self.t.id, {"handover": "chat-111"})
        claimed = store.create_ticket(self.dir, title="Beansprucht", description="")
        store.update_ticket(self.dir, claimed.id,
                            {"status": "in_arbeit", "session": "chat-111"})
        orphan = store.create_ticket(self.dir, title="Waise", description="")
        store.update_ticket(self.dir, orphan.id, {"status": "in_arbeit"})
        swept = dispatch.sweep_orphaned(self.dir, state_path=self.state)
        self.assertEqual(swept, [orphan.id])


class PerProjectLineageTest(unittest.TestCase):
    """WB-28: the remembered ticket session is looked up by the TICKET's
    project — two projects never share a lineage."""

    def test_handover_targets_the_tickets_own_project_session(self):
        force_marker_handover(self)
        base = temp_dir()
        try:
            proj_a, proj_b = base / "a", base / "b"
            proj_a.mkdir(); proj_b.mkdir()
            state = base / "state.json"
            dispatch.register_ticket_session(str(proj_a), "chat-AAA", state)
            dispatch.register_ticket_session(str(proj_b), "chat-BBB", state)
            tickets_dir = base / "tickets"
            t = store.create_ticket(tickets_dir, title="Fuer B", description="",
                                    project=str(proj_b))
            store.update_ticket(tickets_dir, t.id, {"status": "in_arbeit"})
            d = make_dispatcher(self, 
                tickets_dir, cfg={"state_path": str(state),
                                  "default_project": str(proj_a),
                                  "chat_handover_minutes": 10},
                runner=lambda tk, on_start=None: ("nie", None))
            d.dispatch(t.id)
            d.join(timeout=5)
            after = {x.id: x for x in store.load_tickets(tickets_dir)}[t.id]
            self.assertEqual(after.handover, "chat-BBB")  # B's lineage, not A's
        finally:
            remove_tree(base)


class QueueColumnTest(unittest.TestCase):
    """WB-40: 'zu_bearbeiten' is a queue — the next ticket starts by itself when
    the running one finishes; per project, review either blocks that or not."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"   # empty: no interactive lineage
        self.started = []

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self, nonblocking=None):
        def runner(t, on_start=None):
            self.started.append(t.id)
            return "fertig", "sess-x"
        cfg = {"state_path": str(self.state), "default_project": str(self.dir),
               "nonblocking_review": nonblocking or {}}
        return make_dispatcher(self, self.dir, cfg=cfg, runner=runner)

    def _queued(self, title, priority="normal", project=None):
        t = store.create_ticket(self.dir, title=title, description="",
                                priority=priority, project=project or str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        return t

    def _status(self, tid):
        return {x.id: x for x in store.load_tickets(self.dir)}[tid].status

    def test_pump_starts_highest_priority_queued_ticket(self):
        low = self._queued("Niedrig", priority="niedrig")
        high = self._queued("Hoch", priority="hoch")
        d = self._dispatcher()
        d.pump_queue()
        d.join(timeout=5)
        wait_until(lambda: self.started == [high.id])
        self.assertEqual(self.started, [high.id])          # priority wins
        self.assertEqual(self._status(high.id), "review")
        self.assertEqual(self._status(low.id), "zu_bearbeiten")

    def test_finished_run_pulls_the_next_queued_ticket(self):
        first = self._queued("Erstes")
        second = self._queued("Zweites")
        d = self._dispatcher(nonblocking={str(self.dir): True})
        d.pump_queue()
        deadline = time.time() + 5
        while time.time() < deadline and len(self.started) < 2:
            time.sleep(0.05)
        d.join(timeout=5)
        wait_until(lambda: self.started == [first.id, second.id])
        self.assertEqual(self.started, [first.id, second.id])  # chained by itself

    def test_review_blocks_the_queue_by_default(self):
        done = store.create_ticket(self.dir, title="Wartet auf Abnahme", description="")
        store.update_ticket(self.dir, done.id, {"status": "review"})
        queued = self._queued("Darf nicht starten")
        d = self._dispatcher()
        d.pump_queue()
        d.join(timeout=2)
        self.assertEqual(self.started, [])
        self.assertEqual(self._status(queued.id), "zu_bearbeiten")

    def test_nonblocking_project_starts_despite_review(self):
        done = store.create_ticket(self.dir, title="In Review", description="")
        store.update_ticket(self.dir, done.id, {"status": "review"})
        queued = self._queued("Darf starten")
        d = self._dispatcher(nonblocking={str(self.dir): True})
        d.pump_queue()
        d.join(timeout=5)
        wait_until(lambda: self.started == [queued.id])
        self.assertEqual(self.started, [queued.id])

    def test_review_of_another_project_never_blocks(self):
        other = self.dir / "anderes"
        other.mkdir()
        done = store.create_ticket(self.dir, title="Fremd", description="",
                                   project=str(other))
        store.update_ticket(self.dir, done.id, {"status": "review"})
        queued = self._queued("Eigenes")
        d = self._dispatcher()
        d.pump_queue()
        d.join(timeout=5)
        wait_until(lambda: self.started == [queued.id])
        self.assertEqual(self.started, [queued.id])

    def test_wb138_pump_queue_honours_manual_position(self):
        """After ↑ nach oben, pump_queue picks the moved ticket next — not
        the lower ticket number."""
        first = self._queued("Erstling")
        second = self._queued("Zweitling")
        store.move_queued_up(self.dir, second.id)   # second → top
        d = self._dispatcher()
        d.pump_queue()
        d.join(timeout=5)
        wait_until(lambda: self.started == [second.id])
        self.assertEqual(self.started, [second.id])

    def test_link_blocked_ticket_stays_queued(self):
        blocker = store.create_ticket(self.dir, title="Blocker", description="")
        queued = self._queued("Wartet")
        store.update_ticket(self.dir, queued.id, {"nach": blocker.id})
        d = self._dispatcher()
        d.pump_queue()
        d.join(timeout=2)
        self.assertEqual(self.started, [])
        self.assertEqual(self._status(queued.id), "zu_bearbeiten")

    def test_wb136_nach_review_does_not_block_under_nonblocking(self):
        """WB-136: user reported 'review blockierte den luna kameramann obwohl
        review nicht blockierend eingestellt ist'. The 'same-project review'
        check honoured nonblocking_review, but the `nach`-link check did NOT —
        a linked ticket in review still held things up, contradicting the
        user's setting. With nonblocking_review, a review-status blocker means
        'agent is done, just waiting on user' and must NOT stall the queue."""
        blocker = store.create_ticket(self.dir, title="Blocker",
                                      description="", project=str(self.dir))
        store.update_ticket(self.dir, blocker.id, {"status": "review"})
        queued = self._queued("Folge")
        store.update_ticket(self.dir, queued.id, {"nach": blocker.id})
        d = self._dispatcher(nonblocking={str(self.dir): True})
        d.pump_queue()
        d.join(timeout=5)
        wait_until(lambda: self.started == [queued.id])
        self.assertEqual(self.started, [queued.id])

    def test_wb136_nach_failed_still_blocks_even_when_nonblocking(self):
        """Guardrail for the WB-136 fix: only 'review' becomes ok under
        nonblocking; a fehlgeschlagen or in_arbeit blocker must still hold
        the queue (the agent has not succeeded)."""
        blocker = store.create_ticket(self.dir, title="Kaputt",
                                      description="", project=str(self.dir))
        store.update_ticket(self.dir, blocker.id, {"status": "fehlgeschlagen"})
        queued = self._queued("Folge")
        store.update_ticket(self.dir, queued.id, {"nach": blocker.id})
        d = self._dispatcher(nonblocking={str(self.dir): True})
        d.pump_queue()
        d.join(timeout=2)
        self.assertEqual(self.started, [])
        self.assertEqual(self._status(queued.id), "zu_bearbeiten")

    def test_other_projects_running_ticket_does_not_block(self):
        other = self.dir / "fremd"
        other.mkdir()
        busy = store.create_ticket(self.dir, title="Fremd laeuft", description="",
                                   project=str(other))
        store.update_ticket(self.dir, busy.id, {"status": "in_arbeit"})
        queued = self._queued("Eigenes darf starten")
        d = self._dispatcher()
        d.pump_queue()
        d.join(timeout=5)
        wait_until(lambda: self.started == [queued.id])
        self.assertEqual(self.started, [queued.id])


class LiveStatusTest(unittest.TestCase):
    """WB-37: while a run is going, the board must see what it is doing and
    whether it died — including usage limits."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        self.t = store.Ticket(id="WB-90", title="Live", status="in_arbeit",
                              project=str(self.dir),
                              body="## Beschreibung\n\nx\n\n## Ergebnis\n\n_(noch offen)_\n")

    def tearDown(self):
        remove_tree(self.dir)

    def _stub(self, script):
        return {"claude_bin": sh_stub(self.dir, "fake-claude", script),
                "state_path": str(self.state), "agent_timeout_minutes": 1}

    def test_stream_events_feed_progress_and_result(self):
        cfg = self._stub(
            "echo '{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"s-live\"}'\n"
            "echo '{\"type\":\"assistant\",\"message\":{\"content\":"
            "[{\"type\":\"tool_use\",\"name\":\"Bash\"}]}}'\n"
            "echo '{\"type\":\"assistant\",\"message\":{\"content\":"
            "[{\"type\":\"tool_use\",\"name\":\"Edit\"}]}}'\n"
            "echo '{\"type\":\"result\",\"subtype\":\"success\",\"result\":\"fertig\","
            "\"session_id\":\"s-live\",\"usage\":{\"input_tokens\":10,\"output_tokens\":5}}'\n")
        seen = []
        result, session = dispatch.run_claude(self.t, cfg, on_event=seen.append)
        self.assertEqual((result, session), ("fertig", "s-live"))
        last = seen[-1]
        self.assertEqual(last["steps"], 2)              # two tool uses
        self.assertEqual(last["last_tool"], "Edit")
        self.assertEqual(last["tokens"], 15)
        self.assertIsNone(last["error"])

    def test_usage_limit_is_named_in_plain_german(self):
        cfg = self._stub("echo 'Claude AI usage limit reached' >&2\nexit 1\n")
        with self.assertRaises(dispatch.DispatchError) as cm:
            dispatch.run_claude(self.t, cfg)
        self.assertIn("Nutzungslimit", str(cm.exception))

    def test_error_result_event_is_reported_as_failure(self):
        cfg = self._stub(
            "echo '{\"type\":\"result\",\"subtype\":\"error_max_turns\",\"is_error\":true,"
            "\"result\":\"zu viele Schritte\",\"session_id\":\"s-err\"}'\n")
        with self.assertRaises(dispatch.DispatchError) as cm:
            dispatch.run_claude(self.t, cfg)
        self.assertIn("zu viele Schritte", str(cm.exception))

    def test_log_is_written_while_the_run_is_still_going(self):
        marker = self.dir / "gestartet"
        cfg = self._stub(
            "echo '{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"s-log\"}'\n"
            f"touch {marker}\n"
            f"while [ ! -f {sh_path(self.dir)}/weiter ]; do sleep 0.05; done\n"
            "echo '{\"type\":\"result\",\"subtype\":\"success\",\"result\":\"spaet\","
            "\"session_id\":\"s-log\"}'\n")
        log = dispatch._log_path(self.t.id)
        if log.exists():
            log.unlink()
        out = {}
        th = threading.Thread(target=lambda: out.update(
            zip(("result", "session"), dispatch.run_claude(self.t, cfg))))
        th.start()
        deadline = time.time() + 5
        while time.time() < deadline and not marker.exists():
            time.sleep(0.02)
        wait_until(lambda: log.exists() and "s-log" in log.read_text(encoding="utf-8"))
        mid_run = log.read_text(encoding="utf-8") if log.exists() else ""
        (self.dir / "weiter").write_text("", encoding="utf-8")   # let the stub finish
        th.join(timeout=5)
        self.assertIn("s-log", mid_run)        # log had content BEFORE the end
        self.assertEqual(out.get("result"), "spaet")


class StallDetectionTest(unittest.TestCase):
    """WB-37: a run that stops reporting must be visible as such."""

    def test_idle_seconds_are_exposed_for_the_board(self):
        d = temp_dir()
        try:
            t = store.create_ticket(d, title="Stumm", description="")
            store.update_ticket(d, t.id, {"status": "in_arbeit"})
            started = threading.Event()
            release = threading.Event()

            def runner(ticket, on_start=None, on_event=None):
                on_start({"parent": None, "forked": False, "mode": "fresh"})
                on_event({"steps": 1, "last_tool": "Bash", "tokens": 7,
                          "session": "s-1", "error": None})
                started.set()
                release.wait(5)
                return "fertig", "s-1"

            disp = make_dispatcher(self, d, cfg={"default_project": str(d)},
                                       runner=runner)
            disp.dispatch(t.id)
            self.assertTrue(started.wait(5))
            time.sleep(1.1)
            info = disp.active_runs()[t.id]
            self.assertEqual(info["steps"], 1)
            self.assertEqual(info["last_tool"], "Bash")
            self.assertEqual(info["tokens"], 7)
            self.assertGreaterEqual(info["idle_seconds"], 1)   # silent for ~1s
            release.set()
            disp.join(timeout=5)
        finally:
            remove_tree(d)


class RateLimitEventTest(unittest.TestCase):
    """WB-37: the CLI reports quota state live (rate_limit_event) — the board
    must be able to show it before the run dies of it."""

    def test_quota_event_is_folded_into_progress(self):
        progress = {"steps": 0, "last_tool": None, "tokens": 0,
                    "session": None, "error": None, "limit": None}
        dispatch._consume_event({
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed_warning", "utilization": 0.82,
                                "rateLimitType": "seven_day",
                                "resetsAt": 1787061600},
            "session_id": "s-1"}, progress)
        self.assertEqual(progress["limit"]["percent"], 82)
        self.assertEqual(progress["limit"]["kind"], "seven_day")
        self.assertFalse(progress["limit"]["blocked"])

    def test_rejected_quota_marks_blocked(self):
        progress = {"steps": 0, "last_tool": None, "tokens": 0,
                    "session": None, "error": None, "limit": None}
        dispatch._consume_event({
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "utilization": 1.0,
                                "rateLimitType": "five_hour"}}, progress)
        self.assertTrue(progress["limit"]["blocked"])


class LimitResumeTest(unittest.TestCase):
    """WB-57: a run that dies of the usage limit must resume by itself once
    the quota resets — the user should never have to say 'continue'."""

    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(self.dir, title="Nach Limit weiter", description="")
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        self.calls = []

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self, runner):
        return make_dispatcher(self, self.dir, cfg={"default_project": str(self.dir),
                                                  "state_path": str(self.dir / "s.json")},
                                   runner=runner)

    def test_limit_failure_requeues_instead_of_failing(self):
        reset_at = time.time() + 3600

        def runner(t, on_start=None, on_event=None):
            self.calls.append(t.id)
            raise dispatch.LimitError("Nutzungslimit erreicht", resets_at=reset_at)

        d = self._dispatcher(runner)
        d.dispatch(self.t.id)
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        # WB-69: stays visible in "In Arbeit" (red on the board), never fails
        self.assertEqual(after.status, "in_arbeit")
        self.assertIn("Kontingent", after.body)
        self.assertAlmostEqual(d.pause_until, reset_at, delta=2)

    def test_queue_stays_paused_until_the_reset(self):
        d = self._dispatcher(lambda t, on_start=None, on_event=None: ("ok", "s"))
        d.pause_until = time.time() + 600
        store.update_ticket(self.dir, self.t.id, {"status": "zu_bearbeiten"})
        d.pump_queue()
        d.join(timeout=2)
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.status, "zu_bearbeiten")
        self.assertIn("Kontingent", d.pause_reason() or "")

    def test_queue_runs_again_once_the_reset_has_passed(self):
        started = []
        d = self._dispatcher(lambda t, on_start=None, on_event=None:
                             (started.append(t.id), ("ok", "s"))[1])
        d.pause_until = time.time() - 1          # reset is in the past
        store.update_ticket(self.dir, self.t.id, {"status": "zu_bearbeiten"})
        d.pump_queue()
        d.join(timeout=5)
        self.assertEqual(started, [self.t.id])
        self.assertIsNone(d.pause_reason())

    def test_reset_time_comes_from_the_cli_event_when_present(self):
        stub = Path(sh_stub(self.dir, "fake-claude",
                        "echo '{\"type\":\"rate_limit_event\",\"rate_limit_info\":"
                        "{\"status\":\"rejected\",\"utilization\":1.0,"
                        "\"rateLimitType\":\"five_hour\",\"resetsAt\":2000000000}}'\n"
                        "echo 'Claude AI usage limit reached' >&2\n"
                        "exit 1\n"))
        t = store.Ticket(id="WB-80", title="X", status="in_arbeit",
                         project=str(self.dir),
                         body="## Beschreibung\n\nx\n\n## Ergebnis\n\n_(noch offen)_\n")
        with self.assertRaises(dispatch.LimitError) as cm:
            dispatch.run_claude(t, {"claude_bin": str(stub), "agent_timeout_minutes": 1,
                                    "state_path": str(self.dir / "s.json")})
        self.assertEqual(cm.exception.resets_at, 2000000000)


class QueueTickerTest(unittest.TestCase):
    """WB-59: the queue must move on its own. Until now pump_queue only ran
    after an API write or a finished dispatched run — so a ticket finalized by
    a CHAT session (which writes through the store, not the API) left the queue
    standing still with a free agent and an unblocked ticket."""

    def setUp(self):
        self.dir = temp_dir()
        self.started = []

    def tearDown(self):
        remove_tree(self.dir)

    def test_queued_ticket_starts_without_any_api_call(self):
        t = store.create_ticket(self.dir, title="Wartet", description="")
        store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        d = make_dispatcher(self, 
            self.dir,
            cfg={"default_project": str(self.dir), "state_path": str(self.dir / "s.json"),
                 "queue_poll_seconds": 0.2},
            runner=lambda tk, on_start=None, on_event=None:
                (self.started.append(tk.id), ("ok", "s"))[1])
        deadline = time.time() + 5
        while time.time() < deadline and not self.started:
            time.sleep(0.1)          # nobody calls pump_queue — the ticker must
        d.join(timeout=5)
        wait_until(lambda: self.started == [t.id])
        self.assertEqual(self.started, [t.id])
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.status, "review")

    def test_ticker_does_not_start_anything_while_paused(self):
        t = store.create_ticket(self.dir, title="Pausiert", description="")
        store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        d = make_dispatcher(self, 
            self.dir,
            cfg={"default_project": str(self.dir), "state_path": str(self.dir / "s.json"),
                 "queue_poll_seconds": 0.2},
            runner=lambda tk, on_start=None, on_event=None:
                (self.started.append(tk.id), ("ok", "s"))[1])
        d.pause_until = time.time() + 30
        time.sleep(0.8)
        self.assertEqual(self.started, [])


class HandoverDeadlineTest(unittest.TestCase):
    """WB-66: the claim deadline must survive board restarts. The old timer
    lived in memory only, so every restart granted another full window — an
    unclaimed handover could linger forever ('der nächste startet nicht')."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        dispatch.register_ticket_session(str(self.dir), "chat-abc", self.state)
        self.t = store.create_ticket(self.dir, title="Übergabe", description="",
                                     project=str(self.dir))
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        self.started = []
        force_marker_handover(self)

    def tearDown(self):
        for d in getattr(self, "_dispatchers", []):
            d.stop()
        remove_tree(self.dir)

    def _dispatcher(self):
        d = make_dispatcher(self, 
            self.dir,
            cfg={"state_path": str(self.state), "default_project": str(self.dir),
                 "chat_handover_minutes": 5, "queue_poll_seconds": 0.2},
            runner=lambda t, on_start=None, on_event=None:
                (self.started.append(t.id), ("ok", "s"))[1])
        self._dispatchers = getattr(self, "_dispatchers", []) + [d]
        return d

    def test_handover_records_when_it_happened(self):
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.handover, "chat-abc")
        self.assertTrue(after.handover_at.isdigit(), after.handover_at)

    def test_expired_handover_is_swept_into_a_background_run(self):
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        # pretend the marker was set ten minutes ago and the board restarted
        store.update_ticket(self.dir, self.t.id,
                            {"handover_at": str(int(time.time()) - 600)})
        deadline = time.time() + 5
        while time.time() < deadline and not self.started:
            time.sleep(0.1)
        self.assertEqual(self.started, [self.t.id])
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.handover, "")

    def test_fresh_handover_is_left_alone(self):
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        time.sleep(0.8)                      # several ticker rounds
        self.assertEqual(self.started, [])
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.handover, "chat-abc")


class HandoverGivesUpTest(unittest.TestCase):
    """WB-68: after one missed handover the ticket must go to a background run
    and STAY there. The 'already tried' note lived in memory, so a restart (or
    a fresh dispatch) handed the same ticket to the same silent chat session
    again — the board looked busy while nothing ever ran."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        dispatch.register_ticket_session(str(self.dir), "chat-still", self.state)
        self.t = store.create_ticket(self.dir, title="Nie beansprucht", description="",
                                     project=str(self.dir))
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        self.started = []
        self.dispatchers = []
        force_marker_handover(self)

    def tearDown(self):
        for d in self.dispatchers:
            d.stop()
        remove_tree(self.dir)

    def _dispatcher(self):
        d = make_dispatcher(self, 
            self.dir,
            cfg={"state_path": str(self.state), "default_project": str(self.dir),
                 "chat_handover_minutes": 5, "queue_poll_seconds": 0.2},
            runner=lambda t, on_start=None, on_event=None:
                (self.started.append(t.id), ("ok", "s"))[1])
        self.dispatchers.append(d)
        return d

    def test_expired_handover_is_never_handed_over_again(self):
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        store.update_ticket(self.dir, self.t.id,
                            {"handover_at": str(int(time.time()) - 600)})
        deadline = time.time() + 5
        while time.time() < deadline and not self.started:
            time.sleep(0.1)
        self.assertEqual(self.started, [self.t.id])       # ran in the background
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.handover_expired, "ja")    # remembered in the FILE

    def test_a_restarted_board_does_not_hand_it_over_again(self):
        store.update_ticket(self.dir, self.t.id, {"handover_expired": "ja"})
        fresh = self._dispatcher()                        # simulates a restart
        fresh.dispatch(self.t.id)
        fresh.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.handover, "")              # no new handover
        self.assertEqual(self.started, [self.t.id])       # it just ran


class OrphanAdoptionTest(unittest.TestCase):
    """WB-68b: a ticket sitting in in_arbeit with no handover marker and no
    running agent is invisible-stuck — nobody picks it up until the next board
    restart. The ticker must adopt it; a ticket a chat session is actively
    working (its own session id) must be left alone."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        self.started = []
        self.dispatchers = []

    def tearDown(self):
        for d in self.dispatchers:
            d.stop()
        remove_tree(self.dir)

    def _dispatcher(self):
        d = make_dispatcher(self, 
            self.dir,
            cfg={"state_path": str(self.state), "default_project": str(self.dir),
                 "queue_poll_seconds": 0.2},
            runner=lambda t, on_start=None, on_event=None:
                (self.started.append(t.id), ("ok", "s"))[1])
        self.dispatchers.append(d)
        return d

    def test_stranded_ticket_is_adopted(self):
        t = store.create_ticket(self.dir, title="Gestrandet", description="",
                                project=str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        self._dispatcher()
        deadline = time.time() + 5
        while time.time() < deadline and not self.started:
            time.sleep(0.1)
        self.assertEqual(self.started, [t.id])

    def test_ticket_being_worked_in_a_chat_is_left_alone(self):
        dispatch.register_ticket_session(str(self.dir), "chat-live", self.state)
        t = store.create_ticket(self.dir, title="Im Chat in Arbeit", description="",
                                project=str(self.dir))
        store.update_ticket(self.dir, t.id,
                            {"status": "in_arbeit", "session": "chat-live"})
        self._dispatcher()
        time.sleep(0.8)
        self.assertEqual(self.started, [])


class LimitStaysInProgressTest(unittest.TestCase):
    """WB-69: a quota stop must look like 'still mine, waiting' — the ticket
    stays in in_arbeit (red on the board) and resumes by itself."""

    def setUp(self):
        self.dir = temp_dir()
        self.calls = []
        self.dispatchers = []

    def tearDown(self):
        for d in self.dispatchers:
            d.stop()
        remove_tree(self.dir)

    def _dispatcher(self, runner):
        d = make_dispatcher(self, self.dir,
                                cfg={"default_project": str(self.dir),
                                     "state_path": str(self.dir / "s.json"),
                                     "queue_poll_seconds": 0.2},
                                runner=runner)
        self.dispatchers.append(d)
        return d

    def test_ticket_stays_in_progress_and_resumes_after_the_reset(self):
        reset_at = time.time() + 0.6

        def runner(t, on_start=None, on_event=None):
            self.calls.append(t.id)
            if len(self.calls) == 1:
                raise dispatch.LimitError("Nutzungslimit erreicht", resets_at=reset_at)
            return "danach fertig", "sess-2"

        t = store.create_ticket(self.dir, title="Limit", description="",
                                project=str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        d = self._dispatcher(runner)
        d.dispatch(t.id)
        deadline = time.time() + 2
        while time.time() < deadline and len(self.calls) < 1:
            time.sleep(0.05)
        mid = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(mid.status, "in_arbeit")        # visible, not failed
        self.assertTrue(d.pause_reason())
        deadline = time.time() + 6
        while time.time() < deadline and len(self.calls) < 2:
            time.sleep(0.1)
        d.join(timeout=5)
        self.assertEqual(len(self.calls), 2)             # picked up by itself
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.status, "review")

    def test_nothing_else_starts_while_the_quota_pause_holds(self):
        queued = store.create_ticket(self.dir, title="Wartet", description="",
                                     project=str(self.dir))
        store.update_ticket(self.dir, queued.id, {"status": "zu_bearbeiten"})
        stranded = store.create_ticket(self.dir, title="Gestrandet", description="",
                                       project=str(self.dir))
        store.update_ticket(self.dir, stranded.id, {"status": "in_arbeit"})
        d = self._dispatcher(lambda t, on_start=None, on_event=None:
                             (self.calls.append(t.id), ("ok", "s"))[1])
        d.pause_until = time.time() + 30
        time.sleep(0.8)
        self.assertEqual(self.calls, [])


class LimitStaysInArbeitTest(unittest.TestCase):
    """WB-69: a run stopped by the quota limit must NOT hop into the queue
    (that misled the user with a green-looking waiting card). It stays in
    in_arbeit — the board shows it red — and resumes by itself on reset."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        self.started = []
        self.dispatchers = []

    def tearDown(self):
        for d in self.dispatchers:
            d.stop()
        remove_tree(self.dir)

    def _dispatcher(self, runner):
        d = make_dispatcher(self, 
            self.dir,
            cfg={"state_path": str(self.state), "default_project": str(self.dir),
                 "queue_poll_seconds": 0.2},
            runner=runner)
        self.dispatchers.append(d)
        return d

    def test_limit_keeps_ticket_in_arbeit(self):
        t = store.create_ticket(self.dir, title="Limit", description="",
                                project=str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        reset = time.time() + 3600
        d = self._dispatcher(lambda tk, on_start=None, on_event=None:
                             (_ for _ in ()).throw(dispatch.LimitError("weg", reset)))
        d.dispatch(t.id)
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.status, "in_arbeit")
        self.assertIn("Kontingent", after.body)
        self.assertEqual(after.limit_until, str(int(reset)))

    def test_pause_reason_reflects_the_limit(self):
        t = store.create_ticket(self.dir, title="Grund", description="",
                                project=str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        d = self._dispatcher(lambda tk, on_start=None, on_event=None:
                             (_ for _ in ()).throw(dispatch.LimitError("x", time.time() + 1800)))
        d.dispatch(t.id)
        d.join(timeout=5)
        self.assertIn("Kontingent", d.pause_reason() or "")

    def test_paused_run_is_not_adopted_as_an_orphan(self):
        t = store.create_ticket(self.dir, title="Nicht adoptieren", description="",
                                project=str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        d = self._dispatcher(lambda tk, on_start=None, on_event=None:
                             (self.started.append(tk.id),
                              (_ for _ in ()).throw(dispatch.LimitError("x", time.time() + 1800)))[1])
        d.dispatch(t.id); d.join(timeout=5)
        self.started.clear()
        # Give the ticker several rounds — adopt_orphans must leave it alone.
        time.sleep(0.9)
        self.assertEqual(self.started, [])

    def test_ticket_resumes_after_the_reset(self):
        t = store.create_ticket(self.dir, title="Nach Reset", description="",
                                project=str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        seen = []

        def runner(tk, on_start=None, on_event=None):
            seen.append(tk.id)
            if len(seen) == 1:
                raise dispatch.LimitError("aus", time.time() - 1)   # already reset
            return ("fertig", "sess-x")

        d = self._dispatcher(runner)
        d.dispatch(t.id)
        deadline = time.time() + 5
        while time.time() < deadline and len(seen) < 2:
            time.sleep(0.1)
        d.join(timeout=5)
        self.assertEqual(seen, [t.id, t.id])
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.status, "review")
        self.assertEqual(after.limit_until, "")


class WerkbankPromptTest(unittest.TestCase):
    """WB-70: for tickets targeting the Werkbank itself the prompt must invoke
    the werkbank-work-ticket skill (single source of truth) and keep only the
    non-negotiable safety net inline. For tickets in other projects the prompt
    stays generic — the skill only exists here."""

    def _ticket(self, project):
        return store.Ticket(id="WB-999", title="Testauftrag", type="aufgabe",
                            project=project,
                            body="## Beschreibung\n\nx\n\n## Ergebnis\n\n_(noch offen)_\n")

    def test_werkbank_project_prompt_references_the_skill(self):
        repo = str(Path(__file__).resolve().parent.parent)
        p = dispatch.build_prompt(self._ticket(repo))
        self.assertIn("werkbank-work-ticket", p)
        # Doppelter Boden bleibt drin, damit ein ignorierter Skill nicht still
        # den Rahmen sprengt.
        self.assertIn("erledigt", p.lower())
        self.assertIn("Ticket-Datei", p)
        self.assertIn("nie", p.lower())

    def test_foreign_project_prompt_stays_generic(self):
        p = dispatch.build_prompt(self._ticket("/tmp/anderes-projekt"))
        self.assertNotIn("werkbank-work-ticket", p)
        # Der bisherige Auftragstext bleibt, damit fremde Projekte nichts verlieren.
        self.assertIn("aktuellen Projektverzeichnis", p)

    def test_bug_discipline_travels_via_the_skill_reference(self):
        # Für ein Werkbank-Bug-Ticket steht die volle Disziplin nicht mehr
        # dupliziert im Prompt — der Skill trägt sie. Nur der Verweis muss da sein.
        repo = str(Path(__file__).resolve().parent.parent)
        t = self._ticket(repo); t.type = "bug"
        p = dispatch.build_prompt(t)
        self.assertIn("werkbank-work-ticket", p)
        # Für fremde Projekte bleibt die Disziplin inline (kein Skill dort).
        t2 = self._ticket("/tmp/fremd"); t2.type = "bug"
        p2 = dispatch.build_prompt(t2)
        self.assertIn("Regressionstest", p2)


class RealLimitMessageTest(unittest.TestCase):
    """WB-76: the CLI's actual wording was not recognised as a limit, so three
    tickets were marked 'fehlgeschlagen' instead of pausing until the reset."""

    REAL = "You've hit your limit · resets 6:40am (Europe/Berlin)"

    def test_real_cli_wording_counts_as_a_limit(self):
        cause = dispatch.classify_failure(self.REAL)
        self.assertIsNotNone(cause)
        self.assertIn("Kontingent", cause)

    def test_other_wordings_still_recognised(self):
        for msg in ("Claude AI usage limit reached",
                    "rate limit exceeded",
                    "Your credit balance is too low"):
            self.assertIsNotNone(dispatch.classify_failure(msg), msg)

    def test_unrelated_errors_are_not_swallowed(self):
        self.assertIsNone(dispatch.classify_failure("SyntaxError: invalid syntax"))
        self.assertIsNone(dispatch.classify_failure("no such file or directory"))

    def test_reset_time_is_read_from_the_message(self):
        ts = dispatch.parse_reset_time(self.REAL)
        self.assertIsNotNone(ts)
        import datetime
        when = datetime.datetime.fromtimestamp(ts)
        self.assertEqual((when.hour, when.minute), (6, 40))
        self.assertGreater(ts, time.time())          # always in the future

    def test_no_time_in_message_gives_none(self):
        self.assertIsNone(dispatch.parse_reset_time("You've hit your limit"))


class ClaudeBinaryTest(unittest.TestCase):
    """WB-76b: the board runs as a service; if `claude` is not on that PATH the
    ticket failed instantly with 'Programm nicht gefunden'."""

    def test_falls_back_to_known_locations(self):
        found = dispatch.resolve_claude({}, which=lambda name: None,
                                        candidates=[Path(sys.executable)])
        self.assertEqual(found, str(Path(sys.executable)))

    def test_config_wins(self):
        self.assertEqual(
            dispatch.resolve_claude({"claude_bin": "/eigenes/claude"},
                                    which=lambda name: "/usr/bin/claude"),
            "/eigenes/claude")

    def test_none_when_really_missing(self):
        self.assertIsNone(dispatch.resolve_claude({}, which=lambda name: None,
                                                  candidates=[Path("/gibt/es/nicht")]))


class FinishedRunMustEndTest(unittest.TestCase):
    """WB-77: a run that delivered its result must free the queue AT ONCE.

    Measured on 2026-08-16: a dispatched run finished at 08:46, emitted its
    result event — and did not exit, because the agent had left a background
    shell loop running that inherited the stdout pipe. run_claude reads that
    pipe until EOF, so EOF never came: the board showed 'in Arbeit' for 19
    minutes, the whole queue stood behind it, and at the 30-minute watchdog the
    successful run would have been recorded as FEHLGESCHLAGEN. Reading must
    therefore stop at the result event, and nothing of the run may survive it.
    """

    def setUp(self):
        self.dir = temp_dir()
        self.t = store.Ticket(id="WB-77", title="Ende", status="in_arbeit",
                              project=str(self.dir),
                              body="## Beschreibung\n\nx\n\n## Ergebnis\n\n_(noch offen)_\n")

    def tearDown(self):
        remove_tree(self.dir)

    def _cfg(self, script):
        return {"claude_bin": sh_stub(self.dir, "fake-claude", script),
                "state_path": str(self.dir / "s.json"),
                "agent_timeout_minutes": 1, "exit_grace_seconds": 1}

    RESULT = ('echo \'{"type":"result","subtype":"success","result":"fertig",'
              '"session_id":"s-1"}\'\n')

    @posix_only
    def test_background_job_holding_stdout_does_not_stall_the_run(self):
        """The exact production shape: agent leaves a watcher running, the
        run itself also stays alive. Without the fix this blocks until the
        watchdog and is then reported as a failure."""
        child = self.dir / "child.pid"
        cfg = self._cfg(f"sleep 60 & echo $! > {child}\n" + self.RESULT + "sleep 60\n")
        began = time.time()
        result, session = dispatch.run_claude(self.t, cfg)
        took = time.time() - began
        self.assertEqual((result, session), ("fertig", "s-1"))
        self.assertLess(took, 20, "run_claude waited for a run that was done")
        pid = int(child.read_text(encoding="utf-8").strip())
        deadline = time.time() + 3
        while time.time() < deadline and dispatch._is_running(pid):
            time.sleep(0.05)
        self.assertFalse(dispatch._is_running(pid),
                         "the leftover background job of the run survived")

    def test_run_that_exits_by_itself_is_still_a_normal_success(self):
        """The fix must not turn well-behaved runs into forced kills."""
        cfg = self._cfg(self.RESULT)
        self.assertEqual(dispatch.run_claude(self.t, cfg), ("fertig", "s-1"))

    def test_failure_after_the_result_event_still_counts_as_failure(self):
        """Stopping at the result event must not swallow an error result."""
        cfg = self._cfg('echo \'{"type":"result","subtype":"error_during_execution",'
                        '"result":"kaputt","session_id":"s-2"}\'\nsleep 60\n')
        with self.assertRaises(dispatch.DispatchError):
            dispatch.run_claude(self.t, cfg)


class Wb92LaneTest(unittest.TestCase):
    """WB-92: one slot PER ASSORTMENT (claude / opencode), not one global slot.
    An opencode ticket must start while a Claude run is active; within one
    assortment it stays one run at a time."""

    def setUp(self):
        from types import SimpleNamespace
        self.SimpleNamespace = SimpleNamespace
        self.dir = temp_dir()
        (self.dir / "projekt-a").mkdir()
        (self.dir / "projekt-b").mkdir()
        self.proj_a = str(self.dir / "projekt-a")
        self.proj_b = str(self.dir / "projekt-b")
        self.state = self.dir / "state.json"
        self.claude_started = []
        self.claude_release = threading.Event()

    def tearDown(self):
        self.claude_release.set()
        d = getattr(self, "d", None)
        if d is not None:
            d.stop()           # a live ticker recreates .lock mid-rmtree
            d.join(timeout=5)
        deadline = time.monotonic() + 3
        while True:
            try:
                remove_tree(self.dir)
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def _dispatcher(self):
        def runner(t, on_start=None, **kw):
            self.claude_started.append(t.id)
            self.claude_release.wait(timeout=10)   # hold the claude lane
            return "fertig", "sess-x"
        cfg = {"state_path": str(self.state), "default_project": self.proj_a,
               "nonblocking_review": {self.proj_a: True, self.proj_b: True}}
        self.d = make_dispatcher(self, self.dir, cfg=cfg, runner=runner)
        return self.d

    def _queued(self, title, project, assignee="claude"):
        t = store.create_ticket(self.dir, title=title, description="",
                                project=project, assignee=assignee,
                                gate="Tests laufen durch" if assignee == "opencode" else "")
        store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        return t

    def _status(self, tid):
        return {x.id: x for x in store.load_tickets(self.dir)}[tid].status

    def _fake_opencode(self, started, release=None):
        def work_ticket(t, cfg, on_progress=None, **_):
            started.append(t.id)
            if release is not None:
                release.wait(timeout=10)          # hold the opencode lane
            return self.SimpleNamespace(result="ok (Fake)", status="review",
                                        changes={})
        return work_ticket

    def test_opencode_starts_while_claude_runs(self):
        from unittest import mock
        c = self._queued("Claude-Arbeit", self.proj_a)
        o = self._queued("Lokal-Arbeit", self.proj_b, assignee="opencode")
        oc_started = []
        d = self._dispatcher()
        with mock.patch.object(dispatch.opencode, "work_ticket",
                               self._fake_opencode(oc_started)):
            d.pump_queue()
            wait_until(lambda: self.claude_started == [c.id])
            d.pump_queue()                        # claude lane busy — opencode must go anyway
            wait_until(lambda: oc_started == [o.id])
        self.assertEqual(oc_started, [o.id])
        self.assertEqual(self._status(c.id), "in_arbeit")   # claude still holding its lane
        self.claude_release.set()
        d.join(timeout=5)

    @posix_only
    def test_wb146_claude_config_dir_per_project_isolates_and_shares_creds(self):
        """WB-146: helper creates a per-project subdir under the config root,
        symlinks the user's real credentials in once (not copies), and two
        different projects land in DIFFERENT dirs."""
        import tempfile as _tempfile, pathlib
        # Fake the user's credentials source by overriding HOME.
        root = pathlib.Path(str(temp_dir()))
        fake_home = root / "home"
        (fake_home / ".claude").mkdir(parents=True)
        (fake_home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
        cfg = {"claude_config_root": str(root / "cfg")}
        import os as _os
        old_home = _os.environ.get("HOME")
        _os.environ["HOME"] = str(fake_home)
        try:
            a = dispatch.claude_config_dir_for("/proj/a", cfg)
            b = dispatch.claude_config_dir_for("/proj/b", cfg)
            self.assertNotEqual(a, b)
            self.assertTrue((a / ".credentials.json").is_symlink())
            self.assertTrue((b / ".credentials.json").is_symlink())
            # Root must be private (0700). Peers on the machine must not read a
            # foreign user's tokens via the werkbank-local dir tree.
            self.assertEqual(_os.stat(root / "cfg").st_mode & 0o777, 0o700)
        finally:
            if old_home is None: del _os.environ["HOME"]
            else: _os.environ["HOME"] = old_home
            import shutil as _sh; _sh.rmtree(root, ignore_errors=True)

    def test_wb146_two_claude_projects_run_in_parallel(self):
        """WB-146: each project owns its CLAUDE_CONFIG_DIR, so a running
        claude ticket in project A no longer blocks a claude ticket in
        project B. The old 'one claude at a time GLOBALLY' rule is gone."""
        first = self._queued("Claude A", self.proj_a)
        second = self._queued("Claude B", self.proj_b)
        d = self._dispatcher()
        d.pump_queue()
        wait_until(lambda: sorted(self.claude_started) == sorted([first.id, second.id]))
        self.assertEqual(sorted(self.claude_started), sorted([first.id, second.id]))
        self.assertEqual(self._status(first.id), "in_arbeit")
        self.assertEqual(self._status(second.id), "in_arbeit")
        self.claude_release.set()
        d.join(timeout=5)

    def test_wb146_no_second_claude_in_the_SAME_project(self):
        """The one-run-per-project rule stays — same project still shares
        files and the ticket-in-arbeit guard still applies. Only the
        cross-project block goes away."""
        first = self._queued("Erster Claude", self.proj_a)
        second = self._queued("Zweiter Claude", self.proj_a)   # SAME project
        d = self._dispatcher()
        d.pump_queue()
        wait_until(lambda: self.claude_started == [first.id])
        d.pump_queue()
        time.sleep(0.3)                           # give a wrong start the chance to happen
        self.assertEqual(self.claude_started, [first.id])
        self.assertEqual(self._status(second.id), "zu_bearbeiten")
        self.claude_release.set()
        d.join(timeout=5)

    def test_board_names_the_lane_wait_reason(self):
        """WB-92 acceptance 4: the card must say WHY it waits (own lane busy),
        and only fall back to 'startet gleich' when nothing is in the way.
        Pins the board.html shape the way the swipe tests do — a rename or
        rewrite of queueWaitReason must consciously touch this test."""
        board = (Path(__file__).resolve().parent.parent
                 / "src/werkbank/board.html").read_text(encoding="utf-8")
        # WB-219: the lane is no longer "opencode" but "the local model" —
        # opencode and dsh share one slot because they share one GPU.
        for needle in (
            'const own = isLocalLane(t.assignee) ? "lokal" : "claude"',
            'isLocalLane(r.model)',           # claude runs carry no model field
            '"wartet, bis der laufende Lauf des lokalen Modells fertig ist"',
            # WB-146 removed the global claude lane block; the same-project
            # fallback below (also present in the same function) covers claude.
            '"wartet, bis das laufende Ticket fertig ist"',
        ):
            self.assertIn(needle, board, f"lane wait reason lost: {needle}")
        # The local-lane check must sit before the optimistic fallback
        # (the RETURNED string "startet gleich …", not the comments that
        # also mention it).
        self.assertLess(board.index('"wartet, bis der laufende Lauf des lokalen '
                                    'Modells fertig ist"'),
                        board.index('return "startet gleich'),
                        "lane check no longer precedes the 'startet gleich' fallback")

    def test_no_second_opencode_while_opencode_runs(self):
        from unittest import mock
        first = self._queued("Lokal eins", self.proj_a, assignee="opencode")
        second = self._queued("Lokal zwei", self.proj_b, assignee="opencode")
        oc_started, oc_release = [], threading.Event()
        d = self._dispatcher()
        try:
            with mock.patch.object(dispatch.opencode, "work_ticket",
                                   self._fake_opencode(oc_started, oc_release)):
                d.pump_queue()
                wait_until(lambda: oc_started == [first.id])
                d.pump_queue()
                time.sleep(0.3)
                self.assertEqual(oc_started, [first.id])
                self.assertEqual(self._status(second.id), "zu_bearbeiten")
                oc_release.set()
                d.join(timeout=5)
        finally:
            oc_release.set()


class Wb105AssigneeGateTest(unittest.TestCase):
    """WB-105: only tickets whose assignee maps to a known lane (claude,
    opencode) may start automatically. A ticket assigned to a human must not
    spawn a Bash-enabled agent on its body — the drag path refuses this, but
    pump_queue and adopt_orphans did not."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        self.started = []

    def tearDown(self):
        d = getattr(self, "d", None)
        if d is not None:
            d.stop()
            d.join(timeout=5)
        remove_tree(self.dir)

    def _dispatcher(self):
        def runner(t, on_start=None, **kw):
            self.started.append(t.id)
            return "fertig", "sess-x"
        cfg = {"state_path": str(self.state), "default_project": str(self.dir),
               "nonblocking_review": {str(self.dir): True}}
        self.d = make_dispatcher(self, self.dir, cfg=cfg, runner=runner)
        return self.d

    def test_pump_skips_human_assignee_and_names_the_reason(self):
        t = store.create_ticket(self.dir, title="Für eine Person", description="",
                                assignee="mensch")
        store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        d = self._dispatcher()
        d.pump_queue()
        d.join(timeout=5)
        self.assertEqual(self.started, [])
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.status, "zu_bearbeiten")   # stays queued, visibly
        reason = d._queue_blocked_reason(store.load_tickets(self.dir), after)
        self.assertIn("mensch", reason)                    # the card can say why

    def test_pump_still_starts_claude_tickets(self):
        t = store.create_ticket(self.dir, title="Normal", description="")
        store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        d = self._dispatcher()
        d.pump_queue()
        d.join(timeout=5)
        wait_until(lambda: self.started == [t.id])
        self.assertEqual(self.started, [t.id])

    def test_adopt_orphans_leaves_human_tickets_alone(self):
        t = store.create_ticket(self.dir, title="Mensch arbeitet", description="",
                                assignee="mensch")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        d = self._dispatcher()
        d.adopt_orphans()
        d.join(timeout=5)
        self.assertEqual(self.started, [])
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.status, "in_arbeit")       # not swept, not run


class PauseUntilRaceTest(unittest.TestCase):
    """WB-109 P1: pause_until is written from the worker (LimitError) and from
    adopt_orphans (ticker), read from many places. `pause_until = max(pause_until,
    x)` is a read-modify-write race — under contention two concurrent bumps can
    lose one. The lock-guarded _bump_pause_until must never lose."""

    def _dispatcher(self):
        d = temp_dir()
        self.addCleanup(shutil.rmtree, d)
        return make_dispatcher(self, d, cfg={}, runner=lambda t, **kw: ("ok", "s"))

    def test_bump_never_lowers(self):
        d = self._dispatcher()
        d._bump_pause_until(500.0)
        d._bump_pause_until(200.0)   # lower value must not win
        self.assertEqual(d._get_pause_until(), 500.0)

    def test_concurrent_bumps_all_survive(self):
        """The naive `pause_until = max(pause_until, x)` loses updates when two
        threads read the same old value before either writes back. This test
        would go RED against that pattern; the lock-guarded bump keeps the max
        of ALL contenders."""
        import threading as th
        d = self._dispatcher()
        values = [1_000_000_000 + i for i in range(200)]
        barrier = th.Barrier(len(values))
        def bump(v):
            barrier.wait()
            d._bump_pause_until(float(v))
        threads = [th.Thread(target=bump, args=(v,)) for v in values]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(d._get_pause_until(), float(max(values)))

    def test_pause_reason_snapshots_the_value(self):
        """A second reader must not see a torn read: pause_reason takes ONE
        snapshot for both the threshold check and the formatted time. Proven
        indirectly by hitting it while a bumper thread races beside it."""
        import threading as th
        d = self._dispatcher()
        future = time.time() + 3600
        stop = th.Event()
        def churn():
            v = future
            while not stop.is_set():
                v += 1
                d._bump_pause_until(v)
        t = th.Thread(target=churn); t.start()
        try:
            for _ in range(2000):
                reason = d.pause_reason()
                # Either resting with a well-formed sentence or None — never
                # a partial exception from datetime seeing a torn value.
                if reason is not None:
                    self.assertIn("Warteschlange", reason)
        finally:
            stop.set(); t.join()


class BoardVersionOnStatusMoveTest(unittest.TestCase):
    """WB-109 P2: every status-changing POST from board.html must send the
    ticket's `version` so the WB-9 stale-write guard fires. Without this a
    drag/swipe/quick-button can silently overwrite an update the agent made
    a moment ago."""

    def test_every_status_post_carries_version(self):
        import re
        board = (Path(__file__).resolve().parent.parent
                 / "src/werkbank/board.html").read_text(encoding="utf-8")
        # Every call to api("/api/tickets/…") that includes `status:` must
        # also include `version:` in the same argument object. Regex matches
        # a compact one-line object literal — the format the file uses.
        posts = re.findall(
            r'api\("/api/tickets/"\s*\+\s*[^,]+,\s*\{([^}]*status:[^}]*)\}',
            board)
        self.assertGreaterEqual(len(posts), 5,
                                f"only found {len(posts)} status POST(s) — "
                                "regex drifted, please update this test")
        without_version = [p.strip() for p in posts if "version:" not in p]
        self.assertEqual(without_version, [],
                         f"status POSTs without version: {without_version}")


class Wb123RueckfrageTest(unittest.TestCase):
    """WB-123: an agent that needs a decision beginns its final message with
    'RÜCKFRAGE AN DEN NUTZER:'. The dispatcher parks the ticket in the
    rueckfrage status (lane free immediately), and when the user answers,
    the ticket resumes the SAME session with the answer as prompt."""

    def setUp(self):
        self.dir = temp_dir()
        (self.dir / "projA").mkdir()
        self.proj = str(self.dir / "projA")
        self.state = self.dir / "state.json"

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self, runner):
        cfg = {"state_path": str(self.state), "default_project": self.proj,
               "nonblocking_review": {self.proj: True}}
        return make_dispatcher(self, self.dir, cfg=cfg, runner=runner)

    def _queued(self, title):
        t = store.create_ticket(self.dir, title=title, description="",
                                project=self.proj)
        store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        return t

    # --- P2: marker detection ---
    def test_marker_landing_flips_status_to_rueckfrage(self):
        # A run whose final message begins with the marker parks the ticket
        # in rueckfrage — not review — and the session id is preserved so
        # the answer endpoint can resume it.
        started = []
        def runner(t, **kw):
            started.append(t.id)
            return ("RÜCKFRAGE AN DEN NUTZER:\nWelche Datei?", "sess-xyz")
        t = self._queued("Frag mich")
        d = self._dispatcher(runner)
        d.pump_queue()
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.status, "rueckfrage")
        self.assertEqual(after.session, "sess-xyz")
        # Prose that MENTIONS the marker later in a paragraph must NOT trigger.
        self.assertTrue(dispatch.is_query_result("RÜCKFRAGE AN DEN NUTZER: X"))
        self.assertFalse(dispatch.is_query_result("Erledigt.\nRÜCKFRAGE ..."))

    def test_lane_is_free_immediately_after_rueckfrage(self):
        # WB-92 says one Claude run per lane; after a rueckfrage the pending
        # set must be empty so another Claude ticket can start right away.
        def runner(t, **kw):
            return ("RÜCKFRAGE AN DEN NUTZER:\nBraucht Klärung", "sess-1")
        t = self._queued("Erst rueckfrage")
        d = self._dispatcher(runner)
        d.pump_queue()
        d.join(timeout=5)
        with d._lock:
            self.assertEqual(d._pending["claude"], set(),
                             "rueckfrage still counted as busy — lane blocked")

    def test_queued_sibling_starts_while_first_is_in_rueckfrage(self):
        # P4 acceptance: a rueckfrage ticket does NOT block a queued ticket
        # in the same project.
        outcomes = {}
        started = []
        def runner(t, **kw):
            started.append(t.id)
            r = outcomes.get(t.id, ("fertig", "s"))
            return r
        first = self._queued("Erst rueckfrage")
        second = self._queued("Danach normal")
        outcomes[first.id] = ("RÜCKFRAGE AN DEN NUTZER:\nWas?", "sess-1")
        outcomes[second.id] = ("Erledigt.", "sess-2")
        d = self._dispatcher(runner)
        d.pump_queue()
        wait_until(lambda: started == [first.id])
        d.pump_queue()
        wait_until(lambda: started == [first.id, second.id])
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(after[first.id].status, "rueckfrage")
        self.assertEqual(after[second.id].status, "review")

    # --- P3: resume command shape ---
    def test_build_command_answer_mode_uses_resume_and_answer_prompt(self):
        # The command must carry --resume <session>, an answer prompt built
        # from t.answer, and NEVER --fork-session (would branch off before
        # the question, losing the exchange).
        t = store.Ticket(id="WB-9", title="X", session="sess-abc",
                         answer="Nimm Option A.", fork="ja")   # even fork=ja
        cmd = dispatch.build_command("claude", t, "answer", cfg={},
                                     resume_id="sess-abc",
                                     prompt=dispatch.build_answer_prompt(t))
        self.assertIn("--resume", cmd)
        self.assertEqual(cmd[cmd.index("--resume") + 1], "sess-abc")
        self.assertNotIn("--fork-session", cmd)
        self.assertIn("Nimm Option A.", cmd[-1])
        self.assertIn("WB-9", cmd[-1])

    def test_answered_ticket_dispatches_via_answer_path(self):
        # The runner receives a ticket carrying `answer`; when a chat POST
        # to /answer sets in_arbeit and dispatches, the runner sees the
        # answer text on the ticket and can build the resume prompt from it.
        # We use a stub runner and assert on what it was HANDED.
        seen = {}
        def runner(t, **kw):
            seen["answer"] = t.answer
            seen["session"] = t.session
            return ("Nach der Antwort erledigt.", t.session or "s")
        t = self._queued("Wartet auf Antwort")
        # Simulate the prior rueckfrage: session recorded, status rueckfrage
        store.update_ticket(self.dir, t.id, {"session": "sess-y", "status": "rueckfrage"})
        # The endpoint's job: set answer + in_arbeit + dispatch.
        store.update_ticket(self.dir, t.id,
                            {"answer": "Nimm B", "status": "in_arbeit"})
        d = self._dispatcher(runner)
        d.dispatch(t.id)
        d.join(timeout=5)
        self.assertEqual(seen.get("answer"), "Nimm B")
        self.assertEqual(seen.get("session"), "sess-y")
        # Answer must be cleared after a successful consumption so a retry
        # does not re-send it.
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.answer, "")
        self.assertEqual(after.status, "review")

    def test_failed_answer_run_keeps_the_answer_for_retry(self):
        # If the answered run technically fails, KEEPING the answer lets the
        # user hit "Erneut versuchen" without retyping it. Only clear on
        # success or rueckfrage.
        def runner(t, **kw):
            raise dispatch.DispatchError("kaputt")
        t = self._queued("Wartet")
        store.update_ticket(self.dir, t.id,
                            {"session": "sess-z", "status": "rueckfrage"})
        store.update_ticket(self.dir, t.id,
                            {"answer": "immer noch B", "status": "in_arbeit"})
        d = self._dispatcher(runner)
        d.dispatch(t.id)
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.status, "fehlgeschlagen")
        self.assertEqual(after.answer, "immer noch B")   # kept for retry


class Wb123AnswerEndpointTest(unittest.TestCase):
    """WB-123: /api/tickets/<id>/answer path, exercised via the pure
    dispatch.answer_ticket function so no HTTP server is needed."""

    def setUp(self):
        self.dir = temp_dir()
        self.proj = str(self.dir)
        self.dispatched = []
        self.dispatcher_stub = type("Stub", (), {
            "dispatch": lambda s, tid: self.dispatched.append(tid) or True})()

    def tearDown(self):
        remove_tree(self.dir)

    def _rueckfrage_ticket(self):
        t = store.create_ticket(self.dir, title="X", description="",
                                project=self.proj)
        # Mimic what the dispatcher writes on marker landing.
        store.update_ticket(self.dir, t.id,
                            {"status": "rueckfrage", "session": "sess-1"})
        return {x.id: x for x in store.load_tickets(self.dir)}[t.id]

    def test_valid_answer_flips_status_and_dispatches(self):
        t = self._rueckfrage_ticket()
        code, payload = dispatch.answer_ticket(
            self.dir, t.id, {"answer": "Nimm A", "version": t.version},
            self.dispatcher_stub)
        self.assertEqual(code, 200)
        self.assertEqual(payload["status"], "in_arbeit")
        self.assertEqual(payload["answer"], "Nimm A")
        self.assertEqual(self.dispatched, [t.id])

    def test_empty_answer_is_rejected_and_nothing_moves(self):
        t = self._rueckfrage_ticket()
        code, payload = dispatch.answer_ticket(
            self.dir, t.id, {"answer": "   ", "version": t.version},
            self.dispatcher_stub)
        self.assertEqual(code, 400)
        self.assertIn("leer", payload["error"])
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.status, "rueckfrage")
        self.assertEqual(self.dispatched, [])

    def test_answer_to_ticket_not_in_rueckfrage_is_refused(self):
        # A stale browser tab must not resurrect a done ticket by POSTing
        # to /answer.
        t = self._rueckfrage_ticket()
        store.update_ticket(self.dir, t.id, {"status": "review"})
        current = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        code, payload = dispatch.answer_ticket(
            self.dir, t.id, {"answer": "spät dran", "version": current.version},
            self.dispatcher_stub)
        self.assertEqual(code, 409)
        self.assertIn("Antwort", payload["error"])
        self.assertEqual(self.dispatched, [])

    def test_stale_version_gets_a_conflict(self):
        # WB-9 lost-update guard: an answer posted from a card that has since
        # been updated (agent bumped the ticket) must be rejected.
        t = self._rueckfrage_ticket()
        stale_version = t.version
        # Simulate a concurrent bump.
        store.update_ticket(self.dir, t.id, {"handover_at": "0"})
        code, payload = dispatch.answer_ticket(
            self.dir, t.id, {"answer": "OK", "version": stale_version},
            self.dispatcher_stub)
        self.assertEqual(code, 409)

    def test_multiline_answer_is_folded_to_one_line(self):
        # WB-35 F4 keeps the frontmatter safe: newlines in a field would
        # forge a second frontmatter line. Fold instead of refusing so a
        # user pasting a short note is not turned away.
        t = self._rueckfrage_ticket()
        code, payload = dispatch.answer_ticket(
            self.dir, t.id,
            {"answer": "erste Zeile\nzweite Zeile", "version": t.version},
            self.dispatcher_stub)
        self.assertEqual(code, 200)
        self.assertEqual(payload["answer"], "erste Zeile zweite Zeile")
        self.assertNotIn("\n", payload["answer"])


class Wb123BoardShapeTest(unittest.TestCase):
    """WB-123: the answer form is one grep away in board.html. Pin the exact
    call the frontend makes so a refactor cannot silently drop the version
    guard or wire the button to the wrong endpoint."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_rueckfrage_column_and_status_class_present(self):
        self.assertIn('["rueckfrage", "Rückfrage"]', self.board)
        # 7-column desktop grid — a stale 6 would push a column off-screen.
        self.assertIn("grid-template-columns: repeat(7, 1fr)", self.board)
        self.assertIn('.col[data-status="rueckfrage"]', self.board)
        self.assertIn(".card.rueckfrage", self.board)

    def test_answer_form_posts_to_answer_endpoint_with_version(self):
        # The one call that must NOT drift: /answer + version, exactly.
        m = re.search(r'api\("/api/tickets/"\s*\+\s*t\.id\s*\+\s*"/answer",\s*\{([^}]*)\}',
                      self.board)
        self.assertIsNotNone(m, "answer POST call missing or reshaped")
        args = m.group(1)
        self.assertIn("answer", args)
        self.assertIn("version: t.version", args)

    def test_empty_answer_is_refused_before_posting(self):
        # The button must fail closed: an empty textarea shows the German
        # error and does NOT hit the endpoint. Regex targets the button
        # click handler right before the api() call.
        self.assertRegex(self.board,
                         r'if\s*\(\s*!\s*answer\s*\)\s*\{[^}]*Antwort[^}]*return')

    def test_extract_question_reads_the_marker_body(self):
        self.assertIn('const RUECKFRAGE_MARKER = "RÜCKFRAGE AN DEN NUTZER:"',
                      self.board)
        self.assertIn("function extractQuestion", self.board)


class Wb161EpicTypeTest(unittest.TestCase):
    """WB-161: an epic is a planning ticket. Its type is accepted, its
    children carry `epic: WB-N`, and the planning prompt tells the agent
    to write child tickets instead of coding."""

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_create_ticket_accepts_type_epic(self):
        t = store.create_ticket(self.dir, title="Ein Epic", description="Ziel",
                                type="epic")
        self.assertEqual(t.type, "epic")
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.type, "epic")

    def test_epic_field_roundtrips_on_child_ticket(self):
        parent = store.create_ticket(self.dir, title="Epic", description="",
                                     type="epic")
        child = store.create_ticket(self.dir, title="Kind", description="",
                                    epic=parent.id)
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[child.id]
        self.assertEqual(loaded.epic, parent.id)
        # And update_ticket accepts it too — the planner can retro-attach.
        loose = store.create_ticket(self.dir, title="loose", description="")
        store.update_ticket(self.dir, loose.id, {"epic": parent.id})
        self.assertEqual({x.id: x for x in store.load_tickets(self.dir)
                          }[loose.id].epic, parent.id)

    def test_build_prompt_for_epic_names_the_planning_flow(self):
        # A werkbank-local epic: the skill line is present, PLUS the
        # planning block that tells the agent this is planning, not coding.
        t = store.create_ticket(self.dir, title="Plan me",
                                description="grober Zweck",
                                project=dispatch.WERKBANK_ROOT, type="epic")
        p = dispatch.build_prompt(t)
        self.assertIn("Werkbank-Epic", p)
        self.assertIn("plane das Paket", p)
        self.assertIn("Kind-Tickets", p)
        self.assertIn("epic:", p)  # the frontmatter key children must carry


class Wb161EpicDispatchTest(unittest.TestCase):
    """WB-161: an epic dispatched to a project WITHOUT a live chat session
    bounces back to Offen with instructions; WITH a chat session it takes
    the same WB-22 handover path as any other ticket, no background run."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        self.t = store.create_ticket(self.dir, title="Plan me",
                                     description="grober Zweck", type="epic",
                                     project=str(self.dir))
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        self.calls = []

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self):
        def runner(t, on_start=None):
            self.calls.append(t.id)
            return "hintergrund", "sess-bg"
        return make_dispatcher(self,
            self.dir, cfg={"state_path": str(self.state),
                           "default_project": str(self.dir),
                           "chat_handover_minutes": 10},
            runner=runner)

    def _load(self):
        return {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]

    def test_epic_without_chat_session_bounces_back_to_offen(self):
        # No register_ticket_session call → no interactive lineage.
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [], "background runner must not fire")
        after = self._load()
        self.assertEqual(after.status, "offen")
        self.assertIn("Epic wartet auf eine Chat-Session", after.body)
        self.assertIn("zieh dir dein Ticket", after.body)

    def test_epic_with_chat_session_uses_wb22_handover(self):
        force_marker_handover(self)
        dispatch.register_ticket_session(str(self.dir), "chat-epic",
                                         self.state)
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [], "background runner must not fire")
        after = self._load()
        self.assertEqual(after.status, "in_arbeit")
        self.assertEqual(after.handover, "chat-epic")


class Wb161EpicBoardShapeTest(unittest.TestCase):
    """WB-161: pin the epic UI shape — a rename or removal must consciously
    touch this test."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_create_and_detail_dialogs_offer_epic(self):
        # Two <option value="epic"> — one in the create dialog, one in detail.
        self.assertEqual(self.board.count('<option value="epic">Epic</option>'),
                         2)

    def test_epic_badge_and_waiting_state_are_wired(self):
        self.assertIn('.epic-badge', self.board)
        self.assertIn('.card.epic', self.board)
        self.assertIn('.card.epic.waiting', self.board)
        self.assertIn('"EPIC"', self.board)
        # The waiting-state trigger matches what dispatch writes into the
        # ticket body when it bounces an epic back — keep both in sync.
        self.assertIn('Epic wartet auf eine Chat-Session', self.board)

    def test_children_render_uses_epic_field(self):
        self.assertIn('x.epic === t.id', self.board)


class Wb170ReviewerCostTest(unittest.TestCase):
    """WB-170: `review_command` now asks the CLI for `--output-format json`;
    `adversarial_review` / `review_diff` parse the result event and return
    a usage dict; `append_review_note` writes the per-run footer AND
    cumulates the ticket's `review_cost_usd` frontmatter field."""

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def _fake_json(self, text="verdict text", cost=0.15,
                   tokens_in=10, tokens_out=20, cache_read=100, cache_create=50):
        return json.dumps({
            "type": "result", "subtype": "success",
            "result": text, "total_cost_usd": cost,
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out,
                      "cache_read_input_tokens": cache_read,
                      "cache_creation_input_tokens": cache_create},
        })

    def test_review_command_asks_for_json_output(self):
        from werkbank import opencode
        cmd = opencode.review_command()
        self.assertIn("--output-format", cmd)
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "json")

    def test_parse_review_output_extracts_text_and_usage(self):
        from werkbank import opencode
        text, usage = opencode._parse_review_output(
            self._fake_json(text="Ich bin die Antwort", cost=0.1234,
                            tokens_in=7, tokens_out=13,
                            cache_read=1000, cache_create=200))
        self.assertEqual(text, "Ich bin die Antwort")
        self.assertAlmostEqual(usage["cost_usd"], 0.1234)
        self.assertEqual(usage["tokens_in"], 7)
        self.assertEqual(usage["tokens_out"], 13)
        self.assertEqual(usage["tokens_cache"], 1200)

    def test_parse_review_output_falls_back_on_non_json(self):
        from werkbank import opencode
        text, usage = opencode._parse_review_output("plaintext, kein JSON")
        self.assertEqual(text, "plaintext, kein JSON")
        self.assertIsNone(usage)

    def test_adversarial_review_returns_text_truncated_usage(self):
        from types import SimpleNamespace
        from werkbank import opencode
        def fake_run(cmd, cwd=None, input=None, capture_output=True,
                     text=True, timeout=None):
            return SimpleNamespace(
                stdout=self._fake_json(text="alles cool", cost=0.05),
                stderr="", returncode=0)
        text, truncated, usage = opencode.adversarial_review(
            "body", "diff --git a/x b/x\n+neu", run=fake_run)
        self.assertEqual(text, "alles cool")
        self.assertFalse(truncated)
        self.assertAlmostEqual(usage["cost_usd"], 0.05)

    def test_append_review_note_cumulates_cost_and_writes_footer(self):
        t = store.create_ticket(self.dir, title="X", description="Y")
        store.append_review_note(self.dir, t.id, "erster Report",
                                 usage={"cost_usd": 0.10,
                                        "tokens_in": 5, "tokens_out": 15,
                                        "tokens_cache": 100})
        first = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(first.review_cost_usd, "0.1000")
        self.assertIn("💰 $0.1000", first.body)
        self.assertIn("5 in / 15 out / 100 cache", first.body)
        # Second click adds up.
        store.append_review_note(self.dir, t.id, "zweiter Report",
                                 usage={"cost_usd": 0.25,
                                        "tokens_in": 1, "tokens_out": 2,
                                        "tokens_cache": 0})
        second = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(second.review_cost_usd, "0.3500")
        self.assertEqual(second.body.count("## Review-Bot"), 2)

    def test_append_review_note_without_usage_stays_unchanged(self):
        # Backwards path: a non-JSON review still lands as a section, but
        # nothing about cost is written (no footer, no field update).
        t = store.create_ticket(self.dir, title="X", description="Y")
        store.append_review_note(self.dir, t.id, "kein JSON, kein Preis")
        fresh = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(fresh.review_cost_usd, "")
        self.assertNotIn("💰", fresh.body)
        self.assertIn("## Review-Bot", fresh.body)


class Wb176BesprechenButtonTest(unittest.TestCase):
    """WB-176: a one-click button on Offen cards that turns the ticket into
    an interactive chat handover — sets `interactive: ja` AND
    `status: in_arbeit` in a single PATCH so the server's WB-22 branch (or
    WB-168's bounce, when no chat is registered) does the rest. Board is
    exercised as a shape pin here; the runtime path is already covered by
    Wb168InteractiveOptInTest."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_button_is_only_shown_on_open_unblocked_claude_tickets(self):
        # The three predicates must ALL be present in the render guard —
        # a rename or a removed check would silently expose the button
        # for tickets it does not fit.
        self.assertRegex(self.board,
                         r't\.status\s*===\s*"offen"[^{}]*t\.assignee[^{}]*claude'
                         r'[^{}]*!\s*info\.blocked')

    def test_button_sends_status_and_interactive_together(self):
        # The PATCH must carry BOTH fields — status alone leaves the
        # ticket a normal background run; interactive alone doesn't
        # dispatch anything. Both together are the load-bearing pair.
        self.assertRegex(
            self.board,
            r'api\(\s*"/api/tickets/"\s*\+\s*t\.id\s*,\s*\{\s*status:\s*"in_arbeit"'
            r',\s*interactive:\s*"ja",\s*version:\s*t\.version\s*\}')

    def test_button_label_says_besprechen(self):
        self.assertIn('🗨️ Besprechen', self.board)


class Wb172AdversarialPromptCeilingTest(unittest.TestCase):
    """WB-172: the adversarial-reviewer prompt now names a hard 200-word
    ceiling, a `- file:line — scenario` line format, and forbids the
    preamble / trailing summary / description-restatement that drove
    the WB-169 audit average up to 21.6k output tokens per click."""

    def test_prompt_names_word_ceiling_and_format(self):
        from werkbank import opencode
        p = opencode.adversarial_review_prompt("Ticket-Text",
                                                "diff --git a/x b/x\n+neu")
        self.assertIn("≤200 Wörtern", p)
        # Findings format: one line per finding, "- file:line — scenario".
        self.assertIn("`- <file>:<line> — <konkretes Fehlerszenario", p)
        # The three "don't do this" rules that expand output the most.
        self.assertIn("Keine Präambel", p)
        self.assertIn("keine Schluss-Zusammenfassung", p)
        self.assertIn("keine Wiederholung", p)
        # The "nothing to complain about" escape hatch stays: one sentence.
        self.assertRegex(p, r"nichts zu meckern[^.]*ein Satz")

    def test_prompt_still_embeds_ticket_and_diff(self):
        # The compaction rules must not have stripped the actual context
        # the reviewer is supposed to reason over.
        from werkbank import opencode
        p = opencode.adversarial_review_prompt("TICKET-XYZ", "DIFF-XYZ")
        self.assertIn("Ticket:\nTICKET-XYZ", p)
        self.assertIn("Diff:\nDIFF-XYZ", p)


class Wb171ReviewerProcessGroupTest(unittest.TestCase):
    """WB-171: the reviewer's default runner is `_run_grouped`, and a
    timeout does not leave the per-ticket `_REVIEWS_RUNNING` lock held
    forever. Both were plain `subprocess.run` before, which meant a
    grandchild of the `claude -p` process would hold stdout open past
    the REVIEW_TIMEOUT and the ticket could not be reviewed again
    until the server was restarted (WB-92 shape, WB-169 audit)."""

    def test_review_diff_defaults_to_run_grouped(self):
        from unittest import mock
        from types import SimpleNamespace
        from werkbank import opencode
        seen = []
        def spy(*a, **kw):
            seen.append((a, kw))
            return SimpleNamespace(stdout='{"result":"ok"}', stderr="",
                                   returncode=0)
        with mock.patch.object(opencode, "_run_grouped", spy):
            opencode.review_diff("kriterium", "+diff")
        self.assertEqual(len(seen), 1, "review_diff must go through _run_grouped")

    def test_adversarial_review_defaults_to_run_grouped(self):
        from unittest import mock
        from types import SimpleNamespace
        from werkbank import opencode
        seen = []
        def spy(*a, **kw):
            seen.append((a, kw))
            return SimpleNamespace(stdout='{"result":"ok"}', stderr="",
                                   returncode=0)
        with mock.patch.object(opencode, "_run_grouped", spy):
            opencode.adversarial_review("body", "+diff")
        self.assertEqual(len(seen), 1,
                         "adversarial_review must go through _run_grouped")

    def test_reviewer_timeout_releases_the_per_ticket_lock(self):
        """The whole point of WB-171: a hanging reviewer must not keep the
        per-ticket lock alive. Runs the real server thread against a
        mocked `adversarial_review` that raises TimeoutExpired (what
        `_run_grouped` re-raises after signalling the group)."""
        from unittest import mock
        import time as _time
        # Set up an isolated tempdir + minimal server module state so
        # _run_review's `store.load_tickets` and `append_review_note`
        # work without a real board.
        import tempfile as _tempfile
        from pathlib import Path as _Path
        from werkbank import server, store
        tmpdir = _Path(_tempfile.mkdtemp(prefix="werkbank-wb171-"))
        try:
            tickets_dir = tmpdir / "tickets"
            tickets_dir.mkdir()
            saved = server.TICKETS_DIR
            server.TICKETS_DIR = tickets_dir
            try:
                t = store.create_ticket(tickets_dir, title="X",
                                        description="Y", project=str(tmpdir))
                # The lock is a set on the server module; simulate the
                # start_review guard by adding the id there — this is
                # what start_review would do before spawning the thread.
                with server._REVIEWS_LOCK:
                    server._REVIEWS_RUNNING.add(t.id)
                def blows_up(*a, **kw):
                    raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
                with mock.patch.object(server.opencode,
                                       "adversarial_review", blows_up), \
                     mock.patch.object(server.subprocess, "check_output",
                                       return_value=""):
                    server._run_review(t.id)  # in-thread; must not raise
                # The whole point: the lock is empty afterwards.
                with server._REVIEWS_LOCK:
                    self.assertNotIn(t.id, server._REVIEWS_RUNNING)
                # And a Reviewer-Lauf-fehlgeschlagen note landed on the
                # ticket — the timeout is an honest, explained failure.
                fresh = {x.id: x for x in
                         store.load_tickets(tickets_dir)}[t.id]
                self.assertIn("Reviewer-Lauf fehlgeschlagen", fresh.body)
            finally:
                server.TICKETS_DIR = saved
        finally:
            remove_tree(tmpdir)


class Wb174OpencodeDurationTest(unittest.TestCase):
    """WB-174: opencode-lane runs must persist `duration_s` on the ticket,
    the same way the claude lane has since WB-139 — the WB-169 audit's
    "Ø-Sekunden pro Ticket-Kategorie" metric needs the number. Historical
    opencode tickets (WB-102/106/107/…) predate the frontmatter field and
    never had it; this test pins the CURRENT dispatcher's write."""

    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(
            self.dir, title="Lokal", description="Y",
            assignee="opencode", project=str(self.dir),
            gate="Tests laufen durch")
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self):
        return make_dispatcher(self,
            self.dir, cfg={"state_path": str(self.dir / "state.json"),
                           "default_project": str(self.dir),
                           "gates": {str(self.dir): {"Tests laufen durch": "true"}}})

    def test_success_writes_duration_s(self):
        from types import SimpleNamespace
        from unittest import mock

        def fake_work_ticket(t, cfg, on_progress=None, on_pid=None, owner=None):
            time.sleep(0.2)  # something to actually measure
            return SimpleNamespace(result="lokal fertig", status="review",
                                    changes={})

        d = self._dispatcher()
        with mock.patch.object(dispatch.opencode, "work_ticket",
                               fake_work_ticket):
            d.dispatch(self.t.id)
            d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.status, "review")
        self.assertTrue(after.duration_s,
                        "duration_s must be set on an opencode ticket too")
        self.assertGreaterEqual(int(after.duration_s), 0)
        int(after.duration_s)          # parses as an integer

    def test_escalation_still_carries_duration_s(self):
        """The one Outcome that ships a non-empty `.changes` is the WB-92
        escalation (`{"assignee": "claude"}`). `changes.update(outcome
        .changes)` must NOT accidentally strip our own duration_s — the
        two dicts must not fight over the same key."""
        from types import SimpleNamespace
        from unittest import mock

        def escalating(t, cfg, on_progress=None, on_pid=None, owner=None):
            time.sleep(0.1)
            # WB-92 escalation shape — hand the ticket back to claude.
            return SimpleNamespace(result="opencode gab auf", status="offen",
                                    changes={"assignee": "claude"})

        d = self._dispatcher()
        with mock.patch.object(dispatch.opencode, "work_ticket",
                               escalating):
            d.dispatch(self.t.id)
            d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.assignee, "claude")   # escalation happened
        self.assertTrue(after.duration_s,
                        "escalation must not eat duration_s")


class Wb178UnknownKeyErrorTest(unittest.TestCase):
    """WB-178: the raw `cannot update keys: [...]` ValueError bubbled up to
    the user unchanged (English, list syntax, no hint what to do). This
    tests the new German message AND pins that `interactive` — the field
    the incident report was about — is actually accepted by the current
    store (so a next server restart makes the reported error go away)."""

    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(self.dir, title="X", description="Y")

    def tearDown(self):
        remove_tree(self.dir)

    def test_unknown_key_error_is_german_and_names_the_likely_cause(self):
        with self.assertRaises(ValueError) as cm:
            store.update_ticket(self.dir, self.t.id, {"vollhonk": "ja"})
        msg = str(cm.exception)
        self.assertIn("Unbekannte Felder", msg)
        self.assertIn("vollhonk", msg)
        # The load-bearing hint: the reason 9 out of 10 users hit this is
        # a stale server, and the fix they need to hear is "restart".
        self.assertIn("Board", msg)
        self.assertIn("neu", msg)

    def test_interactive_is_accepted_by_the_current_store(self):
        # The specific field WB-178 was reported about — this is the
        # regression that prevents the same "cannot update keys:
        # ['interactive']" from surfacing after future refactors.
        store.update_ticket(self.dir, self.t.id, {"interactive": "ja"})
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(loaded.interactive, "ja")


class Wb175AssigneeRouterTest(unittest.TestCase):
    """WB-175: title-based router suggests opencode / claude at creation,
    the user always overrides in the dialog, and an override is logged so
    the owner can calibrate the regex list. Client-side and server-side pieces
    are pinned separately — the JS regex list travels through the config
    payload."""

    def test_default_config_carries_the_router_seed(self):
        # A fresh install must ship SOME patterns for both lanes — a router
        # config with either side empty would only suggest the other,
        # defeating the "safer default" invariant.
        from werkbank import server
        cfg = server.load_config()
        router = cfg.get("assignee_router") or {}
        self.assertTrue(router.get("opencode"),
                        "opencode seed patterns missing from load_config")
        self.assertTrue(router.get("claude"),
                        "claude seed patterns missing from load_config")

    def test_public_config_ships_the_router_to_the_client(self):
        # The client-side JS needs the same regex list; if a future refactor
        # strips it out of public_config, the hint stays empty forever.
        from werkbank import server
        cfg = server.load_config()
        cfg["password_hash"] = "must-not-leak"
        pub = server.public_config(cfg)
        self.assertIn("assignee_router", pub)
        self.assertNotIn("password_hash", pub)   # unchanged safety invariant

    def test_router_log_writes_line_on_override(self):
        # The whole point of the log: after two weeks the owner greps this file
        # to see which titles got misrouted. One JSON-lines record per
        # override event, unicode-safe, timestamped.
        from werkbank import server
        import tempfile as _tempfile
        from pathlib import Path as _Path
        d = _Path(str(temp_dir()))
        saved_cfg = dict(server.CONFIG)
        try:
            server.CONFIG.clear()
            server.CONFIG.update(saved_cfg)
            server.CONFIG["state_path"] = str(d / "state.json")
            server._log_router_override("opencode", "claude",
                                        "refactor der Datenbank", "WB-99")
            log = server._router_log_path()
            self.assertTrue(log.exists(), "override log was never written")
            line = log.read_text(encoding="utf-8").strip()
            self.assertIn('"suggested": "opencode"', line)
            self.assertIn('"chosen": "claude"', line)
            self.assertIn('"ticket": "WB-99"', line)
            self.assertIn("refactor der Datenbank", line)
        finally:
            server.CONFIG.clear()
            server.CONFIG.update(saved_cfg)
            remove_tree(d)

    def test_router_log_stays_silent_when_suggestion_taken(self):
        from werkbank import server
        import tempfile as _tempfile
        from pathlib import Path as _Path
        d = _Path(str(temp_dir()))
        saved_cfg = dict(server.CONFIG)
        try:
            server.CONFIG["state_path"] = str(d / "state.json")
            # Empty suggestion → no log.
            server._log_router_override("", "claude", "titel", "WB-1")
            self.assertFalse(server._router_log_path().exists())
            # Suggestion equals chosen → no log.
            server._log_router_override("opencode", "opencode", "doku", "WB-2")
            self.assertFalse(server._router_log_path().exists())
        finally:
            server.CONFIG.clear()
            server.CONFIG.update(saved_cfg)
            remove_tree(d)


class Wb175RouterBoardShapeTest(unittest.TestCase):
    """WB-175: the JS router lives in board.html — pin the pieces that a
    rename would silently break."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_router_function_and_safety_rule_present(self):
        # Function name + the load-bearing safety rule ("both match → claude
        # wins") + the config read path.
        self.assertIn("function routerSuggest", self.board)
        self.assertIn("config.assignee_router", self.board)
        # Claude branch runs BEFORE opencode branch — that IS the safety rule.
        claude_pos = self.board.find("router.claude")
        opencode_pos = self.board.find("router.opencode")
        self.assertLess(0, claude_pos)
        self.assertLess(claude_pos, opencode_pos,
                        "claude patterns must be checked FIRST — a "
                        "opencode-first order flips the WB-146 safety rule.")

    def test_submit_forwards_router_suggestion(self):
        # The only knob the server sees. Missing here = the log stays empty
        # forever and the owner has nothing to calibrate against.
        self.assertRegex(self.board,
                         r'body\.router_suggestion\s*=\s*formEl\.dataset'
                         r'\.routerSuggestion')

    def test_title_input_updates_hint_live(self):
        self.assertIn('.title.addEventListener(\n  "input", updateRouterHint)',
                      self.board)


class Wb230LiveOrphanTest(unittest.TestCase):
    """WB-230: the incident. Dispatcher died, agent process kept running
    for 73 min, ticket sat in in_arbeit, nobody caught the output. The
    ticker must NOTICE (not just sweep_orphaned at startup), the
    detection must NOT kill the live process (that was the WB-75
    "silent kill" the bug ticket protested), and the user's Beenden
    call must both kill the process and set fehlgeschlagen."""

    def setUp(self):
        self.dir = temp_dir()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            try: p.kill()
            except OSError: pass
            try: p.wait(timeout=2)
            except Exception: pass
        remove_tree(self.dir)

    def _spawn_for(self, ticket_id):
        # Same stand-in shape as SweepKillsOrphanProcessTest — 'claude' +
        # the ticket id in argv make _process_matches_ticket agree.
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(3600)",
             "claude", "-p", f"prompt for {ticket_id}"])
        self.procs.append(p)
        return p

    @posix_only
    def test_ticker_detects_live_orphan_and_marks_the_ticket(self):
        """The exact incident shape: ticket in_arbeit with a live process
        whose pid the dispatcher does NOT own — must be detected within
        one tick. Uses a fresh dispatcher whose _runs is empty (the
        original owner "died"); calls detect_live_orphans directly so
        the test does not have to wait for the 15 s tick."""
        t = store.create_ticket(self.dir, title="Verwaist", description="")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        p = self._spawn_for(t.id)
        store.update_ticket(self.dir, t.id, {"pid": str(p.pid)})

        d = make_dispatcher(self, self.dir)
        # _runs is empty by construction — this is the exact shape the
        # incident reached (dispatcher restarted, in-memory table gone).
        d.detect_live_orphans()

        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.orphaned, "ja")
        self.assertEqual(loaded.status, "in_arbeit")
        self.assertIsNone(p.poll(), "detect_live_orphans MUST NOT kill "
                          "the process — that decision belongs to the user")
        self.assertIn("Verwaister Lauf", loaded.body)
        self.assertIn(str(p.pid), loaded.body)

    @posix_only
    def test_dispatcher_own_run_is_not_flagged_as_orphan(self):
        """Regression pin: a ticket the dispatcher itself is running
        (i.e., in `_runs`) must NEVER be flagged, even if all other
        surface signals look identical."""
        t = store.create_ticket(self.dir, title="Ich arbeite dran",
                                description="")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        p = self._spawn_for(t.id)
        store.update_ticket(self.dir, t.id, {"pid": str(p.pid)})

        d = make_dispatcher(self, self.dir)
        d._runs[t.id] = {"pid": p.pid}      # simulate active ownership
        try:
            d.detect_live_orphans()
        finally:
            d._runs.pop(t.id, None)
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.orphaned, "")

    def test_detect_live_orphans_ignores_dead_pids(self):
        """A pid that is not alive is sweep_orphaned's problem, not the
        ticker's — the ticker only flags LIVE orphans. This keeps the
        crash-during-run case flowing to `fehlgeschlagen` at startup."""
        t = store.create_ticket(self.dir, title="Toter Lauf", description="")
        store.update_ticket(self.dir, t.id,
                            {"status": "in_arbeit", "pid": "1"})  # PID 1 is init; doesn't match
        d = make_dispatcher(self, self.dir)
        d.detect_live_orphans()
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.orphaned, "")

    def test_already_flagged_ticket_is_not_rewritten(self):
        """Idempotency: repeated ticks must not spam set_result with the
        same message every 15 s."""
        t = store.create_ticket(self.dir, title="Schon markiert",
                                description="")
        store.update_ticket(self.dir, t.id,
                            {"status": "in_arbeit", "orphaned": "ja"})
        p = self._spawn_for(t.id)
        store.update_ticket(self.dir, t.id, {"pid": str(p.pid)})
        before_body = {x.id: x for x in
                       store.load_tickets(self.dir)}[t.id].body
        d = make_dispatcher(self, self.dir)
        d.detect_live_orphans()
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.body, before_body,
                         "detect_live_orphans must be idempotent on "
                         "an already-flagged ticket")

    @posix_only
    def test_mark_orphan_failed_kills_and_flips_status(self):
        """The Beenden-Knopf: user-initiated termination + fehlgeschlagen."""
        t = store.create_ticket(self.dir, title="Nutzer entscheidet",
                                description="")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit",
                                             "orphaned": "ja"})
        p = self._spawn_for(t.id)
        store.update_ticket(self.dir, t.id, {"pid": str(p.pid)})

        result = dispatch.mark_orphan_failed(self.dir, t.id)

        self.assertTrue(result["terminated"])
        self.assertEqual(result["pid"], p.pid)
        end = time.time() + 3
        while time.time() < end and p.poll() is None:
            time.sleep(0.05)
        self.assertIsNotNone(p.poll(),
                             "mark_orphan_failed must actually end the process")
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.status, "fehlgeschlagen")
        self.assertEqual(loaded.orphaned, "")
        self.assertEqual(loaded.pid, "")
        self.assertIn("Beendet", loaded.body)

    def test_mark_orphan_failed_handles_dead_process_cleanly(self):
        """Race: user clicks Beenden after the process already died on
        its own. No terminate happens, but the ticket still lands in
        fehlgeschlagen with an honest note."""
        t = store.create_ticket(self.dir, title="Schon tot", description="")
        # PID 1 = init; not ours, so _process_matches_ticket is False.
        store.update_ticket(self.dir, t.id,
                            {"status": "in_arbeit", "orphaned": "ja",
                             "pid": "1"})
        result = dispatch.mark_orphan_failed(self.dir, t.id)
        self.assertFalse(result["terminated"])
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.status, "fehlgeschlagen")
        self.assertEqual(loaded.orphaned, "")

    @posix_only
    def test_sweep_orphaned_no_longer_kills_live_matching_process(self):
        """The very cost WB-230 protested: sweep_orphaned used to kill
        the live process silently and drop up to 40 min of on-disk
        work. Now it must LEAVE the process alone and mark the ticket."""
        t = store.create_ticket(self.dir, title="Lebt weiter", description="")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        p = self._spawn_for(t.id)
        store.update_ticket(self.dir, t.id, {"pid": str(p.pid)})

        dispatch.sweep_orphaned(self.dir)

        # Still alive; ticket still in_arbeit but flagged.
        self.assertIsNone(p.poll())
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.status, "in_arbeit")
        self.assertEqual(loaded.orphaned, "ja")

    def test_sweep_orphaned_still_fails_dead_runs(self):
        """The crash-mid-run case must keep flowing to `fehlgeschlagen` —
        WB-230 only changed the LIVE branch."""
        t = store.create_ticket(self.dir, title="Abgestürzt", description="")
        store.update_ticket(self.dir, t.id,
                            {"status": "in_arbeit", "pid": "999999999"})
        dispatch.sweep_orphaned(self.dir)
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.status, "fehlgeschlagen")
        self.assertEqual(loaded.pid, "")

    def test_adopt_orphans_skips_flagged_tickets(self):
        """WB-230 must not accidentally re-dispatch a live orphan through
        adopt_orphans — that would fork the run and produce two writers."""
        t = store.create_ticket(self.dir, title="Markiert", description="",
                                assignee="claude")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit",
                                             "orphaned": "ja"})
        d = make_dispatcher(self, self.dir)
        called = []
        d.dispatch = lambda tid: called.append(tid)  # spy
        d.adopt_orphans()
        self.assertEqual(called, [],
                         "adopt_orphans must not re-dispatch an orphaned=ja ticket")


class Wb230BoardShapeTest(unittest.TestCase):
    """WB-230: card renders the orphan warning + Beenden button."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_card_renders_orphaned_state(self):
        self.assertIn('t.orphaned === "ja"', self.board)
        self.assertIn("Verwaister Lauf", self.board)
        # Button POSTs to the WB-230 endpoint.
        self.assertRegex(self.board,
                         r'/api/tickets/"\s*\+\s*t\.id\s*\+\s*"/kill-orphan')


class Wb226GateGapTest(unittest.TestCase):
    """WB-226: a ticket that names something its configured gate does NOT
    prove (`gate_gap` set) is not autostarted — the runner never fires,
    the card bounces back to Offen with the gap text and a next-step hint.
    Applies to BOTH lanes: the local one has no chat-session escape hatch,
    so its message tells the user to reassign to claude."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        self.calls = []
        self.opencode_calls = []

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self):
        def runner(t, on_start=None):
            self.calls.append(t.id)
            return "hintergrund", "sess-bg"
        return make_dispatcher(self,
            self.dir, cfg={"state_path": str(self.state),
                           "default_project": str(self.dir),
                           "chat_handover_minutes": 10,
                           "gates": {str(self.dir):
                                     {"Tests laufen durch": "true"}}},
            runner=runner)

    def _load(self, tid):
        return {x.id: x for x in store.load_tickets(self.dir)}[tid]

    def test_field_roundtrips_and_collapses_newlines(self):
        t = store.create_ticket(
            self.dir, title="X", description="Y",
            gate_gap="Layout\nin der WebView")   # multi-line paste
        loaded = self._load(t.id)
        # One-line frontmatter invariant: newlines collapse to a space.
        self.assertNotIn("\n", loaded.gate_gap)
        self.assertIn("Layout", loaded.gate_gap)
        self.assertIn("WebView", loaded.gate_gap)
        # PATCH-write path normalises the same way.
        store.update_ticket(self.dir, t.id,
                            {"gate_gap": "andere\r\nSache"})
        self.assertEqual(self._load(t.id).gate_gap, "andere Sache")
        # Empty back to normal.
        store.update_ticket(self.dir, t.id, {"gate_gap": ""})
        self.assertEqual(self._load(t.id).gate_gap, "")

    def test_gate_gap_blocks_claude_autostart_and_bounces_to_offen(self):
        t = store.create_ticket(
            self.dir, title="UI-Ticket", description="",
            project=str(self.dir),
            gate_gap="Layout in der WebView, sieht die Prüfung nicht")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        d = self._dispatcher()
        d.dispatch(t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [],
                         "runner MUST NOT fire when gate_gap is set")
        after = self._load(t.id)
        self.assertEqual(after.status, "offen")
        self.assertIn("Kein Autostart", after.body)
        self.assertIn("Layout in der WebView", after.body)
        self.assertIn("zieh dir dein Ticket", after.body)

    def test_gate_gap_blocks_local_lane_with_reassign_hint(self):
        """Local lanes (opencode/dsh) have no chat concept — the message
        must not tell the user to open Claude Code there and wait.
        Instead it names the reassign-to-claude step."""
        from types import SimpleNamespace
        from unittest import mock

        t = store.create_ticket(
            self.dir, title="Layout-Fix", description="",
            assignee="opencode", project=str(self.dir),
            gate="Tests laufen durch",
            gate_gap="Bildschirm — die Prüfung sieht keine Farben")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})

        oc_calls = []
        def fake_work_ticket(t, cfg, on_progress=None, on_pid=None, owner=None):
            oc_calls.append(t.id)
            return SimpleNamespace(result="ok", status="review", changes={})

        d = self._dispatcher()
        with mock.patch.object(dispatch.opencode, "work_ticket",
                               fake_work_ticket):
            d.dispatch(t.id)
            d.join(timeout=5)
        self.assertEqual(oc_calls, [],
                         "local runner MUST NOT fire when gate_gap is set")
        after = self._load(t.id)
        self.assertEqual(after.status, "offen")
        self.assertIn("setze den Bearbeiter auf claude", after.body)
        self.assertIn("Bildschirm", after.body)

    def test_empty_gate_gap_leaves_dispatch_unchanged(self):
        # Regression pin: no bounce when the field is empty (previous
        # behaviour must survive the branch add).
        t = store.create_ticket(self.dir, title="Normal", description="",
                                project=str(self.dir), gate_gap="")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        d = self._dispatcher()
        d.dispatch(t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [t.id])
        self.assertEqual(self._load(t.id).status, "review")


class Wb227AssortmentByBackendTest(unittest.TestCase):
    """WB-227 (follow-up to WB-219 / WB-238): the SLOT a ticket runs in
    is chosen by the BACKEND, not by the assignee name. A dsh ticket with
    `backend: claude` starts the local Claude CLI and never touches the
    GPU (measured: 37 s Claude vs 79 s Qwen on the same ticket) — it
    must not serialise against opencode/dsh Qwen runs, and Qwen runs
    must not wait behind it. Everything else keeps its historical slot,
    including the "two local runs never at the same time" invariant."""

    def test_dsh_with_claude_backend_lands_in_claude_slot(self):
        # The whole point: dsh+claude → claude slot (per-project claude
        # lane per WB-146). A Qwen run and a Claude-backend dsh run may
        # then start in parallel.
        self.assertEqual(dispatch.assortment("dsh", "claude"), "claude")

    def test_dsh_with_local_backend_stays_in_local_slot(self):
        # Explicit "local" is the wrapper's default. Same lane as
        # opencode — one GPU, one run at a time.
        self.assertEqual(dispatch.assortment("dsh", "local"),
                         dispatch.LOCAL_SLOT)

    def test_dsh_with_empty_backend_stays_in_local_slot(self):
        # Backend unset means the wrapper's default, which is local.
        # Explicit AND implicit must route the same way.
        self.assertEqual(dispatch.assortment("dsh", ""),
                         dispatch.LOCAL_SLOT)
        self.assertEqual(dispatch.assortment("dsh"),
                         dispatch.LOCAL_SLOT)

    def test_opencode_ignores_backend_and_stays_in_local_slot(self):
        # opencode-task knows nothing about backends and the store
        # already refuses backend on non-dsh tickets — but assortment
        # must not accidentally rely on that upstream check. If a
        # rogue ticket somehow carried backend=claude on an opencode
        # assignee, its slot must still be local (the runner is
        # opencode-task, which needs the GPU).
        self.assertEqual(dispatch.assortment("opencode", "claude"),
                         dispatch.LOCAL_SLOT)

    def test_claude_assignee_always_claude_slot(self):
        # No surprises for the historical case: a claude ticket lands
        # in claude regardless of what a bogus backend value might say.
        self.assertEqual(dispatch.assortment("claude", ""), "claude")
        self.assertEqual(dispatch.assortment("claude", "local"), "claude")
        self.assertEqual(dispatch.assortment("claude", "claude"), "claude")

    def test_two_local_runs_never_share_a_slot_is_still_the_rule(self):
        # WB-227's "Fertig, wenn"-guard: the pre-existing invariant
        # that dsh (default) and opencode share ONE slot must not
        # regress. Same shape as the existing WB-219 test but pinned
        # explicitly against the WB-227 change.
        self.assertEqual(dispatch.assortment("dsh"),
                         dispatch.assortment("opencode"))
        self.assertEqual(dispatch.assortment("dsh", "local"),
                         dispatch.assortment("opencode", ""))

    def test_dispatcher_dispatch_uses_backend_when_choosing_the_lane(self):
        """End-to-end: create a dsh ticket with backend=claude, dispatch
        it, verify it lands in the claude slot's queue — not the local
        one. Uses the pending/slot bookkeeping the Dispatcher exposes
        via `_pending`."""
        from stubs import temp_dir as _tmp, remove_tree as _rm
        d = _tmp()
        try:
            t = store.create_ticket(d, title="dsh-claude", description="",
                                    assignee="dsh", backend="claude",
                                    gate="Tests laufen durch")
            store.update_ticket(d, t.id, {"status": "in_arbeit"})
            disp = make_dispatcher(self, d)
            disp.dispatch(t.id)
            with disp._lock:
                claude_pending = set(disp._pending["claude"])
                local_pending = set(disp._pending[dispatch.LOCAL_SLOT])
            self.assertIn(t.id, claude_pending)
            self.assertNotIn(t.id, local_pending)
        finally:
            _rm(d)


class Wb238BackendFieldTest(unittest.TestCase):
    """WB-238: per-ticket dsh backend choice. Store validates the value
    AND that the assignee is dsh (opencode/claude reject it, not silently
    ignore). The dsh runner sees `DSH_TASK_BACKEND=claude` in its env
    when the field is set — the wrapper already reads that switch, this
    is the plumbing between the ticket and the wrapper."""

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_backend_field_roundtrips(self):
        t = store.create_ticket(self.dir, title="X", description="",
                                assignee="dsh", backend="claude",
                                gate="Tests laufen durch")
        loaded = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(loaded.backend, "claude")
        # PATCH-write path accepts the same values.
        store.update_ticket(self.dir, t.id, {"backend": ""})
        self.assertEqual({x.id: x for x in
                          store.load_tickets(self.dir)}[t.id].backend, "")
        store.update_ticket(self.dir, t.id, {"backend": "local"})
        self.assertEqual({x.id: x for x in
                          store.load_tickets(self.dir)}[t.id].backend, "local")

    def test_backend_rejects_bogus_value(self):
        with self.assertRaises(ValueError) as cm:
            store.create_ticket(self.dir, title="X", description="",
                                assignee="dsh", backend="qwen")
        self.assertIn("backend muss einer von", str(cm.exception))
        t = store.create_ticket(self.dir, title="Y", description="",
                                assignee="dsh")
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id, {"backend": "unbekannt"})

    def test_backend_rejects_on_non_dsh_assignee(self):
        """The Umsetzungsskizze says: `assignee: opencode` (or claude)
        ignores the field — a set `backend:` there must be rejected, not
        silently dropped, or the user wonders why their choice does
        nothing."""
        with self.assertRaises(ValueError) as cm:
            store.create_ticket(self.dir, title="X", description="",
                                assignee="opencode", backend="claude",
                                gate="Tests laufen durch")
        self.assertIn("dsh", str(cm.exception))
        with self.assertRaises(ValueError):
            store.create_ticket(self.dir, title="Y", description="",
                                assignee="claude", backend="claude")
        # A dsh ticket that later gets its assignee changed to opencode
        # while trying to keep backend=claude in the same PATCH: still
        # rejected. The effective-assignee is what matters.
        t = store.create_ticket(self.dir, title="Z", description="",
                                assignee="dsh", gate="Tests laufen durch")
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id,
                                {"assignee": "opencode", "backend": "claude"})

    def test_backend_empty_is_always_allowed(self):
        # Sending backend="" is the "no-op / clear it" path — must never
        # trip the "wrong assignee" check.
        t = store.create_ticket(self.dir, title="X", description="",
                                assignee="claude")
        store.update_ticket(self.dir, t.id, {"backend": ""})  # no raise
        self.assertEqual({x.id: x for x in
                          store.load_tickets(self.dir)}[t.id].backend, "")

    def test_dsh_run_env_carries_backend_claude(self):
        """The actual plumbing: opencode._run_kwargs must set
        DSH_TASK_BACKEND=claude in the run's environment when the ticket
        says so. Same env dict that already carries WERKBANK_TICKET_ID."""
        from werkbank import opencode

        # Fake `run` with an `env` parameter so _run_kwargs decides to
        # attach the env dict (signature-sniffed — see the docstring).
        def fake_run(cmd, input=None, capture_output=True, text=True,
                     timeout=None, cwd=None, env=None, on_pid=None):
            fake_run.env = env
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        # dsh ticket with backend=claude → env carries DSH_TASK_BACKEND=claude.
        t_claude = store.create_ticket(
            self.dir, title="A", description="", assignee="dsh",
            backend="claude", gate="Tests laufen durch",
            project=str(self.dir))
        opencode.run_task(t_claude, "aufgabe", run=fake_run)
        self.assertEqual(fake_run.env.get("DSH_TASK_BACKEND"), "claude")

        # dsh ticket without backend → the wrapper's default; no env var set.
        t_local = store.create_ticket(
            self.dir, title="B", description="", assignee="dsh",
            gate="Tests laufen durch", project=str(self.dir))
        opencode.run_task(t_local, "aufgabe", run=fake_run)
        self.assertNotIn("DSH_TASK_BACKEND", fake_run.env)

        # backend=local is the same "no override" state; wrapper default.
        # (An empty string would also be fine, but local is the value a
        # user picks from the dropdown, so pin it.)
        store.update_ticket(self.dir, t_local.id, {"backend": "local"})
        t_local2 = {x.id: x for x in
                    store.load_tickets(self.dir)}[t_local.id]
        opencode.run_task(t_local2, "aufgabe", run=fake_run)
        self.assertEqual(fake_run.env.get("DSH_TASK_BACKEND"), "local")

    def test_opencode_run_never_sets_dsh_backend_env(self):
        """Belt-&-braces: even if a rogue ticket somehow slipped past
        validation with backend=claude on an opencode assignee, the
        env plumbing must not lie to the opencode wrapper about which
        knob it should read."""
        from werkbank import opencode

        def fake_run(cmd, input=None, capture_output=True, text=True,
                     timeout=None, cwd=None, env=None, on_pid=None):
            fake_run.env = env
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        t = store.create_ticket(self.dir, title="X", description="",
                                assignee="opencode",
                                gate="Tests laufen durch",
                                project=str(self.dir))
        # Bypass the store's assignee check to simulate the paranoid case.
        t.backend = "claude"
        opencode.run_task(t, "aufgabe", run=fake_run)
        self.assertNotIn("DSH_TASK_BACKEND", fake_run.env)


class Wb238BackendBoardShapeTest(unittest.TestCase):
    """WB-238: the backend row and the badge are in the shape the ticket
    asks for — visible only when assignee=dsh, warns about the quota
    cost, and card marks a claude-backend run so the ticket says WHO
    ran (even though it looks like a local ticket)."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_backend_select_in_both_dialogs(self):
        self.assertEqual(self.board.count('name="backend"'), 2)
        self.assertIn(".backend-row", self.board)

    def test_syncBackendRow_hides_when_not_dsh_and_clears_value(self):
        # The load-bearing hide-and-clear behaviour. If the row stayed
        # visible for claude/opencode, the user could pick "claude" and
        # then be surprised by the store rejection.
        self.assertIn("function syncBackendRow", self.board)
        self.assertRegex(self.board,
            r'isDsh\s*=\s*\(form\.assignee\.value.*===.*"dsh"')
        self.assertRegex(self.board,
            r'if\s*\(\s*!\s*isDsh\s*\)\s*form\.backend\.value\s*=\s*""')

    def test_quota_warning_present_in_dialog(self):
        self.assertIn("verbraucht Abo-Kontingent", self.board)

    def test_card_marks_claude_backend_run(self):
        self.assertRegex(self.board,
            r'\(t\.backend\s*\|\|\s*""\)\.toLowerCase\(\)\s*===\s*"claude"')
        self.assertIn("🧠 claude-backend", self.board)

    def test_detail_submit_ships_backend_only_when_changed(self):
        # WB-251 shape for the new field.
        self.assertRegex(self.board,
            r'if\s*\(\s*backend\s*!==\s*\(detailTicket\.backend\s*\|\|\s*""\s*\)\s*\)'
            r'\s*\n?\s*patch\.backend\s*=\s*backend')


class Wb251DetailSaveSendsOnlyDiffsTest(unittest.TestCase):
    """WB-251: reporter clicked Save in the detail dialog and got an
    "Unbekannter/Unbekannte Felder" toast for tickets whose gate_gap
    field was empty and unchanged. Cause: the client always shipped
    `gate_gap` (and `interactive` before it), and any running server
    that predated the field crashed on it — the "cannot save the
    description" symptom on the day each field landed. Fix: send those
    fields only when they differ from the loaded ticket. Two guards:
    a store-level reproduction of the raw collision (what the old
    server did to the request), and a board-shape pin that the diff
    check is in the submit handler."""

    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(self.dir, title="X", description="Y")

    def tearDown(self):
        remove_tree(self.dir)

    def test_store_rejects_unknown_gate_gap_on_an_older_server_shape(self):
        """Reproduces the exact 400 the reporter saw: simulate a store from
        BEFORE WB-226 by patching the allowed set at test time; a PATCH
        that carries `gate_gap` (even empty) fires the WB-178 message —
        which is what the user then reports as an "unknown error" on
        the day the field lands."""
        from unittest import mock
        real_update = store._update_locked
        def old_update(tickets_dir, ticket_id, changes, expected_version=None):
            allowed = {"title", "type", "status", "assignee", "project",
                       "priority", "nach", "nicht_mit", "fork", "gate",
                       "review", "session", "handover", "handover_at",
                       "handover_expired", "limit_until", "pid", "answer",
                       "tokens_in", "tokens_out", "tokens_cache", "cost_usd",
                       "duration_s", "queue_pos", "epic", "interactive",
                       "review_cost_usd", "orphaned", "claimed_at", "body"}
            unknown = set(changes) - allowed
            if unknown:
                raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}. "
                                 "Meist heißt das, das laufende Board ist älter "
                                 "als das Ticket-Formular — starte das Board neu.")
            return real_update(tickets_dir, ticket_id, changes, expected_version)
        with mock.patch.object(store, "_update_locked", side_effect=old_update):
            with self.assertRaises(ValueError) as cm:
                store.update_ticket(self.dir, self.t.id,
                                    {"gate_gap": "", "body": "..."})
            self.assertIn("Unbekannte Felder", str(cm.exception))
            self.assertIn("gate_gap", str(cm.exception))

    def test_board_detail_submit_only_sends_gate_gap_when_changed(self):
        board = (Path(__file__).resolve().parent.parent
                 / "src/werkbank/board.html").read_text(encoding="utf-8")
        # The diff-guard for gate_gap in the detail submit.
        self.assertRegex(board,
            r'if\s*\(\s*gap\s*!==\s*\(detailTicket\.gate_gap\s*\|\|\s*""\s*\)\s*\)'
            r'\s*\n?\s*patch\.gate_gap\s*=\s*gap')
        # Same guard for interactive — the earlier version of the same bug.
        self.assertRegex(board,
            r'if\s*\(\s*interactive\s*!==\s*'
            r'\(detailTicket\.interactive\s*\|\|\s*"nein"\s*\)\s*\)\s*\n?'
            r'\s*patch\.interactive\s*=\s*interactive')

    def test_board_detail_submit_no_longer_ships_gate_gap_unconditionally(self):
        # A safety net for the same class of regression: the old unconditional
        # `gate_gap: form.gate_gap.value,` in the PATCH object was exactly what
        # made the reporter's save crash. If this pattern comes back, the
        # test fires.
        board = (Path(__file__).resolve().parent.parent
                 / "src/werkbank/board.html").read_text(encoding="utf-8")
        self.assertNotRegex(board,
            r'gate_gap:\s*form\.gate_gap\.value\s*,')


class Wb226GateGapBoardShapeTest(unittest.TestCase):
    """WB-226: the board pieces the user actually sees — a rename or removal
    here must consciously touch this test."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_field_appears_in_both_dialogs(self):
        self.assertEqual(self.board.count('name="gate_gap"'), 2,
                         "gate_gap input must be in create AND detail dialogs")

    def test_card_renders_the_gap_badge_and_note(self):
        self.assertIn(".gate-gap-badge", self.board)
        self.assertIn(".gate-gap-note", self.board)
        self.assertIn("Prüfung deckt nicht ab", self.board)
        # The gap text lands in the card body via textContent (no HTML).
        self.assertIn('line.textContent = "Kein Autostart', self.board)


class Wb168InteractiveOptInTest(unittest.TestCase):
    """WB-168: a ticket with `interactive: ja` takes the same chat-only
    dispatch path as an epic — with a live chat session it goes to WB-22
    handover; without one it bounces back to Offen with a message,
    instead of quietly running in the background."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        self.calls = []

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self):
        def runner(t, on_start=None):
            self.calls.append(t.id)
            return "hintergrund", "sess-bg"
        return make_dispatcher(self,
            self.dir, cfg={"state_path": str(self.state),
                           "default_project": str(self.dir),
                           "chat_handover_minutes": 10},
            runner=runner)

    def _make(self, **fields):
        t = store.create_ticket(self.dir, title="Nur mit dir", description="",
                                project=str(self.dir), **fields)
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        return t

    def _load(self, tid):
        return {x.id: x for x in store.load_tickets(self.dir)}[tid]

    def test_interactive_field_roundtrips(self):
        t = store.create_ticket(self.dir, title="X", description="",
                                interactive="ja")
        self.assertEqual(t.interactive, "ja")
        loaded = self._load(t.id)
        self.assertEqual(loaded.interactive, "ja")
        store.update_ticket(self.dir, t.id, {"interactive": "nein"})
        self.assertEqual(self._load(t.id).interactive, "nein")

    def test_interactive_rejects_bad_value(self):
        with self.assertRaises(ValueError):
            store.create_ticket(self.dir, title="X", description="",
                                interactive="vielleicht")
        t = store.create_ticket(self.dir, title="Y", description="")
        with self.assertRaises(ValueError):
            store.update_ticket(self.dir, t.id, {"interactive": "yes"})

    def test_interactive_ticket_without_chat_session_bounces_to_offen(self):
        t = self._make(interactive="ja")
        d = self._dispatcher()
        d.dispatch(t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [], "background runner must not fire")
        after = self._load(t.id)
        self.assertEqual(after.status, "offen")
        self.assertIn("Ticket wartet auf eine Chat-Session", after.body)
        self.assertIn("zieh dir dein Ticket", after.body)

    def test_interactive_ticket_with_chat_session_uses_wb22_handover(self):
        force_marker_handover(self)
        t = self._make(interactive="ja")
        dispatch.register_ticket_session(str(self.dir), "chat-int", self.state)
        d = self._dispatcher()
        d.dispatch(t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [], "background runner must not fire")
        after = self._load(t.id)
        self.assertEqual(after.status, "in_arbeit")
        self.assertEqual(after.handover, "chat-int")

    def test_non_interactive_ticket_without_chat_still_runs_in_background(self):
        # Regression: the WB-168 branch must not swallow normal claude tickets.
        t = self._make()  # default interactive="nein", type="aufgabe"
        d = self._dispatcher()
        d.dispatch(t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [t.id])
        self.assertEqual(self._load(t.id).status, "review")

    def test_opencode_ignores_the_interactive_opt_in(self):
        # Opencode has no chat session; the opt-in is a no-op for it.
        from types import SimpleNamespace
        from unittest import mock
        t = store.create_ticket(self.dir, title="Lokal",
                                description="", assignee="opencode",
                                project=str(self.dir), interactive="ja",
                                gate="Tests laufen durch")
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        oc_calls = []

        def fake_work_ticket(t, cfg, on_progress=None, **_):
            oc_calls.append(t.id)
            return SimpleNamespace(result="ok", status="review", changes={})

        d = self._dispatcher()
        with mock.patch.object(dispatch.opencode, "work_ticket", fake_work_ticket):
            d.dispatch(t.id)
            d.join(timeout=5)
        self.assertEqual(oc_calls, [t.id])
        self.assertEqual(self._load(t.id).status, "review")


class Wb168BoardShapeTest(unittest.TestCase):
    """WB-168: the interactive-only checkbox is where the ticket said it
    should be (right under the assignee select) in both dialogs, and
    the card renders a badge when a ticket carries `interactive: ja`."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_checkbox_appears_in_both_dialogs(self):
        self.assertEqual(self.board.count('name="interactive"'), 2)
        self.assertIn(".interactive-label", self.board)

    def test_card_renders_the_interactive_badge(self):
        self.assertIn('interactive-badge', self.board)
        self.assertIn('t.interactive === "ja"', self.board)


class Wb164PhoneChipScrollTest(unittest.TestCase):
    """WB-164: on phones the page jumped to the top every 5 seconds. Cause:
    the active chip's `scrollIntoView({block: "nearest"})` fell through to
    the window (the statusBar is only horizontally scrollable) and, once
    the user had scrolled down, pulled the page back to the top on every
    refresh. Fix: scroll the statusBar horizontally by hand — never touch
    the window."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_chip_scrollintoview_is_gone(self):
        # The regex targets an actual CALL (with the open paren), so the
        # journal-style comment nearby that names the old API for context
        # does not trigger a false positive; only the reintroduced call
        # would.
        self.assertNotRegex(
            self.board, r"\.scrollIntoView\s*\(",
            "WB-164 regressed: a scrollIntoView call is back and will pull "
            "the page to the top on every refresh once the phone user has "
            "scrolled down.")

    def test_status_bar_scrolls_itself_horizontally(self):
        # The replacement centres the active chip within the statusBar and
        # nowhere else. Pin the shape (both the call site and the fact that
        # it is scoped to `bar`, i.e. the statusBar element).
        self.assertRegex(self.board,
                         r'bar\.scrollTo\(\s*\{\s*left:')
    """WB-107: the delete dialog must not promise a fake safety net.

    The board never commits, so a ticket that was never in git is gone once
    deleted. The old wording claimed it stays recoverable "über die
    Sicherungs-Historie" — that promise was false. The new warning names the
    one real way back (a prior commit) and tells the user how to get one.
    These greps keep the false promise from creeping back in."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_old_false_promise_is_gone(self):
        self.assertNotIn("Sicherungs-Historie", self.board)

    def test_new_warning_names_the_real_way_back(self):
        self.assertIn("sichere die Tickets", self.board)
        self.assertIn("committet", self.board)


class Wb135FinalizeGuardTest(unittest.TestCase):
    """WB-135 finding 3 — a returning worker must not overwrite a ticket the
    user has already accepted.

    Production reproduction (2026-08-16): WB-107 sat on `erledigt` (accepted,
    with a full Ergebnis); after the stalled opencode process was killed, its
    worker's `set_result`/`update_ticket` fired and pushed the ticket back to
    `review` with "(keine Ausgabe)". The acceptance was silently erased. The
    guard is placement-agnostic: the SAME return path also serves the Claude
    lane, so protecting `_finalize_run` covers both."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self, on_start_side_effect=None):
        """Runner that first calls on_start (like a real run), then lets the
        test flip the ticket to `erledigt` before returning its outcome —
        the exact ordering the WB-107 case observed."""
        def runner(t, on_start=None, **kw):
            if on_start is not None:
                on_start({"model": "opencode"})
            if on_start_side_effect is not None:
                on_start_side_effect()
            return "gefaelschtes Ergebnis vom zurueckgekehrten Lauf", "sess-x"
        cfg = {"state_path": str(self.state), "default_project": str(self.dir),
               "nonblocking_review": {str(self.dir): True}}
        return make_dispatcher(self, self.dir, cfg=cfg, runner=runner)

    def _make_and_run(self, accept_between):
        t = store.create_ticket(self.dir, title="X", description="ein wichtiges Ergebnis",
                                project=str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        # Whatever the run reports, the ticket has been accepted by the user
        # between the run's start and its return.
        d = self._dispatcher(on_start_side_effect=accept_between)
        d._run_one(t.id)
        return t.id

    def test_accepted_ticket_stays_accepted(self):
        def accept():
            store.set_result(self.dir, "WB-1",
                             "Vom Nutzer ausdruecklich abgenommen.")
            store.update_ticket(self.dir, "WB-1", {"status": "erledigt"})
        self._make_and_run(accept)
        after = {x.id: x for x in store.load_tickets(self.dir)}["WB-1"]
        self.assertEqual(after.status, "erledigt",
                         "returning worker erased the acceptance — WB-135 regression")
        self.assertIn("Vom Nutzer ausdruecklich abgenommen", after.body)
        self.assertNotIn("gefaelschtes Ergebnis", after.body)

    def test_non_erledigt_ticket_still_receives_the_outcome(self):
        """The guard must be narrow: a ticket that only advanced within the
        normal flow (e.g. still in_arbeit) still gets its result written."""
        self._make_and_run(lambda: None)
        after = {x.id: x for x in store.load_tickets(self.dir)}["WB-1"]
        self.assertEqual(after.status, "review")
        self.assertIn("gefaelschtes Ergebnis", after.body)

    def test_rejected_ticket_stays_rejected(self):
        """WB-135 finding 3 (extended): the ticket said 'wenn es inzwischen
        weitergezogen ist' — erledigt is not the only user decision worth
        protecting. If the user rejected mid-flight via 'Ablehnen mit Grund'
        the status is `offen` with the reason in the body. A returning worker
        overwriting that would silently erase the rejection AND restart the
        cycle by pushing the ticket back to `review`."""
        def reject():
            store.set_result(self.dir, "WB-1",
                             "**Ablehnung (2026-08-16):** greift nicht die Ursache an.")
            store.update_ticket(self.dir, "WB-1", {"status": "offen"})
        self._make_and_run(reject)
        after = {x.id: x for x in store.load_tickets(self.dir)}["WB-1"]
        self.assertEqual(after.status, "offen",
                         "returning worker resurrected the rejected ticket — WB-135 regression")
        self.assertIn("Ablehnung", after.body)
        self.assertNotIn("gefaelschtes Ergebnis", after.body)

    def test_requeued_ticket_stays_queued(self):
        """Same logic for a manual retry: the user moved the ticket to
        `zu_bearbeiten` because they want a fresh run — a stale returning
        worker's outcome must not steal that intent."""
        def requeue():
            store.update_ticket(self.dir, "WB-1", {"status": "zu_bearbeiten"})
        self._make_and_run(requeue)
        after = {x.id: x for x in store.load_tickets(self.dir)}["WB-1"]
        self.assertEqual(after.status, "zu_bearbeiten",
                         "returning worker preempted the requeue — WB-135 regression")
        self.assertNotIn("gefaelschtes Ergebnis", after.body)


@linux_only
class Wb142IdentifyAgentsTest(unittest.TestCase):
    """WB-142: an agent process must be traceable to its ticket, and a run
    whose ticket has moved on must not keep writing to the repo.

    Production reproduction (2026-08-16, escalation): six opencode processes
    running in the same repo at once, five of them orphaned to systemd, no
    way to say which belonged to which ticket. The dispatcher now sets
    WERKBANK_TICKET_ID in every agent run's environment; /proc/<pid>/environ
    is authoritative and does not depend on the wrapper's argv shape.
    `find_ownerless_agents` walks that env by ticket status."""

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def _spawn_marked(self, ticket_id, owner=None):
        """A sleep bearing WERKBANK_TICKET_ID (and the owning board's
        WERKBANK_TICKETS_DIR) in its own environment — the exact shape a real
        agent run leaves in /proc after Popen. `owner=None` spawns an
        UNSTAMPED process (pre-fix shape / foreign tooling): those must never
        be judged, let alone killed."""
        env = dict(os.environ)
        env["WERKBANK_TICKET_ID"] = ticket_id
        if owner is not None:
            env["WERKBANK_TICKETS_DIR"] = str(owner)
        else:
            env.pop("WERKBANK_TICKETS_DIR", None)
        proc = subprocess.Popen(sleeper_command(30), env=env)
        self.addCleanup(self._reap, proc)
        return proc

    def _wait_marked(self, proc):
        """Wait until /proc exposes the env of the just-forked process."""
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if dispatch._process_env(proc.pid).get("WERKBANK_TICKET_ID"):
                return
            time.sleep(0.05)

    def _reap(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_identify_a_running_agent_by_its_ticket_env(self):
        """First WB-142 acceptance: 'wer schreibt gerade in mein Projekt?'
        must be answerable in one lookup. The env-var is that lookup."""
        proc = self._spawn_marked("WB-777")
        # /proc/<pid>/environ appears only after the exec has completed; a
        # micro-poll matches production timing (the reaper runs from the
        # ticker, seconds after Popen).
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            env = dispatch._process_env(proc.pid)
            if env.get("WERKBANK_TICKET_ID"):
                break
            time.sleep(0.05)
        self.assertEqual(env.get("WERKBANK_TICKET_ID"), "WB-777")
        self.assertTrue(dispatch._process_matches_ticket(proc.pid, "WB-777"))

    def test_ownerless_agent_is_detected_and_reaped(self):
        """Second WB-142 acceptance: a run whose ticket is not `in_arbeit`
        is ownerless — the escalation showed five such at once. The reaper
        must find and terminate it; running-and-live tickets are untouched."""
        t = store.create_ticket(self.dir, title="Legitim", description="",
                                project=str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
        alive_proc = self._spawn_marked(t.id, owner=self.dir)  # live ticket, safe
        orphan_proc = self._spawn_marked("WB-999-existiert-nicht", owner=self.dir)
        # Give /proc a moment to expose the env of the just-forked processes.
        self._wait_marked(orphan_proc)
        found = dispatch.find_ownerless_agents(self.dir)
        found_pids = {pid for pid, _ in found}
        self.assertIn(orphan_proc.pid, found_pids,
                      "orphan (unknown ticket id) must be flagged")
        self.assertNotIn(alive_proc.pid, found_pids,
                         "a process whose ticket is in_arbeit must be spared")
        reaped = dispatch.reap_ownerless_agents(self.dir)
        reaped_pids = {pid for pid, _ in reaped}
        self.assertIn(orphan_proc.pid, reaped_pids)
        self.assertNotIn(alive_proc.pid, reaped_pids)
        # After reaping, the orphan is really dead — verified via /proc.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and dispatch._is_running(orphan_proc.pid):
            time.sleep(0.05)
        self.assertFalse(dispatch._is_running(orphan_proc.pid),
                         "orphan survived the reap — WB-142 regression")
        self.assertTrue(dispatch._is_running(alive_proc.pid),
                        "legitimate run was killed — WB-142 over-reach")

    def test_foreign_boards_runs_are_never_reaped(self):
        """WB-142 follow-up: ticket ids repeat across boards (every Werkbank
        counts WB-1, WB-2, …). A run stamped with ANOTHER tickets dir — a
        second board, or the test suite spawning stand-ins while the real
        board runs — is not ours, whatever its ticket id says. The first cut
        judged by id alone and would have killed it."""
        foreign = temp_dir()
        self.addCleanup(shutil.rmtree, foreign, True)
        proc = self._spawn_marked("WB-1", owner=foreign)
        self._wait_marked(proc)
        found_pids = {pid for pid, _ in dispatch.find_ownerless_agents(self.dir)}
        self.assertNotIn(proc.pid, found_pids,
                         "a foreign board's run was claimed — cross-board kill")
        self.assertTrue(dispatch._is_running(proc.pid))

    def test_unstamped_processes_are_never_reaped(self):
        """A process carrying only the ticket id (pre-fix shape, or foreign
        tooling reusing the variable) has no provable owner: skip it. We only
        ever kill what we can PROVE is ours."""
        proc = self._spawn_marked("WB-999-existiert-nicht", owner=None)
        self._wait_marked(proc)
        found_pids = {pid for pid, _ in dispatch.find_ownerless_agents(self.dir)}
        self.assertNotIn(proc.pid, found_pids,
                         "an unstamped process was claimed by the reaper")
        self.assertTrue(dispatch._is_running(proc.pid))

    def test_default_runner_records_pid_and_owner(self):
        """The default runner lambda must ACCEPT on_pid — _run_one filters
        kwargs by the runner's signature, so a lambda without the parameter
        silently discards the callback and no claude pid is ever recorded
        (found live 2026-08-16: WB-142's own run, ticket pid empty). It must
        also stamp the run with the board's tickets dir."""
        import inspect
        from types import SimpleNamespace
        captured = {}

        def fake_run_claude(t, cfg, on_start=None, on_event=None, on_pid=None,
                            owner_dir=None):
            captured["on_pid"] = on_pid
            captured["owner_dir"] = owner_dir
            return ("ok", "sess-1")

        real = dispatch.run_claude
        dispatch.run_claude = fake_run_claude
        try:
            d = make_dispatcher(self, self.dir, cfg={"default_project": str(self.dir)})
            params = inspect.signature(d.runner).parameters
            self.assertIn("on_pid", params,
                          "default runner drops on_pid — WB-75 recording dead")
            d.runner(SimpleNamespace(id="WB-5", project=str(self.dir)),
                     on_pid=lambda pid: captured.setdefault("pid_cb", pid))
            self.assertIsNotNone(captured["on_pid"],
                                 "on_pid not forwarded to run_claude")
            self.assertEqual(captured["owner_dir"], str(self.dir),
                             "owner_dir not forwarded to run_claude")
        finally:
            dispatch.run_claude = real


class DispatcherExclusivityTest(unittest.TestCase):
    """WB-142 round 2: ONE dispatcher per tickets dir.

    The 2026-08-16 swarm: a test harness imported werkbank.server, whose
    module-level Dispatcher bound the REAL tickets dir. Each extra instance
    has its own _pending, so it re-dispatched tickets that already had live
    runs (two runs, same ticket, same checkout — reproduced), and when
    pytest killed the harness its runs lived on under systemd (five ownerless
    agents at once). The lock makes every extra instance inert."""

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def _queued_ticket(self):
        t = store.create_ticket(self.dir, title="Opfer", description="",
                                project=str(self.dir))
        store.update_ticket(self.dir, t.id, {"status": "zu_bearbeiten"})
        return t

    def test_second_dispatcher_on_the_same_board_is_inert(self):
        t = self._queued_ticket()
        first_runs, second_runs = [], []
        d1 = make_dispatcher(self, self.dir,
                             cfg={"queue_poll_seconds": 3600,
                                  "default_project": str(self.dir)},
                             runner=lambda x, **kw: first_runs.append(x.id) or "ok")
        d2 = make_dispatcher(self, self.dir,
                             cfg={"queue_poll_seconds": 3600,
                                  "default_project": str(self.dir)},
                             runner=lambda x, **kw: second_runs.append(x.id) or "ok")
        self.assertTrue(d1.exclusive, "first dispatcher must own its board")
        self.assertFalse(d2.exclusive, "second dispatcher must notice the owner")
        self.assertFalse(d2.dispatch(t.id), "inert dispatcher accepted a dispatch")
        d2.pump_queue()
        d2.adopt_orphans()
        d2.sweep_handovers()
        time.sleep(0.3)
        after = {x.id: x for x in store.load_tickets(self.dir)}[t.id]
        self.assertEqual(after.status, "zu_bearbeiten",
                         "inert dispatcher moved a ticket")
        self.assertEqual(second_runs, [], "inert dispatcher started a run")
        # The owner still works normally next to it.
        d1.pump_queue()
        self.assertTrue(wait_until(lambda: first_runs == [t.id]),
                        "owning dispatcher stopped working")

    def test_stop_releases_the_board_to_a_successor(self):
        d1 = make_dispatcher(self, self.dir, cfg={"queue_poll_seconds": 3600},
                             runner=lambda x, **kw: "ok")
        d1.stop()
        d2 = make_dispatcher(self, self.dir, cfg={"queue_poll_seconds": 3600},
                             runner=lambda x, **kw: "ok")
        self.assertTrue(d2.exclusive,
                        "lock not released on stop — restarts would go inert")

    def test_boards_on_different_dirs_are_independent(self):
        other = temp_dir()
        self.addCleanup(shutil.rmtree, other, True)
        d1 = make_dispatcher(self, self.dir, cfg={"queue_poll_seconds": 3600},
                             runner=lambda x, **kw: "ok")
        d2 = make_dispatcher(self, other, cfg={"queue_poll_seconds": 3600},
                             runner=lambda x, **kw: "ok")
        self.assertTrue(d1.exclusive)
        self.assertTrue(d2.exclusive)


@linux_only
class Wb135LaneSelfHealTest(unittest.TestCase):
    """WB-135 root cause, end to end. Proven mechanism (commit 354f76e,
    18:06:59: the run's own first-person report committed to the ticket while
    its process pair demonstrably lived until 18:09): the agent INSIDE an
    opencode run finalizes its own ticket through the store, but the process
    does not exit — communicate() never returns, the lane worker stays
    hostage, the queue stalls until a human kills the pair (WB-106: 5 min,
    WB-107: 6 min observed). With ticket-stamped runs the ticker's reaper now
    self-heals this: the run whose ticket moved on is killed, the worker
    returns, the finalize guard keeps the agent's own result, the lane frees
    and the next ticket starts — no human involved."""

    def setUp(self):
        from werkbank import opencode as oc
        self.oc = oc
        self.dir = temp_dir()
        self.project = temp_dir()
        self._old_task = oc.OPENCODE_TASK

    def tearDown(self):
        self.oc.OPENCODE_TASK = self._old_task
        for pidfile in self.dir.glob("standin.*.pid"):
            try:
                os.killpg(int(pidfile.read_text(encoding="utf-8")), 9)
            except (OSError, ValueError):
                pass
        remove_tree(self.dir)
        remove_tree(self.project)

    def _skip_if_external_reaper(self, why):
        """A board running the UNSCOPED round-1 reaper (pre-8b0eb6b) SIGTERMs
        ANY marked process on the machine within one tick — measured live on
        2026-08-16 (foreign-stamped sleep killed after 2.0 s). Under such an
        environment this test's stand-ins are shot from outside and the
        result says nothing about the code. Probe with a canary that ONLY an
        unscoped reaper would touch (foreign board stamp); if it dies, skip
        with instructions instead of failing falsely."""
        env = dict(os.environ)
        env["WERKBANK_TICKET_ID"] = "WB-0-umgebungs-kanarie"
        env["WERKBANK_TICKETS_DIR"] = "/tmp/werkbank-kanarie-fremdes-board"
        canary = subprocess.Popen(sleeper_command(30), env=env)
        try:
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                if canary.poll() is not None:
                    self.skipTest(
                        "Ein Board mit dem UNGESCOPTEN Reaper (Stand vor "
                        "8b0eb6b) läuft auf dieser Maschine und erschießt "
                        "markierte Testprozesse — Board neu starten, dann "
                        f"gilt dieser Test wieder. (Auslöser: {why})")
                time.sleep(0.25)
        finally:
            if canary.poll() is None:
                canary.kill()
                canary.wait()

    def test_self_finalized_run_is_reaped_and_the_lane_moves_on(self):
        src = str(Path(__file__).resolve().parent.parent / "src")
        t1 = store.create_ticket(self.dir, title="erstes", description="",
                                 assignee="opencode", project=str(self.project))
        t2 = store.create_ticket(self.dir, title="zweites", description="",
                                 assignee="opencode", project=str(self.project))
        store.update_ticket(self.dir, t1.id, {"status": "in_arbeit",
                                              "review": "nein"})
        store.update_ticket(self.dir, t2.id, {"status": "zu_bearbeiten",
                                              "review": "nein"})
        standin = self.dir / "opencode-task"
        standin.write_text(
            "#!/bin/bash\n"
            "cat > /dev/null\n"
            f"echo $$ > {self.dir}/standin.$$.pid\n"
            "python3 - <<'PYEOF'\n"
            "import sys\n"
            f"sys.path.insert(0, {src!r})\n"
            "from werkbank import store\n"
            f"store.set_result({str(self.dir)!r}, {t1.id!r},\n"
            "                 'Eigenbericht des Agenten (wie WB-106).')\n"
            f"store.update_ticket({str(self.dir)!r}, {t1.id!r},\n"
            "                    {'status': 'review'})\n"
            "PYEOF\n"
            "sleep 600\n")
        standin.chmod(0o755)
        self.oc.OPENCODE_TASK = str(standin)
        cfg = {"default_project": str(self.project),
               "gates": {str(self.project): {"standard": "true"}},
               "queue_poll_seconds": 0.3,
               "nonblocking_review": {str(self.project): True}}
        d = make_dispatcher(self, self.dir, cfg=cfg)
        d.dispatch(t1.id)

        # The stand-in's pid file appears at spawn — capture t1's run BEFORE
        # anything can be reaped, so the death assert below provably targets
        # the FIRST run (t2's later run writes a second, different pid file).
        self.assertTrue(wait_until(
            lambda: list(self.dir.glob("standin.*.pid"))),
            "t1's stand-in never started — setup broken")
        t1_pids = [int(p.read_text(encoding="utf-8"))
                   for p in self.dir.glob("standin.*.pid")]
        # The agent finalizes its own ticket while its process keeps living…
        self.assertTrue(wait_until(
            lambda: {x.id: x for x in
                     store.load_tickets(self.dir)}[t1.id].status == "review"),
            "stand-in never finalized its own ticket — setup broken")
        # …and WITHOUT the reaper this held the lane for minutes (WB-106: 5,
        # WB-107: 6, until a human killed the pair). Now the run must die.
        # NOTE deliberately NOT asserted: `_pending == set()`. The lane's
        # hand-off is atomic from the queue's point of view — the worker's
        # finally block frees WB-1 and its pump immediately starts WB-2, so
        # the empty set exists only for microseconds. Asserting it made the
        # test fail exactly when the code worked (measured 2026-08-16); the
        # meaningful observables are: the hostage processes die, and the
        # NEXT ticket actually starts.
        self.assertTrue(wait_until(
            lambda: all(not dispatch._is_running(p) for p in t1_pids),
            timeout=20.0),
            f"self-finalized run still alive ({t1_pids}) — lane hostage, "
            "WB-135 regression")
        trace = []
        deadline = time.monotonic() + 20.0
        started = False
        while time.monotonic() < deadline:
            s = {x.id: x for x in store.load_tickets(self.dir)}[t2.id].status
            if not trace or trace[-1][1] != s:
                trace.append((round(time.monotonic() - deadline + 20.0, 1), s))
            if s == "in_arbeit":
                started = True
                break
            time.sleep(0.02)
        if not started:
            self._skip_if_external_reaper("t2 startete nie bzw. wurde sofort "
                                          "abgeräumt")
        self.assertTrue(started,
                        f"next ticket never started — queue still stalled; "
                        f"t2 trace={trace!r} pending={d._pending['opencode']!r}")
        # …and the successor is NOT shot at birth (reaper round 2: the find's
        # ticket snapshot predates the /proc scan, so the fresh t2 run used
        # to be judged by its stale zu_bearbeiten and killed within a tick —
        # it must still be alive and in_arbeit a few ticks later).
        time.sleep(1.5)
        after2 = {x.id: x for x in store.load_tickets(self.dir)}[t2.id]
        if after2.status != "in_arbeit":
            self._skip_if_external_reaper("t2 wurde nach dem Start beendet")
        self.assertEqual(after2.status, "in_arbeit",
                         "fresh run was reaped at birth — snapshot race")
        after = {x.id: x for x in store.load_tickets(self.dir)}[t1.id]
        self.assertEqual(after.status, "review")
        self.assertIn("Eigenbericht des Agenten", after.body,
                      "returning worker overwrote the agent's own result")


class DeadRememberedSessionFallsBackTest(unittest.TestCase):
    """Observed live on 2026-08-16: EVERY dispatched ticket failed instantly with
    'error_during_execution'. The log showed the cause — the remembered ticket
    session could not be resumed:

        --resume 38b23f9e-…  ->  "No conversation found with session ID: 38b23f9e-…"

    The fallback chain (resume -> continue -> fresh) exists for exactly this.
    But the CLI reports it as a RESULT event, and every result error ended the
    run immediately, so one dead session id failed every ticket of that project
    forever. The board was healthy; it just refused to try the next rung."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        self.state.write_text(json.dumps({str(self.dir): "tote-session"}) + "\n",
                              encoding="utf-8")
        self.t = store.Ticket(id="WB-1", title="T", status="in_arbeit",
                              project=str(self.dir),
                              body="## Beschreibung\n\nx\n\n## Ergebnis\n\n_(offen)_\n")

    def tearDown(self):
        remove_tree(self.dir)

    def _stub(self):
        """Fails the way the real CLI does when --resume names a dead session,
        and succeeds on any attempt that does not resume."""
        body = (
            'case "$*" in\n'
            '  *--resume*)\n'
            "    echo '{\"type\":\"result\",\"subtype\":\"error_during_execution\","
            "\"is_error\":true,\"errors\":[\"No conversation found with session ID: "
            "tote-session\"]}'\n"
            '    echo "No conversation found with session ID: tote-session" >&2\n'
            "    exit 1;;\n"
            '  *)\n'
            "    echo '{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"neu-1\"}'\n"
            "    echo '{\"type\":\"result\",\"subtype\":\"success\",\"result\":\"fertig\","
            "\"session_id\":\"neu-1\"}';;\n"
            "esac\n")
        p = sh_stub(self.dir, "fake-claude", body)
        return {"claude_bin": p, "state_path": str(self.state),
                "agent_timeout_minutes": 1, "exit_grace_seconds": 1}

    def test_a_dead_session_falls_back_to_a_fresh_run(self):
        result, session = dispatch.run_claude(self.t, self._stub())
        self.assertEqual(result, "fertig")
        self.assertEqual(session, "neu-1")

    def test_the_dead_id_is_forgotten_so_it_cannot_repeat(self):
        dispatch.run_claude(self.t, self._stub())
        remembered = dispatch.load_last_session(str(self.dir), self.state)
        self.assertNotEqual(remembered, "tote-session",
                            "a session that cannot be resumed must not stay remembered")

    def test_other_result_errors_still_fail(self):
        """Only the unresumable-session case falls through — a real agent error
        must still land in Fehlgeschlagen."""
        p = sh_stub(self.dir, "fake-claude",
                    "echo '{\"type\":\"result\",\"subtype\":\"error_during_execution\","
                    "\"is_error\":true,\"result\":\"irgendwas ging schief\"}'\n")
        with self.assertRaises(dispatch.DispatchError):
            dispatch.run_claude(self.t, {"claude_bin": str(p),
                                         "state_path": str(self.state),
                                         "agent_timeout_minutes": 1})

    def test_forget_session_leaves_other_projects_alone(self):
        self.state.write_text(json.dumps({str(self.dir): "tot", "/anderes": "heil"}),
                              encoding="utf-8")
        dispatch.forget_session(str(self.dir), self.state)
        data = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(data, {"/anderes": "heil"})


class Wb181ChatClaimIsRespectedTest(unittest.TestCase):
    """Observed live 2026-08-17, reported by the user as "das Ticket hat sich
    nicht im Board bewegt":

    A chat session claims a ticket exactly as its skill prescribes and starts
    working. The board sees an in_arbeit ticket with no RUN attached, decides it
    is stranded, hands it to a chat session — and when that handover deadline
    passes, puts the ticket BACK INTO OFFEN while the session is still working.
    Measured: in_arbeit at 0 s, back to offen at 200 s, result "Ticket wartet
    auf eine Chat-Session".

    The old guard asked the state file whether the session was "interactive" —
    true only after the session REGISTERS, which its skill does when it
    FINISHES. So the guard could not cover the window it existed for."""

    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(self.dir, title="Chat arbeitet dran",
                                     description="x", project=str(self.dir))

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self, **cfg):
        base = {"default_project": str(self.dir),
                "state_path": str(self.dir / "state.json")}
        base.update(cfg)
        self.started = []
        return make_dispatcher(
            self, self.dir, cfg=base,
            runner=lambda tk, **kw: (self.started.append(tk.id), ("ok", "s"))[1])

    def test_a_freshly_claimed_ticket_is_not_adopted(self):
        store.claim_ticket(self.dir, self.t.id, "chat-session-1")
        d = self._dispatcher()
        d.adopt_orphans()
        d.join(timeout=5)
        self.assertEqual(self.started, [], "the board took a ticket a chat holds")
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.status, "in_arbeit")

    def test_claim_records_who_and_when(self):
        store.claim_ticket(self.dir, self.t.id, "chat-session-1")
        t = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(t.session, "chat-session-1")
        self.assertTrue(int(t.claimed_at) > 0)
        self.assertEqual(t.status, "in_arbeit")

    def test_claiming_answers_a_handover(self):
        """A handed-over ticket is claimed by clearing the marker — otherwise
        the deadline sweeps it away from the session that just took it."""
        store.update_ticket(self.dir, self.t.id,
                            {"status": "in_arbeit", "handover": "chat-session-1",
                             "handover_at": str(int(time.time()))})
        store.claim_ticket(self.dir, self.t.id, "chat-session-1")
        t = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual((t.handover, t.handover_at), ("", ""))

    def test_a_stale_claim_is_still_adopted(self):
        """The guard must not strand a ticket forever when the chat is gone."""
        store.claim_ticket(self.dir, self.t.id, "chat-session-1")
        store.update_ticket(self.dir, self.t.id,
                            {"claimed_at": str(int(time.time()) - 7200)})
        d = self._dispatcher(chat_claim_minutes=60)
        d.adopt_orphans()
        wait_until(lambda: self.started == [self.t.id])
        self.assertEqual(self.started, [self.t.id])

    def test_an_empty_session_is_refused(self):
        with self.assertRaises(ValueError):
            store.claim_ticket(self.dir, self.t.id, "")


class Wb181StartupSweepRespectsClaimTest(unittest.TestCase):
    """The startup sweep had the same blind spot as adopt_orphans: it asked the
    state file whether a session is interactive. A chat that claimed a ticket
    but has not registered yet — which is the normal state WHILE it works —
    would have its ticket marked fehlgeschlagen by the next board restart."""

    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(self.dir, title="Chat haelt es",
                                     description="x", project=str(self.dir))

    def tearDown(self):
        remove_tree(self.dir)

    def test_a_fresh_claim_survives_a_board_restart(self):
        store.claim_ticket(self.dir, self.t.id, "chat-nicht-registriert")
        swept = dispatch.sweep_orphaned(self.dir, str(self.dir / "state.json"))
        self.assertEqual(swept, [])
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.status, "in_arbeit")

    def test_a_stale_claim_is_still_swept(self):
        store.claim_ticket(self.dir, self.t.id, "chat-weg")
        store.update_ticket(self.dir, self.t.id,
                            {"claimed_at": str(int(time.time()) - 7200)})
        swept = dispatch.sweep_orphaned(self.dir, str(self.dir / "state.json"))
        self.assertEqual(swept, [self.t.id])


class Wb183OrderWithinAProjectTest(unittest.TestCase):
    """WB-146 gave every claude ticket its own thread so different projects
    could run in parallel. Two tickets of the SAME project then raced for the
    project lock, and whoever won ran first — observed as
    `['WB-2', 'WB-1'] != ['WB-1', 'WB-2']` in roughly one suite run in three.

    Order is not cosmetic here: the board sorts by priority and ticket number
    and even offers a "move to front" button. If the order a user sets does not
    survive dispatch, that button is decoration and nobody can tell."""

    def setUp(self):
        self.dir = temp_dir()
        self.a = str(self.dir / "projekt-a")
        self.b = str(self.dir / "projekt-b")
        for p in (self.a, self.b):
            Path(p).mkdir()
        self.seen = []
        self.threads = {}
        self.lock = threading.Lock()

    def tearDown(self):
        remove_tree(self.dir)

    def _runner(self, t, **kw):
        # a little work, so a racing implementation has room to overtake
        time.sleep(0.03)
        with self.lock:
            self.seen.append(t.id)
            self.threads[t.id] = threading.current_thread().name
        return (f"fertig {t.id}", "s")

    def _dispatcher(self):
        return make_dispatcher(
            self, self.dir,
            cfg={"default_project": self.a, "state_path": str(self.dir / "s.json")},
            runner=self._runner)

    def _queue(self, project, count):
        ids = []
        for i in range(count):
            t = store.create_ticket(self.dir, title=f"T{i}", description="x",
                                    project=project)
            store.update_ticket(self.dir, t.id, {"status": "in_arbeit"})
            ids.append(t.id)
        return ids

    def test_same_project_runs_in_the_order_they_were_queued(self):
        ids = self._queue(self.a, 6)
        d = self._dispatcher()
        for i in ids:
            d.dispatch(i)
        wait_until(lambda: len(self.seen) == len(ids), timeout=20)
        self.assertEqual(self.seen, ids)

    def test_one_worker_per_project_is_what_guarantees_it(self):
        """The property, not just the symptom: a single consumer per project.
        Before the fix each run got a fresh thread, so order could only ever be
        luck."""
        ids = self._queue(self.a, 4)
        d = self._dispatcher()
        for i in ids:
            d.dispatch(i)
        wait_until(lambda: len(self.seen) == len(ids), timeout=20)
        self.assertEqual(len(set(self.threads.values())), 1,
                         f"more than one worker touched one project: {self.threads}")

    def test_different_projects_still_run_in_parallel(self):
        """WB-146's gain must survive: separate projects, separate workers."""
        first = self._queue(self.a, 2)
        second = self._queue(self.b, 2)
        d = self._dispatcher()
        for i in first + second:
            d.dispatch(i)
        wait_until(lambda: len(self.seen) == 4, timeout=20)
        names = {self.threads[i] for i in first} | {self.threads[i] for i in second}
        self.assertEqual(len(names), 2, f"projects did not get own workers: {names}")
        # and each project kept its own order
        self.assertEqual([i for i in self.seen if i in first], first)
        self.assertEqual([i for i in self.seen if i in second], second)


class Wb184FreshInstallCanDispatchTest(unittest.TestCase):
    """Found by the fresh-machine test (WB-184), and it hit the very first
    thing a new user does.

    On a fresh install `tickets/` does not exist. If anything constructs the
    Dispatcher before the first ticket is created — opening the board does
    exactly that, since the page asks for the ticket list — the lock file
    cannot be opened inside a missing directory, `try_exclusive` returns None,
    and the dispatcher marks itself NOT exclusive for the rest of the process.
    After that it refuses every dispatch silently: the user drags a ticket to
    In Arbeit, the card moves, and nothing ever runs. No error, anywhere."""

    def setUp(self):
        self.dir = temp_dir()
        self.tickets = self.dir / "tickets"      # deliberately absent

    def tearDown(self):
        remove_tree(self.dir)

    def test_dispatcher_on_a_missing_tickets_dir_is_still_this_board(self):
        d = make_dispatcher(self, self.tickets,
                            cfg={"default_project": str(self.dir),
                                 "state_path": str(self.dir / "s.json")},
                            runner=lambda t, **kw: ("ok", "s"))
        self.assertTrue(d.exclusive,
                        "a board that just created its own tickets dir is the board")
        self.assertTrue(self.tickets.exists(), "the board owns this directory")

    def test_and_it_really_runs_the_first_ticket(self):
        started = []
        d = make_dispatcher(self, self.tickets,
                            cfg={"default_project": str(self.dir),
                                 "state_path": str(self.dir / "s.json")},
                            runner=lambda t, **kw: (started.append(t.id), ("ok", "s"))[1])
        t = store.create_ticket(self.tickets, title="Erstes", description="x",
                                project=str(self.dir))
        store.update_ticket(self.tickets, t.id, {"status": "in_arbeit"})
        self.assertTrue(d.dispatch(t.id), "dispatch refused on a fresh install")
        wait_until(lambda: started == [t.id])
        self.assertEqual(started, [t.id])


class Wb184CredentialsMustReachTheRunTest(unittest.TestCase):
    """Found by an adversarial cross-platform review. The per-project Claude
    config dir (on by default) symlinks the real credentials file into it.
    Windows refuses symlinks to a normal user, the failure was swallowed, and
    the run was then pointed at a config dir with NO credentials — every
    dispatch failing with 'bitte neu anmelden', on the default path, with no
    hint about the cause. A shared config dir costs parallelism; not working at
    all costs everything."""

    def setUp(self):
        self.dir = temp_dir()
        self.home = self.dir / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.cfg = {"claude_config_root": str(self.dir / "cfgroot")}

    def tearDown(self):
        remove_tree(self.dir)

    def _with_home(self, fn):
        import unittest.mock as mock
        with mock.patch.object(Path, "home", staticmethod(lambda: self.home)):
            return fn()

    def test_without_a_credentials_file_the_per_project_dir_is_fine(self):
        """macOS keeps the login in the Keychain — there is no file to link,
        and that is not a failure."""
        d = self._with_home(lambda: dispatch.claude_config_dir_for("/p", self.cfg))
        self.assertIsNotNone(d)
        self.assertTrue(Path(d).is_dir())

    def test_a_symlink_that_cannot_be_made_falls_back(self):
        (self.home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
        import unittest.mock as mock

        def refuse(*a, **kw):
            raise OSError(1, "symlink not permitted")

        with mock.patch.object(os, "symlink", refuse):
            d = self._with_home(lambda: dispatch.claude_config_dir_for("/p", self.cfg))
        self.assertIsNone(d, "without credentials the run must use the shared dir")

    def test_a_working_symlink_keeps_the_per_project_dir(self):
        (self.home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
        d = self._with_home(lambda: dispatch.claude_config_dir_for("/p", self.cfg))
        self.assertIsNotNone(d)
        self.assertTrue((Path(d) / ".credentials.json").exists())


class Wb184ZombieCountsAsDeadEverywhereTest(unittest.TestCase):
    """macOS was the last red CI job, and the instrumented assertion gave the
    fact: the orphan WAS killed, but `_is_running` still said "alive", so the
    ticket reported "the board restarted and lost the run" instead of naming
    the process it had just ended. Cause: the zombie check reads /proc, which
    macOS does not have, and a killed child stays a zombie until its parent
    reaps it — `os.kill(pid, 0)` happily succeeds for a zombie."""

    @posix_only
    def test_a_zombie_is_not_running(self):
        proc = subprocess.Popen(sleeper_command(30))
        proc.terminate()
        # do NOT wait(): without reaping, the process stays a zombie — exactly
        # the state the check has to see through.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and dispatch._is_running(proc.pid):
            time.sleep(0.05)
        self.assertFalse(dispatch._is_running(proc.pid),
                         "a terminated-but-unreaped child must count as dead")
        proc.wait(timeout=5)

    @posix_only
    def test_a_live_process_is_running(self):
        proc = subprocess.Popen(sleeper_command(30))
        try:
            self.assertTrue(dispatch._is_running(proc.pid))
        finally:
            proc.kill()
            proc.wait(timeout=5)


class Wb258DirectHandoverDeliveryTest(unittest.TestCase):
    """WB-258: at handover time the dispatcher tries to poke the chat session
    directly via its messaging socket. A dead / missing session short-circuits
    the 5-minute chat_handover_minutes wait — the board goes straight to a
    background run. Any other outcome (delivered, wrong protocol, error) still
    writes the marker + arms the fallback (audit trail + safety net)."""

    def setUp(self):
        self.dir = temp_dir()
        self.state = self.dir / "state.json"
        dispatch.register_ticket_session(str(self.dir), "chat-258", self.state)
        self.t = store.create_ticket(self.dir, title="Uebergabe direkt",
                                     description="")
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        self.calls = []

    def tearDown(self):
        remove_tree(self.dir)

    def _dispatcher(self):
        def runner(t, on_start=None):
            self.calls.append(t.id)
            return "hintergrund", "sess-bg"
        return make_dispatcher(self,
            self.dir, cfg={"state_path": str(self.state),
                           "default_project": str(self.dir),
                           "chat_handover_minutes": 10},
            runner=runner)

    def _load(self):
        return {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]

    def _patch_delivery(self, result):
        from unittest import mock
        patcher = mock.patch.object(dispatch.messaging, "deliver",
                                    return_value=result)
        m = patcher.start()
        self.addCleanup(patcher.stop)
        return m

    def test_delivered_writes_marker_and_skips_background(self):
        self._patch_delivery(dispatch.messaging.DeliveryResult.DELIVERED)
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [], "background runner must not fire")
        after = self._load()
        self.assertEqual(after.handover, "chat-258")
        self.assertEqual(after.status, "in_arbeit")

    def test_dead_socket_skips_marker_and_runs_background_now(self):
        self._patch_delivery(dispatch.messaging.DeliveryResult.DEAD_SOCKET)
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [self.t.id],
                         "dead session must not wait — background runs at once")
        after = self._load()
        self.assertEqual(after.handover, "", "no marker for a dead chat")

    def test_no_session_file_skips_marker_and_runs_background_now(self):
        self._patch_delivery(dispatch.messaging.DeliveryResult.NO_SESSION_FILE)
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [self.t.id])
        after = self._load()
        self.assertEqual(after.handover, "")

    def test_wrong_protocol_falls_back_to_marker_path(self):
        self._patch_delivery(dispatch.messaging.DeliveryResult.WRONG_PROTOCOL)
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        self.assertEqual(self.calls, [])
        after = self._load()
        self.assertEqual(after.handover, "chat-258")

    def test_delivery_attempt_is_logged(self):
        self._patch_delivery(dispatch.messaging.DeliveryResult.DEAD_SOCKET)
        d = self._dispatcher()
        d.dispatch(self.t.id)
        d.join(timeout=5)
        log = self.state.parent / "handovers.jsonl"
        self.assertTrue(log.exists(), "handovers.jsonl must be written")
        entries = [json.loads(line) for line in
                   log.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["ticket"], self.t.id)
        self.assertEqual(entries[0]["session"], "chat-258")
        self.assertEqual(entries[0]["result"], "dead_socket")
        self.assertEqual(entries[0]["action"], "background")


class Wb258MessagingModuleTest(unittest.TestCase):
    """WB-258: the messaging module reads the user's .claude/sessions directory*.json to find
    the socket, gates on peerProtocol == 1, and reports every failure as a
    value instead of raising."""

    def setUp(self):
        from werkbank import messaging
        self.messaging = messaging
        self.sessions = temp_dir()

    def tearDown(self):
        remove_tree(self.sessions)

    def _write_session(self, name, session_id, socket_path, protocol=1):
        (self.sessions / f"{name}.json").write_text(json.dumps({
            "pid": 4242, "sessionId": session_id,
            "messagingSocketPath": str(socket_path),
            "peerProtocol": protocol,
        }), encoding="utf-8")

    def test_find_session_matches_by_id(self):
        self._write_session("3.json", "sid-A", "/tmp/does-not-matter.sock")
        found = self.messaging.find_session("sid-A", self.sessions)
        self.assertEqual(found, ("/tmp/does-not-matter.sock", 1))

    def test_find_session_returns_none_when_no_match(self):
        self._write_session("3.json", "sid-A", "/tmp/x.sock")
        self.assertIsNone(self.messaging.find_session("sid-Z", self.sessions))

    def test_find_session_ignores_malformed_files(self):
        (self.sessions / "bad.json").write_text("not json", encoding="utf-8")
        self._write_session("3.json", "sid-A", "/tmp/x.sock")
        self.assertEqual(self.messaging.find_session("sid-A", self.sessions),
                         ("/tmp/x.sock", 1))

    def test_deliver_no_session_file(self):
        self.assertEqual(
            self.messaging.deliver("nobody", "hi", sessions_dir=self.sessions),
            self.messaging.DeliveryResult.NO_SESSION_FILE)

    def test_deliver_wrong_protocol(self):
        self._write_session("3.json", "sid-A", "/tmp/x.sock", protocol=2)
        self.assertEqual(
            self.messaging.deliver("sid-A", "hi", sessions_dir=self.sessions),
            self.messaging.DeliveryResult.WRONG_PROTOCOL)

    def test_deliver_dead_socket(self):
        # A path that does not exist as a socket → connect fails with
        # FileNotFoundError, which the module treats as DEAD_SOCKET.
        sock_path = self.sessions / "not-a-socket.sock"
        self._write_session("3.json", "sid-A", sock_path)
        self.assertEqual(
            self.messaging.deliver("sid-A", "hi", sessions_dir=self.sessions),
            self.messaging.DeliveryResult.DEAD_SOCKET)

    @posix_only
    def test_deliver_writes_json_line_to_a_live_socket(self):
        import socket as _s
        sock_path = str(self.sessions / "live.sock")
        server = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)
        self.addCleanup(server.close)
        self._write_session("3.json", "sid-A", sock_path)

        received = []

        def accept():
            conn, _ = server.accept()
            with conn:
                received.append(conn.recv(64 * 1024))

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        result = self.messaging.deliver("sid-A", "hallo",
                                        sessions_dir=self.sessions)
        thread.join(timeout=2)
        self.assertEqual(result, self.messaging.DeliveryResult.DELIVERED)
        self.assertTrue(received, "server never received bytes")
        payload = json.loads(received[0].decode("utf-8").rstrip("\n"))
        self.assertEqual(payload["msgV"], 1)
        self.assertEqual(payload["type"], "user")
        self.assertEqual(payload["priority"], "next")
        self.assertEqual(payload["from"], "werkbank")
        self.assertIn("cross-session-message", payload["message"]["content"])
        self.assertIn("hallo", payload["message"]["content"])


class Wb252DefaultAssigneeIsClaudeTest(unittest.TestCase):
    """WB-252: the create-dialog default is claude, regardless of whether the
    project has a gate configured. Reverses WB-228's gate-aware dsh default —
    the owner's call on 2026-08-20 („claude ist der goto Agent")."""

    def setUp(self):
        self.board = (Path(__file__).resolve().parent.parent
                      / "src/werkbank/board.html").read_text(encoding="utf-8")

    def test_defaultAssignee_returns_claude_unconditionally(self):
        # The function body must not branch on gateNames anymore — a return
        # of "claude" full stop pins the current owner decision.
        self.assertRegex(self.board,
            r'function\s+defaultAssignee\s*\(\s*project\s*\)\s*\{\s*'
            r'return\s+"claude"\s*;\s*\}')
        # Belt and suspenders: no residual dsh branch in the function.
        self.assertNotRegex(self.board,
            r'function\s+defaultAssignee[\s\S]{0,200}gateNames\s*\(')
