"""WB-219: `assignee: dsh` is dispatched like opencode, because it IS the
same contract.

The DeepSeek harness wrapper (`dsh-task`, WB-217) was deliberately written to
answer exactly what `opencode-task` answers: the task on stdin, the final text
and nothing else on stdout, exit 0 ok / 3 bad directory / 4 endpoint
unreachable / 5 no final text. The whole point of that decision was that the
board keeps ONE code path and ONE exit-code mapping. These tests pin the parts
that would silently diverge:

- the gate is mandatory for dsh too (no gate -> refused, never started),
- exit 4 is infrastructure and must NOT be blamed on the model,
- the task travels on stdin, never as argv,
- and dsh shares the local lane with opencode instead of getting its own,
  because both talk to the same GPU on the same box.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree                      # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from werkbank import dispatch, opencode, store               # noqa: E402


class Recorder:
    """A stand-in runner that records the call instead of running anything."""

    def __init__(self, returncode=0, stdout=""):
        self.calls = []
        self.returncode, self.stdout = returncode, stdout

    def __call__(self, argv, input=None, capture_output=None, text=None, **kw):
        self.calls.append({"argv": list(argv), "input": input, "kw": kw})

        class P:
            pass
        p = P()
        p.returncode, p.stdout, p.stderr = self.returncode, self.stdout, ""
        return p


class LaneMembershipTest(unittest.TestCase):
    def test_dsh_may_start_automatically(self):
        self.assertTrue(dispatch.known_assignee("dsh"))

    def test_a_stranger_still_may_not(self):
        self.assertFalse(dispatch.known_assignee("mensch"))

    def test_dsh_and_opencode_share_one_slot(self):
        """Two local runs on one GPU would only make each other slower."""
        self.assertEqual(dispatch.assortment("dsh"),
                         dispatch.assortment("opencode"))

    def test_the_local_slot_is_not_the_claude_slot(self):
        self.assertNotEqual(dispatch.assortment("dsh"),
                            dispatch.assortment("claude"))

    def test_is_local_ignores_case_and_padding(self):
        self.assertTrue(dispatch.is_local("  DSH "))
        self.assertFalse(dispatch.is_local("claude"))
        self.assertFalse(dispatch.is_local(""))


class RunnerChoiceTest(unittest.TestCase):
    def test_dsh_tickets_go_to_the_dsh_wrapper(self):
        self.assertEqual(opencode.runner_for("dsh"), opencode.DSH_TASK)

    def test_everything_else_goes_to_opencode_task(self):
        for who in ("opencode", "", None, "unbekannt"):
            self.assertEqual(opencode.runner_for(who), opencode.OPENCODE_TASK)

    def test_the_wrapper_name_is_resolved_not_hardcoded(self):
        """WB-52: a private $HOME path must not be baked into the source."""
        src = (Path(__file__).resolve().parent.parent
               / "src" / "werkbank" / "opencode.py").read_text(encoding="utf-8")
        self.assertIn('_resolve_bin("dsh-task")', src)
        self.assertNotIn("/home/", src)


class TaskDeliveryTest(unittest.TestCase):
    """The task must travel on stdin. As argv it would die at 128 KB — and
    `dsh-task`'s second argument is not the task (WB-217: dsh reads argv only,
    so the wrapper spools stdin to a file itself)."""

    def setUp(self):
        self.dir = temp_dir()
        self.t = store.create_ticket(self.dir, "Titel", "Rumpf", project="/p",
                                     assignee="dsh")

    def tearDown(self):
        remove_tree(self.dir)

    def test_task_on_stdin_and_the_project_as_the_only_argument(self):
        rec = Recorder(stdout="fertig")
        code, text = opencode.run_task(self.t, "die Aufgabe", run=rec)
        self.assertEqual((code, text), (0, "fertig"))
        call = rec.calls[0]
        self.assertEqual(call["input"], "die Aufgabe")
        self.assertEqual(call["argv"], [opencode.DSH_TASK, "/p"])
        self.assertNotIn("die Aufgabe", call["argv"])

    def test_an_opencode_ticket_still_uses_opencode_task(self):
        t = store.create_ticket(self.dir, "T", "B", project="/p",
                                assignee="opencode")
        rec = Recorder()
        opencode.run_task(t, "x", run=rec)
        self.assertEqual(rec.calls[0]["argv"][0], opencode.OPENCODE_TASK)

    def test_a_stand_in_assigned_to_the_module_is_honoured(self):
        """Tests point the lane at a fake by assigning the module attribute;
        a mapping frozen at import would ignore it (it did, once)."""
        saved = opencode.DSH_TASK
        try:
            opencode.DSH_TASK = "/tmp/fake-dsh-task"
            rec = Recorder()
            opencode.run_task(self.t, "x", run=rec)
            self.assertEqual(rec.calls[0]["argv"][0], "/tmp/fake-dsh-task")
        finally:
            opencode.DSH_TASK = saved


class GateIsMandatoryTest(unittest.TestCase):
    """Same correctness property as opencode: without a resolvable gate the
    ticket is REFUSED, not started. The local model's own report never decides."""

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_no_gate_means_refused_and_the_runner_is_never_called(self):
        t = store.create_ticket(self.dir, "T", "B", project="/p", assignee="dsh")
        rec = Recorder()
        outcome = opencode.work_ticket(t, {"gates": {}}, run=rec)
        self.assertEqual(outcome.status, "fehlgeschlagen")
        self.assertIn("Nicht gestartet", outcome.result)
        self.assertEqual(rec.calls, [], "der Runner darf gar nicht erst laufen")

    def test_a_gate_the_project_does_not_have_is_refused_by_name(self):
        t = store.create_ticket(self.dir, "T", "B", project="/p", assignee="dsh",
                                gate="Tests laufen durch")
        cfg = {"gates": {"/p": {"Andere Pruefung": "true"}}}
        outcome = opencode.work_ticket(t, cfg, run=Recorder())
        self.assertEqual(outcome.status, "fehlgeschlagen")
        self.assertIn("Andere Pruefung", outcome.result)


