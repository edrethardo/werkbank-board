import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import opencode, store


def _cfg(**extra):
    """config.json shape: the COMMAND lives here, the ticket only names it."""
    cfg = {"gates": {"/proj": {"tests": "make check"}}}
    cfg.update(extra)
    return cfg


def _t(**kw):
    kw.setdefault("id", "WB-1")
    kw.setdefault("title", "T")
    kw.setdefault("project", "/proj")
    return store.Ticket(**kw)


class ResolveGateTest(unittest.TestCase):
    """The ticket NAMES a check; the command comes from config.json. Nothing
    that crossed the network is ever executed."""

    def test_named_check_resolves_to_its_command(self):
        self.assertEqual(opencode.resolve_gate(_t(gate="tests"), _cfg()),
                         ("tests", "make check"))

    def test_unknown_name_resolves_to_nothing(self):
        """Quietly running a DIFFERENT check would defeat the mechanism."""
        self.assertEqual(opencode.resolve_gate(_t(gate="erfunden"), _cfg()),
                         (None, None))

    def test_a_command_in_the_ticket_is_not_a_check(self):
        """Even if a command reached the field, it is only ever looked up."""
        self.assertEqual(opencode.resolve_gate(_t(gate="rm -rf /"), _cfg()),
                         (None, None))

    def test_unnamed_falls_back_to_standard(self):
        cfg = {"gates": {"/proj": {"standard": "make check"}}}
        self.assertEqual(opencode.resolve_gate(_t(), cfg), ("standard", "make check"))

    def test_a_plain_string_in_config_still_works(self):
        """Older config.json shape: one command per project."""
        self.assertEqual(opencode.resolve_gate(_t(), {"gates": {"/proj": "make check"}}),
                         ("standard", "make check"))

    def test_none_when_nothing_configured(self):
        self.assertEqual(opencode.resolve_gate(_t(), {}), (None, None))


class NoGateNoDispatchTest(unittest.TestCase):
    """The correctness property: without a gate, a local model's self-report
    would BE the acceptance criterion. Refuse instead of running."""

    def test_refuses_and_never_runs_anything(self):
        calls = []
        out = opencode.work_ticket(_t(assignee="opencode"), {},
                                   run=lambda *a, **k: calls.append(a))
        self.assertEqual(out.status, "fehlgeschlagen")
        self.assertIn("pruefung", out.result.lower())
        self.assertEqual(calls, [], "nothing may be executed without a gate")


class GateOutcomeTest(unittest.TestCase):
    def _runner(self, script):
        """script: list of (argv-matcher, returncode, stdout) applied in order."""
        seen = []

        def run(cmd, **kw):
            seen.append((cmd, kw.get("input")))
            for match, rc, out in script:
                if match in " ".join(cmd):
                    return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        return run, seen

    def test_green_gate_goes_to_review(self):
        run, seen = self._runner([("opencode-task", 0, "fertig"), ("make", 0, "ok")])
        out = opencode.work_ticket(_t(gate="tests"), _cfg(review=False), run=run)
        self.assertEqual(out.status, "review")
        self.assertEqual(out.attempts, 1)

    def test_red_gate_retries_once_then_escalates(self):
        run, seen = self._runner([("opencode-task", 0, "versucht"), ("make", 1, "FAIL x")])
        out = opencode.work_ticket(_t(gate="tests"), _cfg(), run=run)
        self.assertEqual(out.attempts, 2, "exactly one free retry")
        self.assertEqual(out.status, "offen")
        self.assertEqual(out.changes.get("assignee"), "claude", "escalated")
        self.assertIn("FAIL x", out.result, "the gate output must reach the ticket")

    def test_retry_is_told_why_it_failed(self):
        run, seen = self._runner([("opencode-task", 0, "x"), ("make", 1, "AssertionError: nope")])
        opencode.work_ticket(_t(gate="tests"), _cfg(), run=run)
        tasks = [stdin for cmd, stdin in seen if "opencode-task" in " ".join(cmd)]
        self.assertIn("AssertionError: nope", tasks[1],
                      "the failing output is fed back into the retry, on stdin")

    def test_endpoint_down_is_infrastructure_not_failure(self):
        run, _ = self._runner([("opencode-task", 4, "")])
        out = opencode.work_ticket(_t(gate="tests"), _cfg(), run=run)
        self.assertEqual(out.status, "offen")
        self.assertNotIn("assignee", out.changes,
                         "an unreachable box must not escalate the ticket")


