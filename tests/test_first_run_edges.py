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


class HalfFilledConfigTest(unittest.TestCase):
    """WB-263: a config.json that sets only the port passed silently.

    The defaults leave `default_project` on the Werkbank checkout, so the first
    dragged ticket would set a Bash-capable agent loose on the board's own
    repository. WB-48 built this warning for the missing-config case and never
    covered the half-filled one — found by a review that walked a first run on
    a fresh machine, not by the suite."""

    def test_a_config_without_a_project_is_called_out(self):
        from werkbank import setup
        w = setup.config_warning(
            {"default_project": "/repo", "default_project_in_file": False},
            True, "/repo")
        self.assertIsNotNone(w)
        self.assertIn("default_project", w)
        self.assertIn("Befehls-Rechten", w, "say what actually happens")

    def test_a_configured_project_stays_quiet(self):
        from werkbank import setup
        self.assertIsNone(
            setup.config_warning({"default_project": "/mein/projekt"}, True, "/repo"))

    def test_pointing_the_board_at_itself_on_purpose_stays_quiet(self):
        """It is how this tool was built — only the UNSET field is the
        accident, and an existing test (test_deliberate_choice_is_silent)
        caught the first version of this fix for exactly that reason."""
        from werkbank import setup
        self.assertIsNone(setup.config_warning(
            {"default_project": "/repo", "default_project_in_file": True},
            True, "/repo"))

    def test_the_warning_is_backed_by_an_actual_REFUSAL(self):
        """WB-263 round 4: the first version of this fix only warned. A banner
        does not stop the drag, and the user who does not read banners is
        exactly who the guard is for — the missing-config twin has refused
        since WB-48, the half-filled one did not."""
        from werkbank import setup
        cfg = {"repo_root": "/repo", "config_exists": True,
               "default_project_in_file": False}
        refusal = setup.dispatch_refusal(cfg, "/repo")
        self.assertIsNotNone(refusal, "the ticket would still start")
        self.assertIn("default_project", refusal)
        self.assertIn("Befehls-Rechten", refusal)

    def test_a_deliberate_self_target_still_dispatches(self):
        """Aiming the board at its own repository on purpose is how this tool
        was built — the refusal must not touch it."""
        from werkbank import setup
        self.assertIsNone(setup.dispatch_refusal(
            {"repo_root": "/repo", "config_exists": True,
             "default_project_in_file": True}, "/repo"))

    def test_another_project_is_never_refused(self):
        from werkbank import setup
        self.assertIsNone(setup.dispatch_refusal(
            {"repo_root": "/repo", "config_exists": True,
             "default_project_in_file": False}, "/ganz/woanders"))

    def test_the_missing_config_case_still_works(self):
        from werkbank import setup
        w = setup.config_warning({"default_project": "/repo"}, False, "/repo")
        self.assertIn("Keine config.json", w)


class RunLogPathTest(unittest.TestCase):
    """The detail dialog printed /tmp/werkbank-agent-<id>.log — a path that has
    not existed since WB-35 moved logs into the user's state dir with 0700.
    Anyone in an audience who typed it hit nothing."""

    def test_the_page_does_not_hardcode_a_log_path(self):
        board = (Path(__file__).resolve().parent.parent
                 / "src" / "werkbank" / "board.html").read_text(encoding="utf-8")
        self.assertNotIn("/tmp/werkbank-agent-", board)
        self.assertIn("config.log_dir", board)

    def test_the_server_publishes_the_real_directory(self):
        from werkbank import dispatch, server
        safe = server.public_config({"gates": {}, "password_hash": "secret"})
        self.assertEqual(safe["log_dir"], str(dispatch.log_dir()))
        self.assertNotIn("password_hash", safe, "still no hash to the browser")


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