class RueckfrageRefusalTest(unittest.TestCase):
    """WB-220: a dsh/opencode run that starts its final text with the
    RÜCKFRAGE marker must NOT quietly go to review. The wrapper is one-shot;
    there is no session an answer could resume, so pretending the ticket ran
    would silently drop the user's question. Refuse the run instead, name the
    reason, and keep the model's text on the ticket."""

    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def _cfg(self):
        return {"gates": {"/p": {"standard": "true"}}}

    def _run(self, question):
        # A single Recorder answers BOTH calls the lane would make (model,
        # then gate). The test's whole point is that the SECOND call — the
        # gate — is never issued for a question-only run.
        rec = Recorder(returncode=0, stdout=question)
        t = store.create_ticket(self.dir, "T", "B", project="/p",
                                assignee="dsh", gate="standard")
        outcome = opencode.work_ticket(t, self._cfg(), run=rec)
        return outcome, rec

    def test_a_rueckfrage_from_dsh_becomes_fehlgeschlagen(self):
        outcome, _ = self._run("RÜCKFRAGE AN DEN NUTZER:\nWelches Format?")
        self.assertEqual(outcome.status, "fehlgeschlagen")

    def test_the_gate_is_not_spent_on_a_question(self):
        _, rec = self._run("RÜCKFRAGE AN DEN NUTZER:\nWelches Format?")
        argvs = [call["argv"] for call in rec.calls]
        # The wrapper was called exactly once; the gate ('/bin/sh -c') never.
        self.assertEqual(sum(1 for a in argvs if a[:1] == [opencode.DSH_TASK]), 1)
        self.assertFalse(any(a[:1] == ["/bin/sh"] for a in argvs),
                         f"gate was invoked: {argvs}")

    def test_the_message_names_the_worker_and_keeps_the_question(self):
        outcome, _ = self._run("RÜCKFRAGE AN DEN NUTZER:\nWelches Format?")
        self.assertIn("`dsh`", outcome.result)
        self.assertIn("keine Rückfragen", outcome.result)
        self.assertIn("Welches Format?", outcome.result)

    def test_an_opencode_run_gets_the_same_guard(self):
        rec = Recorder(returncode=0, stdout="RÜCKFRAGE AN DEN NUTZER:\nWie?")
        t = store.create_ticket(self.dir, "T", "B", project="/p",
                                assignee="opencode", gate="standard")
        outcome = opencode.work_ticket(t, self._cfg(), run=rec)
        self.assertEqual(outcome.status, "fehlgeschlagen")
        self.assertIn("`opencode`", outcome.result)

    def test_a_regular_answer_still_goes_through_the_gate(self):
        """A non-question answer must reach the gate — otherwise the guard
        would swallow every ticket."""
        rec = Recorder(returncode=0, stdout="fertig, siehe patch.diff")
        t = store.create_ticket(self.dir, "T", "B", project="/p",
                                assignee="dsh", gate="standard")
        outcome = opencode.work_ticket(t, self._cfg(), run=rec)
        argvs = [call["argv"] for call in rec.calls]
        self.assertTrue(any(a[:1] == ["/bin/sh"] for a in argvs),
                        f"gate was NOT invoked: {argvs}")
        self.assertEqual(outcome.status, "review")

    def test_marker_hidden_deep_in_prose_does_not_trigger(self):
        """Same guarantee as `is_query_result`: only the FIRST non-whitespace
        content counts. A stray mention ten paragraphs down is not a question."""
        prose = ("Ich habe das umgesetzt.\n\nHier ein Zitat: "
                 "'RÜCKFRAGE AN DEN NUTZER:' meint der Marker.")
        rec = Recorder(returncode=0, stdout=prose)
        t = store.create_ticket(self.dir, "T", "B", project="/p",
                                assignee="dsh", gate="standard")
        outcome = opencode.work_ticket(t, self._cfg(), run=rec)
        argvs = [call["argv"] for call in rec.calls]
        self.assertTrue(any(a[:1] == ["/bin/sh"] for a in argvs),
                        f"gate was NOT invoked: {argvs}")
        self.assertEqual(outcome.status, "review")


class ExitCodeMappingTest(unittest.TestCase):
    """Exit 4 is the box being off — infrastructure, not a bad model. Blaming
    the model there is what WB-94 called out for opencode; dsh inherits the
    fix because it inherits the path."""

    def test_the_codes_are_the_ones_the_wrapper_documents(self):
        self.assertEqual((opencode.BAD_DIRECTORY, opencode.ENDPOINT_DOWN,
                          opencode.NO_FINAL_TEXT), (3, 4, 5))

    def test_the_shipped_wrapper_documents_exactly_these(self):
        """Reads the real dsh-task if it is installed: the board's mapping and
        the wrapper's contract must not drift apart silently."""
        wrapper = Path.home() / ".local" / "bin" / "dsh-task"
        if not wrapper.exists():
            self.skipTest("dsh-task ist auf dieser Maschine nicht installiert")
        head = wrapper.read_text(encoding="utf-8", errors="replace")[:2000]
        self.assertIn("exit 0 ok | 3 bad dir | 4 endpoint unreachable "
                      "| 5 ran but produced no text", head)
        self.assertIn("STDIN", head)


if __name__ == "__main__":
    unittest.main()
