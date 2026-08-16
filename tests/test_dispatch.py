import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import dispatch, store


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


def make_dispatcher(test, *args, **kwargs):
    """A Dispatcher whose ticker is ALWAYS stopped when the test ends.

    WB-93: 21 dispatchers were created in this file and 6 stopped, so 15 ticker
    threads kept scanning already-deleted temp dirs for the rest of the run —
    the suite generated the very load its timing-sensitive tests could not
    survive."""
    d = dispatch.Dispatcher(*args, **kwargs)
    test.addCleanup(d.stop)
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
        self.assertEqual(
            dispatch.project_slug("/home/USER/code/agent_ticket"),
            "-home-USER-code-agent-ticket",
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


@unittest.skipIf(os.name == "nt", "Attrappen sind sh-Skripte")
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


@unittest.skipIf(os.name == "nt", "Attrappen sind sh-Skripte")
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

        d = make_dispatcher(self, self.dir, runner=runner)
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


class SweepKillsOrphanProcessTest(unittest.TestCase):
    """WB-75: a claude process outlives its board when the board restarts.
    Sweep must find it (via the PID we recorded) and end it — but ONLY if the
    process really is that ticket's run (cmdline check guards against PID
    reuse and blind name-kills). A stand-in process stands in for claude so
    the test never touches the real CLI or the quota."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
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
        shutil.rmtree(self.dir)

    def _spawn(self, *extra_argv):
        # argv extras land in /proc/<pid>/cmdline verbatim — that is what the
        # match helper reads. `python -c` swallows the extras (they show up as
        # sys.argv), so the sleep loop simply hangs until the test kills it.
        p = subprocess.Popen(
            [sys.executable, "-c", "import time\nwhile True: time.sleep(60)",
             *extra_argv])
        self.procs.append(p)
        return p

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

        swept = dispatch.sweep_orphaned(self.dir)
        self.assertEqual(swept, [target.id])

        # Matching process is dead — wait for OS to reap.
        end = time.time() + 3
        while time.time() < end and orphan.poll() is None:
            time.sleep(0.05)
        self.assertIsNotNone(orphan.poll(),
                             "orphan process was not killed by sweep_orphaned")
        # Decoys still running.
        self.assertIsNone(decoy_claude.poll())
        self.assertIsNone(decoy_named.poll())

        loaded = {t.id: t for t in store.load_tickets(self.dir)}[target.id]
        self.assertEqual(loaded.status, "fehlgeschlagen")
        self.assertEqual(loaded.pid, "")
        self.assertIn("beendet", loaded.body)
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
        wait_until(lambda: self.started == [queued.id])
        self.assertEqual(self.started, [queued.id])


@unittest.skipIf(os.name == "nt", "Attrappen sind sh-Skripte")
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
        wait_until(lambda: log.exists() and "s-log" in log.read_text())
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


class LimitResumeTest(unittest.TestCase):
    """WB-57: a run that dies of the usage limit must resume by itself once
    the quota resets — the user should never have to say 'continue'."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.t = store.create_ticket(self.dir, title="Nach Limit weiter", description="")
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.dir)

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
        stub = self.dir / "fake-claude"
        stub.write_text("#!/bin/sh\n"
                        "echo '{\"type\":\"rate_limit_event\",\"rate_limit_info\":"
                        "{\"status\":\"rejected\",\"utilization\":1.0,"
                        "\"rateLimitType\":\"five_hour\",\"resetsAt\":2000000000}}'\n"
                        "echo 'Claude AI usage limit reached' >&2\n"
                        "exit 1\n")
        stub.chmod(0o755)
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
        self.dir = Path(tempfile.mkdtemp())
        self.started = []

    def tearDown(self):
        shutil.rmtree(self.dir)

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
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / "state.json"
        dispatch.register_ticket_session(str(self.dir), "chat-abc", self.state)
        self.t = store.create_ticket(self.dir, title="Übergabe", description="",
                                     project=str(self.dir))
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        self.started = []

    def tearDown(self):
        for d in getattr(self, "_dispatchers", []):
            d.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

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
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / "state.json"
        dispatch.register_ticket_session(str(self.dir), "chat-still", self.state)
        self.t = store.create_ticket(self.dir, title="Nie beansprucht", description="",
                                     project=str(self.dir))
        store.update_ticket(self.dir, self.t.id, {"status": "in_arbeit"})
        self.started = []
        self.dispatchers = []

    def tearDown(self):
        for d in self.dispatchers:
            d.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

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
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / "state.json"
        self.started = []
        self.dispatchers = []

    def tearDown(self):
        for d in self.dispatchers:
            d.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

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
        self.dir = Path(tempfile.mkdtemp())
        self.calls = []
        self.dispatchers = []

    def tearDown(self):
        for d in self.dispatchers:
            d.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

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
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / "state.json"
        self.started = []
        self.dispatchers = []

    def tearDown(self):
        for d in self.dispatchers:
            d.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

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
        self.dir = Path(tempfile.mkdtemp())
        self.t = store.Ticket(id="WB-77", title="Ende", status="in_arbeit",
                              project=str(self.dir),
                              body="## Beschreibung\n\nx\n\n## Ergebnis\n\n_(noch offen)_\n")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _cfg(self, script):
        p = self.dir / "fake-claude"
        p.write_text("#!/bin/sh\n" + script, encoding="utf-8")
        p.chmod(0o755)
        return {"claude_bin": str(p), "state_path": str(self.dir / "s.json"),
                "agent_timeout_minutes": 1, "exit_grace_seconds": 1}

    RESULT = ('echo \'{"type":"result","subtype":"success","result":"fertig",'
              '"session_id":"s-1"}\'\n')

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
        pid = int(child.read_text().strip())
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
        self.dir = Path(tempfile.mkdtemp())
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
                shutil.rmtree(self.dir)
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
        def work_ticket(t, cfg, on_progress=None):
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

    def test_no_second_claude_while_claude_runs(self):
        first = self._queued("Erster Claude", self.proj_a)
        second = self._queued("Zweiter Claude", self.proj_b)
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
        for needle in (
            'const own = t.assignee === "opencode" ? "opencode" : "claude"',
            '(r.model || "claude") === own',   # claude runs carry no model field
            '"wartet, bis der laufende opencode-Lauf fertig ist"',
            '"wartet, bis der laufende Agent fertig ist"',
        ):
            self.assertIn(needle, board, f"lane wait reason lost: {needle}")
        # The lane check must sit before the optimistic fallback (the RETURNED
        # string "startet gleich …", not the comments that also mention it).
        self.assertLess(board.index('"wartet, bis der laufende Agent fertig ist"'),
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
