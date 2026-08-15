import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import dispatch, store


class SlugTest(unittest.TestCase):
    def test_project_slug_matches_claude_projects_layout(self):
        self.assertEqual(
            dispatch.project_slug("/home/user/code/werkbank"),
            "-home-user-code-werkbank",
        )

    def test_has_history_true_only_with_jsonl(self):
        root = Path(tempfile.mkdtemp())
        try:
            self.assertFalse(dispatch.project_has_history("/some/proj", root))
            d = root / "-some-proj"
            d.mkdir()
            self.assertFalse(dispatch.project_has_history("/some/proj", root))
            (d / "abc.jsonl").write_text("{}")
            self.assertTrue(dispatch.project_has_history("/some/proj", root))
        finally:
            shutil.rmtree(root)


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
        self.assertNotIn("Regressionstest", dispatch.build_prompt(self.ticket))
        bug = store.Ticket(id="WB-9", title="Kaputt", type="bug", body="## Beschreibung\n\nX\n")
        prompt = dispatch.build_prompt(bug)
        self.assertIn("Bug-Ticket", prompt)
        self.assertIn("Regressionstest", prompt)


class SessionStateTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / "state.json"

    def tearDown(self):
        shutil.rmtree(self.dir)

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
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / "state.json"

    def tearDown(self):
        shutil.rmtree(self.dir)

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
        self.dir = Path(tempfile.mkdtemp())
        self.project = self.dir / "proj"
        self.project.mkdir()
        self.state = self.dir / "state.json"
        self.bin = self.dir / "fake-claude"
        self.ticket = store.Ticket(id="WB-99", title="Test", project=str(self.project),
                                   fork="nein")
        self.cfg = {"claude_bin": str(self.bin), "state_path": str(self.state),
                    "agent_timeout_minutes": 1}

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _write_fake_claude(self, script: str):
        self.bin.write_text("#!/bin/sh\n" + script, encoding="utf-8")
        os.chmod(self.bin, 0o755)

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
        self.dir = Path(tempfile.mkdtemp())
        self.project = self.dir / "proj"
        self.project.mkdir()
        self.state = self.dir / "state.json"
        self.bin = self.dir / "fake-claude"
        self.ticket = store.Ticket(id="WB-99", title="Test", project=str(self.project))
        self.cfg = {"claude_bin": str(self.bin), "state_path": str(self.state),
                    "agent_timeout_minutes": 1}

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _write_fake_claude(self, script: str):
        self.bin.write_text("#!/bin/sh\n" + script, encoding="utf-8")
        os.chmod(self.bin, 0o755)

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
        self.dir = Path(tempfile.mkdtemp())
        self.blocker = store.create_ticket(self.dir, title="Blocker", description="")
        self.dep = store.create_ticket(self.dir, title="Abhängig", description="",
                                       nach=self.blocker.id)

    def tearDown(self):
        shutil.rmtree(self.dir)

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
        d = dispatch.Dispatcher(self.dir, runner=lambda t: calls.append(t.id) or "lief")
        d.dispatch(self.dep.id)
        d.join(timeout=5)
        self.assertEqual(calls, [])
        dep = {x.id: x for x in store.load_tickets(self.dir)}[self.dep.id]
        self.assertEqual(dep.status, "offen")
        self.assertIn("Nicht gestartet", dep.body)
        self.assertIn(self.blocker.id, dep.body)


class DispatcherTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.t1 = store.create_ticket(self.dir, title="Eins", description="")
        self.t2 = store.create_ticket(self.dir, title="Zwei", description="")
        store.update_ticket(self.dir, self.t1.id, {"status": "in_arbeit"})
        store.update_ticket(self.dir, self.t2.id, {"status": "in_arbeit"})

    def tearDown(self):
        shutil.rmtree(self.dir)

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

        d = dispatch.Dispatcher(self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.dispatch(self.t2.id)
        d.join(timeout=5)
        self.assertEqual(seen, ["WB-1", "WB-2"])
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(loaded["WB-1"].status, "review")
        self.assertIn("Ergebnis für WB-1", loaded["WB-1"].body)

    def test_duplicate_dispatch_ignored_while_pending(self):
        calls = []

        def runner(ticket):
            time.sleep(0.05)
            calls.append(ticket.id)
            return "ok"

        d = dispatch.Dispatcher(self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.dispatch(self.t1.id)
        d.join(timeout=5)
        self.assertEqual(calls, ["WB-1"])

    def test_runner_failure_lands_in_fehlgeschlagen_with_reason(self):
        def runner(ticket):
            raise dispatch.DispatchError("kein claude gefunden")

        d = dispatch.Dispatcher(self.dir, runner=runner)
        d.dispatch(self.t1.id)
        d.join(timeout=5)
        loaded = {x.id: x for x in store.load_tickets(self.dir)}
        self.assertEqual(loaded["WB-1"].status, "fehlgeschlagen")
        self.assertIn("kein claude gefunden", loaded["WB-1"].body)

    def test_internal_error_lands_in_fehlgeschlagen(self):
        def runner(ticket):
            raise RuntimeError("völlig unerwartet")

        d = dispatch.Dispatcher(self.dir, runner=runner)
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
        self.dir = Path(tempfile.mkdtemp())
        self.open_t = store.create_ticket(self.dir, title="Offen bleibt", description="")
        self.orphan = store.create_ticket(self.dir, title="Verwaist", description="")
        self.review_t = store.create_ticket(self.dir, title="Review bleibt", description="")
        store.update_ticket(self.dir, self.orphan.id, {"status": "in_arbeit"})
        store.update_ticket(self.dir, self.review_t.id, {"status": "in_arbeit"})
        store.update_ticket(self.dir, self.review_t.id, {"status": "review"})

    def tearDown(self):
        shutil.rmtree(self.dir)

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


class NoRestartRuleTest(unittest.TestCase):
    def test_prompt_forbids_board_restart(self):
        t = store.Ticket(id="WB-99", title="X", body="## Beschreibung\n\nY\n\n## Ergebnis\n\n_(noch offen)_\n")
        self.assertIn("niemals das Werkbank-Board neu", dispatch.build_prompt(t))


class RunVisibilityTest(unittest.TestCase):
    """WB-20: while a run is active the dispatcher publishes what it knows;
    afterwards the run's real session id is persisted into the ticket."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.t = store.create_ticket(self.dir, title="Sichtbar", description="")
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_active_run_is_published_and_cleared(self):
        started = threading.Event()
        release = threading.Event()

        def runner(t, on_start=None):
            if on_start:
                on_start({"parent": "eltern-123", "forked": True})
            started.set()
            release.wait(5)
            return "fertig", "sess-456"

        d = dispatch.Dispatcher(self.dir, runner=runner)
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

        d = dispatch.Dispatcher(self.dir, runner=runner)
        d.dispatch(self.t.id)
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.status, "review")
        self.assertEqual(after.session, "sess-789")

    def test_plain_string_runner_still_works(self):
        d = dispatch.Dispatcher(self.dir, runner=lambda t, on_start=None: "nur text")
        d.dispatch(self.t.id)
        d.join(timeout=5)
        after = {x.id: x for x in store.load_tickets(self.dir)}[self.t.id]
        self.assertEqual(after.status, "review")
        self.assertEqual(after.session, "")

    def test_run_claude_reports_start_and_returns_session(self):
        stub = self.dir / "fake-claude"
        stub.write_text("#!/bin/sh\necho '{\"result\": \"ok\", \"session_id\": \"s-neu\"}'\n")
        stub.chmod(0o755)
        state = self.dir / "state.json"
        state.write_text('{"%s": "eltern-abc"}' % self.dir)
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
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / "state.json"
        self.t = store.create_ticket(self.dir, title="Uebergabe", description="")
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _dispatcher(self, timeout_min):
        def runner(t, on_start=None):
            self.calls.append(t.id)
            return "hintergrund", "sess-bg"
        return dispatch.Dispatcher(
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
        base = Path(tempfile.mkdtemp())
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
            d = dispatch.Dispatcher(
                tickets_dir, cfg={"state_path": str(state),
                                  "default_project": str(proj_a),
                                  "chat_handover_minutes": 10},
                runner=lambda tk, on_start=None: ("nie", None))
            d.dispatch(t.id)
            d.join(timeout=5)
            after = {x.id: x for x in store.load_tickets(tickets_dir)}[t.id]
            self.assertEqual(after.handover, "chat-BBB")  # B's lineage, not A's
        finally:
            shutil.rmtree(base)


class QueueColumnTest(unittest.TestCase):
    """WB-40: 'zu_bearbeiten' is a queue — the next ticket starts by itself when
    the running one finishes; per project, review either blocks that or not."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / "state.json"   # empty: no interactive lineage
        self.started = []

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _dispatcher(self, nonblocking=None):
        def runner(t, on_start=None):
            self.started.append(t.id)
            return "fertig", "sess-x"
        cfg = {"state_path": str(self.state), "default_project": str(self.dir),
               "nonblocking_review": nonblocking or {}}
        return dispatch.Dispatcher(self.dir, cfg=cfg, runner=runner)

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
        self.assertEqual(self.started, [queued.id])

    def test_link_blocked_ticket_stays_queued(self):
        blocker = store.create_ticket(self.dir, title="Blocker", description="")
        queued = self._queued("Wartet")
        store.update_ticket(self.dir, queued.id, {"nach": blocker.id})
        d = self._dispatcher()
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
        self.assertEqual(self.started, [queued.id])


class LiveStatusTest(unittest.TestCase):
    """WB-37: while a run is going, the board must see what it is doing and
    whether it died — including usage limits."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / "state.json"
        self.t = store.Ticket(id="WB-90", title="Live", status="in_arbeit",
                              project=str(self.dir),
                              body="## Beschreibung\n\nx\n\n## Ergebnis\n\n_(noch offen)_\n")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _stub(self, script):
        p = self.dir / "fake-claude"
        p.write_text("#!/bin/sh\n" + script, encoding="utf-8")
        p.chmod(0o755)
        return {"claude_bin": str(p), "state_path": str(self.state),
                "agent_timeout_minutes": 1}

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
            f"while [ ! -f {self.dir}/weiter ]; do sleep 0.05; done\n"
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
        time.sleep(0.2)
        mid_run = log.read_text() if log.exists() else ""
        (self.dir / "weiter").write_text("")   # let the stub finish
        th.join(timeout=5)
        self.assertIn("s-log", mid_run)        # log had content BEFORE the end
        self.assertEqual(out.get("result"), "spaet")


class StallDetectionTest(unittest.TestCase):
    """WB-37: a run that stops reporting must be visible as such."""

    def test_idle_seconds_are_exposed_for_the_board(self):
        d = Path(tempfile.mkdtemp())
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

            disp = dispatch.Dispatcher(d, cfg={"default_project": str(d)},
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
            shutil.rmtree(d)


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
