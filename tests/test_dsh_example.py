"""WB-233: the shipped `dsh-task` example must satisfy the same contract.

`src/werkbank/opencode.py` runs BOTH local workers through one code path
(WB-219): project as argv[1], task on stdin, result-only stdout, exit 0/3/4/5.
The board therefore cannot tell the two apart — which is the point, and which
means a divergent launcher fails in the way that is hardest to diagnose: it
looks like the model being bad at its job. That misdiagnosis has already
happened twice in this project with `opencode-task`, so the example is pinned
by tests that RUN it against a stand-in rather than by a paragraph in a README.

The dsh-specific parts (spooling the task to a file because dsh reads argv
only, chdir into the project because the sandbox root is the cwd, HOME) are
tested here too — they are exactly what makes this launcher a second file
instead of one flag.
"""

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "examples" / "dsh-task"
sys.path.insert(0, str(REPO / "src"))

from werkbank import opencode as board_side                  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree, sh_stub, posix_only  # noqa: E402


def _stub_agent(directory: Path, body: str) -> Path:
    return Path(sh_stub(directory, "stub-dsh", body))


def _run(args, task, env_extra, cwd):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run([sys.executable, str(LAUNCHER), *args],
                          input=task, capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=60)


@posix_only
class DshLauncherContractTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self.project = self.dir / "projekt"
        self.project.mkdir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_the_result_comes_back_clean(self):
        agent = _stub_agent(self.dir,
                            'echo "geschwaetz" >&2\n'
                            'echo "FERTIG"\n')
        r = _run([str(self.project)], "## Aufgabe\nBaue X ein.",
                 {"DSH_BIN": str(agent)}, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "FERTIG")
        self.assertIn("geschwaetz", r.stderr)

    def test_json_output_is_understood_too(self):
        """Plain prose OR JSON events — a launcher that guesses wrong reports
        an empty result, which the board can only read as 'produced nothing'."""
        agent = _stub_agent(self.dir,
                            '''printf '{"type":"text","text":"AUS JSON"}\\n'\n''')
        r = _run([str(self.project)], "x", {"DSH_BIN": str(agent)}, cwd=self.dir)
        self.assertEqual(r.stdout.strip(), "AUS JSON", r.stderr)

    def test_the_task_reaches_the_agent_through_a_file_not_argv(self):
        """dsh reads its task from argv only, and argv dies at ~128 KB. The
        launcher spools it — so the task must be FINDABLE, and must NOT be in
        the command line."""
        agent = _stub_agent(self.dir,
                            'echo "ARGV: $*" >> "$DSH_SEEN"\n'
                            'cat .dsh-task-*.md >> "$DSH_SEEN"\n'
                            'echo OK\n')
        seen = self.dir / "seen.txt"
        marker = "EINDEUTIGE-MARKE-4711"
        r = _run([str(self.project)], f"Aufgabe mit {marker}",
                 {"DSH_BIN": str(agent), "DSH_SEEN": str(seen)}, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = seen.read_text(encoding="utf-8")
        argv_line = [l for l in text.splitlines() if l.startswith("ARGV:")][0]
        self.assertNotIn(marker, argv_line, "task must never travel in argv")
        self.assertIn(marker, text, "the agent could not read the task file")

    def test_a_large_task_survives_intact(self):
        """200 KB — over the argv limit, the reason for the file."""
        agent = _stub_agent(self.dir, 'cat .dsh-task-*.md | wc -c\n')
        big = "x" * 200_000 + "\nENDE"
        r = _run([str(self.project)], big, {"DSH_BIN": str(agent)}, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreaterEqual(int(r.stdout.strip()), 200_000)

    def test_the_spool_file_is_removed_again(self):
        """It is written INTO the user's project — leaving it there would be
        litter in someone else's repository, and it would end up committed."""
        agent = _stub_agent(self.dir, 'echo OK\n')
        _run([str(self.project)], "x", {"DSH_BIN": str(agent)}, cwd=self.dir)
        self.assertEqual(list(self.project.glob(".dsh-task-*")), [])

    def test_the_spool_is_removed_even_when_the_agent_fails(self):
        agent = _stub_agent(self.dir, 'exit 7\n')
        r = _run([str(self.project)], "x", {"DSH_BIN": str(agent)}, cwd=self.dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(list(self.project.glob(".dsh-task-*")), [])

    def test_the_agent_runs_INSIDE_the_project(self):
        """The sandbox derives its workspace root from the cwd: the same call
        worked from one directory and failed from another."""
        agent = _stub_agent(self.dir, 'pwd\n')
        r = _run([str(self.project)], "x", {"DSH_BIN": str(agent)}, cwd=self.dir)
        self.assertEqual(os.path.realpath(r.stdout.strip()),
                         os.path.realpath(str(self.project)))

    def test_home_is_always_passed(self):
        """Without HOME the harness cannot find its profiles and reports a
        loader error that reads like a broken installation."""
        agent = _stub_agent(self.dir, 'echo "HOME=${HOME:-LEER}"\n')
        env = {"DSH_BIN": str(agent)}
        r = _run([str(self.project)], "x", env, cwd=self.dir)
        self.assertNotIn("HOME=LEER", r.stdout)
        self.assertTrue(r.stdout.strip().startswith("HOME="), r.stdout)

    def test_a_preferred_node_goes_first_on_path(self):
        """A minimal environment can put an old node first, and the failure
        message names neither node nor the version."""
        node_dir = self.dir / "node22" / "bin"
        node_dir.mkdir(parents=True)
        (node_dir / "node").write_text("#!/bin/sh\n", encoding="utf-8")
        agent = _stub_agent(self.dir, 'echo "$PATH"\n')
        r = _run([str(self.project)], "x",
                 {"DSH_BIN": str(agent), "DSH_NODE_BIN": str(node_dir / "node")},
                 cwd=self.dir)
        self.assertTrue(r.stdout.strip().startswith(str(node_dir)), r.stdout)

    def test_missing_directory_is_exit_3(self):
        r = _run([str(self.dir / "gibt-es-nicht")], "x", {}, cwd=self.dir)
        self.assertEqual(r.returncode, board_side.BAD_DIRECTORY)

    def test_unreachable_endpoint_is_exit_4(self):
        """Infrastructure, not a ticket failure — for this code the board puts
        the ticket back to Offen instead of blaming the model."""
        agent = _stub_agent(self.dir, 'echo x\n')
        r = _run([str(self.project)], "x",
                 {"DSH_BIN": str(agent),
                  "DSH_ENDPOINT": "http://127.0.0.1:9",     # nothing listens
                  "DSH_PROBE_TIMEOUT": "1"}, cwd=self.dir)
        self.assertEqual(r.returncode, board_side.ENDPOINT_DOWN, r.stderr)

    def test_no_final_text_is_exit_5(self):
        agent = _stub_agent(self.dir, 'echo "nur laerm" >&2\n')
        r = _run([str(self.project)], "x", {"DSH_BIN": str(agent)}, cwd=self.dir)
        self.assertEqual(r.returncode, board_side.NO_FINAL_TEXT, r.stderr)

    def test_an_empty_task_is_refused_before_the_model_runs(self):
        agent = _stub_agent(self.dir, 'echo "ICH LIEF" > "$DSH_SEEN"\n')
        seen = self.dir / "seen.txt"
        r = _run([str(self.project)], "   \n",
                 {"DSH_BIN": str(agent), "DSH_SEEN": str(seen)}, cwd=self.dir)
        self.assertEqual(r.returncode, board_side.NO_FINAL_TEXT)
        self.assertFalse(seen.exists(), "the model must not run on nothing")

    def test_a_background_child_cannot_hold_the_pipe(self):
        """The failure that stalled the board for 19 minutes."""
        agent = _stub_agent(self.dir, 'sleep 30 &\necho FERTIG\n')
        began = time.monotonic()
        r = _run([str(self.project)], "x", {"DSH_BIN": str(agent)}, cwd=self.dir)
        took = time.monotonic() - began
        self.assertEqual(r.stdout.strip(), "FERTIG")
        self.assertLess(took, 15, "a stray child kept the launcher alive")

    def test_a_missing_agent_says_which_variable_to_set(self):
        r = _run([str(self.project)], "x",
                 {"DSH_BIN": str(self.dir / "gibt-es-nicht")}, cwd=self.dir)
        self.assertEqual(r.returncode, 127)
        self.assertIn("DSH_BIN", r.stderr)


class ShippedFileTest(unittest.TestCase):
    """What a stranger receives."""

    def setUp(self):
        self.source = LAUNCHER.read_text(encoding="utf-8")

    def test_the_exit_codes_match_what_the_board_expects(self):
        self.assertIn("BAD_DIRECTORY = 3", self.source)
        self.assertIn("ENDPOINT_DOWN = 4", self.source)
        self.assertIn("NO_FINAL_TEXT = 5", self.source)
        self.assertEqual((board_side.BAD_DIRECTORY, board_side.ENDPOINT_DOWN,
                          board_side.NO_FINAL_TEXT), (3, 4, 5))

    def test_no_private_paths_or_host_names(self):
        """WB-52: a hardcoded $HOME path or the owner's box name would ship
        with every copy.

        The host name is checked by SHAPE, not by literal. The first version
        of this test spelled the private machine's name out — and thereby
        shipped it in a public file, which is the very thing it forbids. An
        adversarial review found it (WB-236 round 2); the pattern has a name
        in this project's own release notes: a check that exempts itself."""
        self.assertNotIn("/home/", self.source)
        self.assertNotIn(".local/bin/dsh", self.source)
        import re as _re
        hosts = _re.findall(r"[A-Za-z0-9][A-Za-z0-9.-]*\.local\b", self.source)
        self.assertEqual(hosts, [], f"private host name shipped: {hosts}")

    def test_it_is_executable(self):
        self.assertTrue(os.access(LAUNCHER, os.X_OK),
                        "a launcher nobody can run is not a launcher")

    def test_the_readme_documents_it(self):
        readme = (REPO / "examples" / "README.md").read_text(encoding="utf-8")
        self.assertIn("dsh-task", readme)
        for variable in ("DSH_BIN", "DSH_ENDPOINT", "DSH_NODE_BIN"):
            self.assertIn(variable, readme)


if __name__ == "__main__":
    unittest.main()
