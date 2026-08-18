"""WB-236 round 2: the four places a hostile first-run review stopped.

An adversarial reviewer copied the release into a sandbox and followed the
README literally, as a non-technical person would. The getting-started path
worked end to end — and broke at the edges, in a tool that speaks German
everywhere else:

- a hand-edited `config.json` with one comma too many ended in a raw
  `json.decoder.JSONDecodeError` traceback at import time,
- the documented way to stop the board (Ctrl-C) printed a `KeyboardInterrupt`
  traceback every single time,
- a wrong `claude_bin` reached the ticket as "[Errno 2] No such file or
  directory", while the README promises a plain-language reason,
- `lan: true` with a local `host` started normally and silently ignored the
  network mode — "nothing happens and nobody says why" is this project's
  most-hit failure mode.
"""

import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stubs import temp_dir, remove_tree                      # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from werkbank import dispatch, server                        # noqa: E402


class BrokenConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()

    def tearDown(self):
        remove_tree(self.dir)

    def _load(self, text):
        path = self.dir / "config.json"
        path.write_text(text, encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                server.load_config(path)
        return raised.exception.code, err.getvalue()

    def test_a_trailing_comma_gets_a_german_explanation(self):
        code, message = self._load('{\n  "port": 8765,\n}\n')
        self.assertEqual(code, 1)
        self.assertIn("config.json", message)
        self.assertIn("Zeile", message)
        self.assertIn("Komma", message)
        self.assertNotIn("Traceback", message)
        self.assertNotIn("JSONDecodeError", message)

    def test_it_names_the_file_by_its_full_path(self):
        """"Something is wrong somewhere" is not actionable."""
        _, message = self._load("{ kaputt }")
        self.assertIn(str(self.dir / "config.json"), message)

    def test_it_points_at_a_way_out(self):
        _, message = self._load("{,}")
        self.assertIn("config.example.json", message)

    def test_a_healthy_config_still_loads(self):
        path = self.dir / "config.json"
        path.write_text(json.dumps({"port": 8123}), encoding="utf-8")
        cfg = server.load_config(path)
        self.assertEqual(cfg["port"], 8123)
        self.assertTrue(cfg["config_exists"])

    def test_a_missing_config_is_not_an_error(self):
        cfg = server.load_config(self.dir / "gibt-es-nicht.json")
        self.assertFalse(cfg["config_exists"])
        self.assertIn("port", cfg)


class SilentLanModeTest(unittest.TestCase):
    """`lan: true` + local host is SAFE but does nothing. Say so."""

    def test_the_no_op_is_announced(self):
        note = server.lan_note("127.0.0.1", True, "")
        self.assertIsNotNone(note)
        self.assertIn("wirkungslos", note)
        self.assertIn("--lan-on", note)

    def test_a_normal_local_board_says_nothing(self):
        self.assertIsNone(server.lan_note("127.0.0.1", False, ""))

    def test_a_board_that_is_really_open_says_nothing_here(self):
        """That case is the refusal's job, not this note's."""
        self.assertIsNone(server.lan_note("0.0.0.0", True, "hash"))

    def test_the_refusal_still_owns_the_dangerous_direction(self):
        self.assertIsNotNone(server.exposure_refusal("0.0.0.0", True, ""))
        self.assertIsNotNone(server.exposure_refusal("0.0.0.0", False, "hash"))
        self.assertIsNone(server.exposure_refusal("127.0.0.1", True, ""))


class WorkerErrorTextTest(unittest.TestCase):
    def test_a_missing_program_names_the_setting_not_the_errno(self):
        text = dispatch.worker_error_text(
            FileNotFoundError(2, "No such file or directory", "/nirgendwo/claude"))
        self.assertIn("/nirgendwo/claude", text)
        self.assertIn("claude_bin", text)
        self.assertNotIn("Errno", text)
        self.assertNotIn("interner Fehler", text)

    def test_a_genuine_internal_error_still_says_so(self):
        text = dispatch.worker_error_text(ValueError("kaputt"))
        self.assertIn("interner Fehler der Werkbank", text)
        self.assertIn("kaputt", text)

    def test_a_file_error_without_a_name_falls_back(self):
        self.assertIn("interner Fehler",
                      dispatch.worker_error_text(FileNotFoundError("nichts")))


class StartupOutputTest(unittest.TestCase):
    """Two things the reviewer could only see by running the board."""

    def setUp(self):
        self.source = (Path(__file__).resolve().parent.parent
                       / "src" / "werkbank" / "server.py").read_text(encoding="utf-8")

    def test_ctrl_c_is_caught(self):
        """The documented way to stop the board must not print a traceback."""
        self.assertIn("except KeyboardInterrupt:", self.source)
        self.assertIn("Board beendet", self.source)

    def test_the_running_line_is_flushed(self):
        """Off a terminal (systemd, nohup, a pipe) Python buffers it, so the
        one message saying the board is up arrived late or never."""
        self.assertIn('Werkbank-Board läuft: http://{host}:{port}", flush=True',
                      self.source)


if __name__ == "__main__":
    unittest.main()