class ReviewTest(unittest.TestCase):
    def test_review_runs_outside_the_project_directory(self):
        seen = []

        def run(cmd, **kw):
            seen.append((cmd, kw.get("cwd")))
            joined = " ".join(cmd)
            if "opencode-task" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
            if "claude" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="VERDICT: OK", stderr="")
            if "rev-parse" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")
            if "diff" in joined:   # a real change, or there is nothing to review
                return subprocess.CompletedProcess(cmd, 0, stdout="+++ b/x\n+broken\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        opencode.work_ticket(_t(gate="tests"), _cfg(review=True), run=run)
        review = [(c, cwd) for c, cwd in seen if "claude" in " ".join(c)]
        self.assertTrue(review, "a review must have run")
        cmd, cwd = review[0]
        self.assertNotEqual(cwd, "/proj",
                            "running in the project loads CLAUDE.md and skills (~93k tokens)")
        self.assertIn("--disallowedTools", cmd)
        self.assertNotIn("--bare", cmd, "--bare breaks OAuth on a subscription")

    def test_review_nein_skips_it_entirely(self):
        seen = []

        def run(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        opencode.work_ticket(_t(gate="tests", review="nein"), _cfg(review=True), run=run)
        self.assertFalse([c for c in seen if "claude" in " ".join(c)],
                         "review: nein must cost nothing")


    def test_no_diff_means_no_paid_review(self):
        """An empty diff is nothing to review — do not pay for a verdict on it."""
        seen = []

        def run(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        opencode.work_ticket(_t(gate="tests"), _cfg(review=True), run=run)
        self.assertFalse([c for c in seen if "claude" in " ".join(c)])


class RealSubprocessTest(unittest.TestCase):
    """At least one test per external command must actually execute something.
    Injected fakes verify our logic and hide the calling convention: the task
    was passed as argv for a whole review cycle and every mocked test passed."""

    def _stand_in(self, tmp, name):
        """A script that records how it was called, mirroring opencode-task:
        the task arrives on STDIN and $2 is the MODEL ID."""
        p = Path(tmp) / name
        p.write_text(
            "#!/bin/sh\n"
            "printf 'ARGV1=[%s]\\n' \"$1\" >> \"$RECORD\"\n"
            "printf 'ARGV2=[%s]\\n' \"$2\" >> \"$RECORD\"\n"
            "printf 'STDIN=[%s]\\n' \"$(cat)\" >> \"$RECORD\"\n",
            encoding="utf-8")
        p.chmod(0o755)
        return str(p)

    def test_task_reaches_stdin_not_argv(self):
        tmp = tempfile.mkdtemp()
        record = Path(tmp) / "rec"
        script = self._stand_in(tmp, "opencode-task")
        old = opencode.OPENCODE_TASK
        os.environ["RECORD"] = str(record)
        try:
            opencode.OPENCODE_TASK = script
            opencode.run_task(_t(project=tmp), "## Aufgabe\nBaue X ein.")
        finally:
            opencode.OPENCODE_TASK = old
        seen = record.read_text(encoding="utf-8")
        self.assertIn("STDIN=[## Aufgabe", seen, "the task must arrive on stdin")
        self.assertEqual("ARGV2=[]", seen.splitlines()[1],
                         "argv[2] is the MODEL ID in the wrapper — never the task")

    def test_review_prompt_is_not_argv(self):
        """A single argv element dies at ~128 KB (MAX_ARG_STRLEN)."""
        tmp = tempfile.mkdtemp()
        record = Path(tmp) / "rec"
        script = self._stand_in(tmp, "claude")
        old = opencode.CLAUDE_BIN
        os.environ["RECORD"] = str(record)
        try:
            opencode.CLAUDE_BIN = script
            opencode.review_diff("krit", "+" * 200000)
        finally:
            opencode.CLAUDE_BIN = old
        self.assertIn("STDIN=[Pruefe diesen Diff", record.read_text(encoding="utf-8"))

    def test_a_huge_diff_would_have_broken_argv(self):
        with self.assertRaises(OSError):
            subprocess.run(["/bin/echo", "x" * 200000], capture_output=True)


class ClipTest(unittest.TestCase):
    def test_small_diff_untouched(self):
        text, cut = opencode.clip_diff("+ok")
        self.assertEqual(text, "+ok"); self.assertFalse(cut)

    def test_large_diff_is_cut_and_says_so(self):
        text, cut = opencode.clip_diff("x" * (opencode.MAX_DIFF + 5000))
        self.assertTrue(cut)
        self.assertLess(len(text), opencode.MAX_DIFF + 200)
        self.assertIn("abgeschnitten", text)


class BudgetTest(unittest.TestCase):
    # WB-94 changed the spec: the opencode lane has its OWN budget
    # (opencode_timeout_minutes, default 60) — the Claude limit no longer
    # applies here. These tests were rewritten to the new behaviour, not
    # weakened: the lane independence itself is pinned in Wb94TimeoutTest.
    def test_derived_from_the_board_config(self):
        self.assertEqual(opencode.budget_seconds({"opencode_timeout_minutes": 30}), 1800)

    def test_defaults_and_survives_rubbish(self):
        self.assertEqual(opencode.budget_seconds({}), 3600)
        self.assertEqual(opencode.budget_seconds({"opencode_timeout_minutes": "x"}), 3600)


class ProgressTest(unittest.TestCase):
    def test_reports_each_step(self):
        steps = []

        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        opencode.work_ticket(_t(gate="tests", review="nein"), _cfg(), run=run,
                             on_progress=steps.append)
        self.assertTrue(any("Versuch 1" in s for s in steps))
        self.assertTrue(any("tests" in s for s in steps),
                        f"the running check must be named: {steps}")


class SchemaTest(unittest.TestCase):
    def test_gate_survives_a_round_trip(self):
        t = _t(gate="tests", review="nein")
        again = store.parse_ticket(store.serialize_ticket(t))
        self.assertEqual(again.gate, "tests")
        self.assertEqual(again.review, "nein")

    def test_gate_cannot_be_set_through_the_api(self):
        """A gate is a shell command. The board is reachable on the LAN, so no
        request may introduce one — it may only come from the file itself."""
        d = Path(tempfile.mkdtemp())
        store.create_ticket(d, title="T", description="d", project="/proj")
        with self.assertRaises(ValueError):
            store.update_ticket(d, "WB-1", {"gate": "rm -rf /"})

    def test_newline_in_gate_is_refused(self):
        with self.assertRaises(ValueError):
            store.serialize_ticket(_t(gate="make check\nstatus: erledigt"))


class DispatcherRoutingTest(unittest.TestCase):
    def test_opencode_ticket_never_reaches_the_claude_runner(self):
        from werkbank import dispatch
        d = Path(tempfile.mkdtemp())
        store.create_ticket(d, title="T", description="Tu was", project=str(d))
        store.update_ticket(d, "WB-1", {"assignee": "opencode", "status": "in_arbeit"})
        called = []
        disp = dispatch.Dispatcher(d, cfg={"default_project": str(d)},
                                   runner=lambda *a, **k: called.append(a) or "nope")
        disp._run_one("WB-1")
        self.assertEqual(called, [], "the Claude runner must not be used")
        t = store.load_tickets(d)[0]
        self.assertEqual(t.status, "fehlgeschlagen", "no gate -> refused")
        self.assertIn("pruefung", t.body.lower())


if __name__ == "__main__":
    unittest.main()


class GateNameIsNotACommandTest(unittest.TestCase):
    """The board may set a check NAME — that is what makes the feature usable
    for an owner who never opens a terminal. It must therefore be impossible
    for that field to be, or to become, a shell command: the command lives in
    config.json and is only ever looked up by name."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        store.create_ticket(self.dir, title="T", description="x", project="/proj")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir)

    def test_a_name_can_be_set(self):
        store.update_ticket(self.dir, "WB-1", {"gate": "tests"})
        self.assertEqual(store.load_tickets(self.dir)[0].gate, "tests")

    def test_a_command_is_refused_as_a_name(self):
        for evil in ("rm -rf /", "make; rm -rf /", "$(id)", "`id`", "a|b", "x&y",
                     "a>b", "a\\b", "'q'", '"q"', "a" * 41):
            with self.subTest(evil=evil), self.assertRaises(ValueError):
                store.update_ticket(self.dir, "WB-1", {"gate": evil})

    def test_creation_refuses_one_too(self):
        with self.assertRaises(ValueError):
            store.create_ticket(self.dir, title="T", description="x",
                                project="/proj", gate="rm -rf /")

    def test_an_unknown_name_runs_nothing(self):
        """Even a perfectly valid NAME that is not configured must not run."""
        calls = []
        out = opencode.work_ticket(_t(gate="ausgedacht"), _cfg(),
                                   run=lambda *a, **k: calls.append(a))
        self.assertEqual(out.status, "fehlgeschlagen")
        self.assertEqual(calls, [])
        self.assertIn("ausgedacht", out.result)
        self.assertIn("tests", out.result, "name the checks he can pick instead")


class PublicConfigTest(unittest.TestCase):
    """What the board page is allowed to know."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from werkbank import server
        self.server = server

    def test_the_password_hash_never_reaches_the_browser(self):
        out = self.server.public_config({"password_hash": "pbkdf2$geheim", "lan": True})
        self.assertNotIn("password_hash", out)
        self.assertTrue(out["lan"])

    def test_only_check_names_are_published_never_the_commands(self):
        out = self.server.public_config(
            {"gates": {"/proj": {"tests": "python3 -m pytest -q", "baut": "make"}}})
        self.assertEqual(out["gates"], {"/proj": ["baut", "tests"]})
        self.assertNotIn("pytest", json.dumps(out))


class ToolFailureIsNotModelFailureTest(unittest.TestCase):
    """Measured 2026-08-16 by the coding_agent session: a task too large for
    argv made the wrapper exit 126. That is neither 0 nor the endpoint code, so
    the old flow read it as 'ran, produced nothing', retried, and escalated as
    'twice red' — blaming the local model for a tooling limit. The check still
    decides SUCCESS; the exit code decides who to blame for FAILURE."""

    def _run(self, task_rc, gate_rc):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            rc = task_rc if opencode.OPENCODE_TASK in cmd[0] else gate_rc
            return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")

        return opencode.work_ticket(_t(gate="tests", review="nein"), _cfg(),
                                    run=run), calls

    def test_a_broken_run_is_not_escalated_as_twice_red(self):
        out, calls = self._run(task_rc=126, gate_rc=1)
        self.assertEqual(out.status, "fehlgeschlagen")
        self.assertEqual(out.attempts, 1, "a run that never happened gets no retry")
        self.assertIn("126", out.result)
        self.assertNotIn("assignee", out.changes, "must not be blamed on the model")

    def test_a_green_check_still_wins_over_a_bad_exit_code(self):
        """Evidence beats exit codes: if the work is there, it counts."""
        out, _ = self._run(task_rc=126, gate_rc=0)
        self.assertEqual(out.status, "review")

    def test_a_missing_project_directory_says_so(self):
        out, calls = self._run(task_rc=opencode.BAD_DIRECTORY, gate_rc=0)
        self.assertEqual(out.status, "fehlgeschlagen")
        self.assertIn("Projektverzeichnis", out.result)
        self.assertEqual(len(calls), 2, "no check runs when the directory is wrong")

    def test_no_final_text_is_still_a_normal_attempt(self):
        """exit 5 means it ran but said nothing — the check may still be green."""
        out, _ = self._run(task_rc=opencode.NO_FINAL_TEXT, gate_rc=0)
        self.assertEqual(out.status, "review")


class NewFilesReachTheReviewTest(unittest.TestCase):
    """Found on the FIRST live ticket (2026-08-16): the run created a brand-new
    module, `git diff <sha>` showed nothing — untracked files are invisible to
    it — and the ticket recorded "kein Diff gegenueber dem Stand vor dem Lauf".
    So the paid review silently did not run in precisely the case where the
    model wrote something entirely new, which is the usual outcome of a
    "build X" ticket. Same shape as the argv bugs: a mechanism failing quietly
    while producing evidence that reads like a considered finding."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._git("init", ".")
        (self.dir / "vorhanden.txt").write_text("alt\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "start")
        self.sha = subprocess.run(["git", "-C", str(self.dir), "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir)

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.dir), *args], capture_output=True,
                       text=True, check=False)

    def test_a_new_file_is_part_of_the_diff(self):
        (self.dir / "brandneu.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        diff = opencode.diff_since(str(self.dir), self.sha)
        self.assertIn("brandneu.py", diff, "a new file must reach the reviewer")
        self.assertIn("+    return 1", diff, "with its content, as an addition")

    def test_tracked_changes_still_come_through(self):
        (self.dir / "vorhanden.txt").write_text("alt\nneu\n", encoding="utf-8")
        diff = opencode.diff_since(str(self.dir), self.sha)
        self.assertIn("+neu", diff)

    def test_both_together(self):
        (self.dir / "vorhanden.txt").write_text("alt\nneu\n", encoding="utf-8")
        (self.dir / "brandneu.py").write_text("x = 1\n", encoding="utf-8")
        diff = opencode.diff_since(str(self.dir), self.sha)
        self.assertIn("+neu", diff)
        self.assertIn("brandneu.py", diff)

    def test_ignored_files_stay_out(self):
        (self.dir / ".gitignore").write_text("geheim.txt\n", encoding="utf-8")
        (self.dir / "geheim.txt").write_text("nicht anfassen\n", encoding="utf-8")
        diff = opencode.diff_since(str(self.dir), self.sha)
        self.assertNotIn("NEUE DATEI: geheim.txt", diff)
        self.assertNotIn("nicht anfassen", diff, "ignored content must stay out")

    def test_the_index_is_left_alone(self):
        """`git add -N` would have worked too — and would have staged files in a
        repo the user also works in, where a later `git commit -a` sweeps them up."""
        (self.dir / "brandneu.py").write_text("x = 1\n", encoding="utf-8")
        opencode.diff_since(str(self.dir), self.sha)
        staged = subprocess.run(["git", "-C", str(self.dir), "diff", "--cached",
                                 "--name-only"], capture_output=True, text=True)
        self.assertEqual(staged.stdout.strip(), "", "nothing may be staged")

    def test_nothing_changed_means_an_empty_diff(self):
        self.assertEqual(opencode.diff_since(str(self.dir), self.sha).strip(), "")


class MissingWrapperIsExplainedTest(unittest.TestCase):
    """WB-52 (public release): `opencode-task` is a local install, not part of
    this project. A fresh checkout that assigns a ticket to opencode used to get
    'Fehlgeschlagen (interner Fehler der Werkbank)' from the worker's catch-all —
    blaming the board for a missing prerequisite of the user's machine."""

    def test_a_missing_wrapper_says_what_is_missing(self):
        def run(cmd, **kw):
            if opencode.OPENCODE_TASK in cmd[0]:
                raise FileNotFoundError(2, "No such file or directory", cmd[0])
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        out = opencode.work_ticket(_t(gate="tests"), _cfg(), run=run)
        self.assertEqual(out.status, "fehlgeschlagen")
        self.assertIn("nicht gefunden", out.result)
        self.assertIn("claude", out.result, "say what to do instead")
        self.assertNotIn("assignee", out.changes, "not an escalation")


class Wb94TimeoutTest(unittest.TestCase):
    """WB-94: the opencode lane gets its OWN time budget, and a timeout must
    end the WHOLE process group — the WB-92 incident left the wrapper and the
    opencode binary alive and editing the repo after the abort."""

    def test_budget_uses_own_limit_not_the_claude_one(self):
        self.assertEqual(opencode.budget_seconds({}), 3600)
        self.assertEqual(opencode.budget_seconds({"opencode_timeout_minutes": 5}), 300)
        # The claude limit must have NO influence on the opencode lane.
        self.assertEqual(opencode.budget_seconds({"agent_timeout_minutes": 1}), 3600)

    def test_timeout_kills_the_whole_process_group(self):
        import time as _time
        d = Path(tempfile.mkdtemp())
        try:
            pidfile = d / "grandchild.pid"
            wrapper = d / "fake-opencode-task"
            wrapper.write_text("#!/bin/sh\nsleep 8 &\necho $! > %s\nwait\n" % pidfile)
            wrapper.chmod(0o755)
            old = opencode.OPENCODE_TASK
            opencode.OPENCODE_TASK = str(wrapper)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    opencode.run_task(_t(), "aufgabe", timeout=0.5)
            finally:
                opencode.OPENCODE_TASK = old
            self.assertTrue(pidfile.exists(), "wrapper never started")
            pid = int(pidfile.read_text().strip())
            deadline = _time.monotonic() + 3
            alive = True
            while _time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    alive = False
                    break
                _time.sleep(0.05)
            self.assertFalse(alive, f"grandchild {pid} survived the timeout")
        finally:
            import shutil as _shutil
            _shutil.rmtree(d)

    def test_timeout_becomes_honest_failure_not_internal_error(self):
        def run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        out = opencode.work_ticket(_t(gate="tests"), _cfg(), run=run)
        self.assertEqual(out.status, "fehlgeschlagen")
        self.assertIn("Zeitlimit", out.result)


if __name__ == "__main__":
    unittest.main()
