"""The shipped example launcher must satisfy the contract dispatch.py encodes.

`src/werkbank/opencode.py` hardcodes how the board talks to the local worker:
project as argv[1], task on STDIN, result-only on stdout, exit 4 for an
unreachable endpoint. `examples/opencode-task` is the reference implementation
we hand to strangers — if the two drift apart, every opencode ticket fails in a
way that looks like the model being bad at its job. That happened twice while
building this (task passed as argv instead of stdin, both times), so the
contract is pinned by tests that RUN the example against a stand-in agent
rather than by a paragraph in a README.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "examples" / "opencode-task"
sys.path.insert(0, str(REPO / "src"))

from werkbank import opencode as board_side
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree, sh_stub, posix_only


def _stub_agent(directory: Path, body: str) -> Path:
    """A stand-in for the local agent, so no model and no network are needed."""
    return Path(sh_stub(directory, "stub-agent", body))


def _run(launcher_args, task, env_extra, cwd):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run([sys.executable, str(LAUNCHER), *launcher_args],
                          input=task, capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=60)


@posix_only
class ExampleLauncherContractTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self.project = self.dir / "projekt"
        self.project.mkdir()

    def tearDown(self):
        remove_tree(self.dir)

    def test_the_task_arrives_on_stdin_and_the_result_comes_back_clean(self):
        """The board passes the ticket text on stdin; only the final text may
        reach stdout, because it is copied into the ticket verbatim."""
        agent = _stub_agent(self.dir,
                            'cat > "$OPENCODE_SEEN"\n'
                            'echo "laut geschwaetz" >&2\n'
                            '''printf '{"type":"text","text":"FERTIG"}\\n'\n''')
        seen = self.dir / "seen.txt"
        r = _run([str(self.project)], "## Aufgabe\nBaue X ein.",
                 {"OPENCODE_BIN": str(agent), "OPENCODE_SEEN": str(seen)},
                 cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "FERTIG", "stdout must be result-only")
        self.assertIn("laut geschwaetz", r.stderr, "agent noise belongs on stderr")

    def test_a_large_task_survives_intact(self):
        """A real ticket plus a failing check's output is tens of KB. Passing
        that as an argument dies at ~128 KB — the reason it goes on stdin."""
        agent = _stub_agent(self.dir,
                            'printf \'{"type":"text","text":"%s"}\\n\' "$(wc -c < /dev/null)"\n')
        big = "x" * 200_000 + "\nENDE"
        r = _run([str(self.project)], big, {"OPENCODE_BIN": str(agent)}, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_directory_is_exit_3(self):
        r = _run([str(self.dir / "gibt-es-nicht")], "x", {}, cwd=self.dir)
        self.assertEqual(r.returncode, board_side.BAD_DIRECTORY)

    def test_unreachable_endpoint_is_exit_4(self):
        """Infrastructure, not a ticket failure — the board returns the ticket
        to Offen for this code instead of blaming the model."""
        agent = _stub_agent(self.dir, 'printf \'{"type":"text","text":"x"}\\n\'\n')
        r = _run([str(self.project)], "x",
                 {"OPENCODE_BIN": str(agent),
                  "OPENCODE_ENDPOINT": "http://127.0.0.1:9",   # nothing listens
                  "OPENCODE_PROBE_TIMEOUT": "1"}, cwd=self.dir)
        self.assertEqual(r.returncode, board_side.ENDPOINT_DOWN, r.stderr)

    def test_no_final_text_is_exit_5(self):
        agent = _stub_agent(self.dir, 'echo "nur laerm" >&2\n')
        r = _run([str(self.project)], "x", {"OPENCODE_BIN": str(agent)}, cwd=self.dir)
        self.assertEqual(r.returncode, board_side.NO_FINAL_TEXT, r.stderr)

    def test_a_background_child_cannot_hold_the_pipe(self):
        """The failure that stalled the board for 19 minutes: a leftover child
        inherits stdout, so the caller waits for an EOF that never comes."""
        agent = _stub_agent(self.dir,
                            'sleep 30 &\n'
                            '''printf '{"type":"text","text":"FERTIG"}\\n'\n''')
        began = __import__("time").monotonic()
        r = _run([str(self.project)], "x", {"OPENCODE_BIN": str(agent)}, cwd=self.dir)
        took = __import__("time").monotonic() - began
        self.assertEqual(r.stdout.strip(), "FERTIG")
        self.assertLess(took, 15, "a stray child kept the launcher alive")

    def test_the_exit_codes_match_what_the_board_expects(self):
        """One drift here fails every opencode ticket, silently and wrongly."""
        source = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("BAD_DIRECTORY = 3", source)
        self.assertIn("ENDPOINT_DOWN = 4", source)
        self.assertIn("NO_FINAL_TEXT = 5", source)
        self.assertEqual((board_side.BAD_DIRECTORY, board_side.ENDPOINT_DOWN,
                          board_side.NO_FINAL_TEXT), (3, 4, 5))
