"""The suite must never write into the user's real state dir (audit 2026-08-16:
hardcoded ticket ids in test_dispatch collided with live tickets and a full
run destroyed the real werkbank-agent-WB-90.log — invisible to git because it
lives outside the repo)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from werkbank import dispatch


class LogIsolationTest(unittest.TestCase):
    def test_log_dir_honours_the_state_env(self):
        tmp = tempfile.mkdtemp(prefix="werkbank-isolation-probe-")
        old_x, old_l = os.environ.get("XDG_STATE_HOME"), os.environ.get("LOCALAPPDATA")
        os.environ["XDG_STATE_HOME"] = tmp
        os.environ["LOCALAPPDATA"] = tmp
        try:
            self.assertTrue(str(dispatch.log_dir()).startswith(tmp))
        finally:
            for k, v in (("XDG_STATE_HOME", old_x), ("LOCALAPPDATA", old_l)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_the_log_writing_module_isolates_itself(self):
        # Importing test_dispatch (however the runner names it) must leave the
        # state env pointing AWAY from the user's home.
        try:
            import test_dispatch  # noqa: F401  (top-level discovery)
        except ImportError:
            from tests import test_dispatch  # noqa: F401  (package discovery)
        state = os.environ.get("XDG_STATE_HOME", "")
        real = str(Path.home() / ".local" / "state")
        self.assertTrue(state, "test_dispatch did not set XDG_STATE_HOME")
        self.assertFalse(state.startswith(real),
                         f"tests would write into the real state dir: {state}")
        self.assertFalse(str(dispatch.log_dir()).startswith(real))


if __name__ == "__main__":
    unittest.main()
