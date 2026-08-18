"""Test-package init: isolate EVERYTHING state-touching from the real user.

Running the suite used to write into the REAL ~/.local/state/werkbank/logs —
tests hardcode ticket ids like WB-90 that collide with live tickets, so a
full run deleted and polluted real agent logs (found by an external audit,
2026-08-16; WB-90's original log is lost). git cannot see files outside the
repo, so the clean-tree discipline never caught it.

This runs at import time, before any test module touches werkbank code:
dispatch.log_dir() reads the env at call time, so pointing the state homes
at a per-run temp dir isolates every test in every module, including future
ones nobody remembers to isolate by hand. tests/test_log_isolation.py pins
this guarantee.
"""

import os
import tempfile

_STATE = tempfile.mkdtemp(prefix="werkbank-test-state-")
os.environ["XDG_STATE_HOME"] = _STATE      # POSIX path in dispatch.log_dir()
os.environ["LOCALAPPDATA"] = _STATE        # Windows path (WB-43)
