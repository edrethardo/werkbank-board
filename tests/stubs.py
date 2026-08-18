"""Stand-in command-line programs that also work on Windows.

The suite fakes external programs (`claude`, `opencode-task`) by writing a small
script and pointing the code under test at it. Those stand-ins are `#!/bin/sh`
files — which Windows cannot execute, because it has no shebang handling. That
one detail produced ~52 errors in every Windows CI run.

Skipping those tests on Windows would be the cheap answer and the wrong one:
the code under test is portable, only the FAKE was not. So on Windows the same
shell script is wrapped in a `.cmd` that hands it to Git Bash, which ships with
every GitHub `windows-latest` image and with Git for Windows generally. Where
bash genuinely is not available, `require_sh()` skips with a reason that says
so, instead of failing as if the product were broken.

Genuinely POSIX-only behaviour — signals, process groups, `/proc` — stays
skipped on Windows, deliberately and visibly.
"""

import os
import shutil
import stat
import sys
import unittest
from pathlib import Path

WINDOWS = os.name == "nt"

_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def find_sh():
    """Path to a POSIX shell, or None. On Windows that is Git Bash."""
    if not WINDOWS:
        return "/bin/sh"
    found = shutil.which("bash") or shutil.which("sh")
    if found:
        return found
    for candidate in _BASH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def require_sh():
    """Skip loudly rather than fail, when no shell exists to run a fake with."""
    shell = find_sh()
    if shell is None:
        raise unittest.SkipTest(
            "kein POSIX-Shell fuer die Attrappe gefunden (Windows ohne Git Bash)")
    return shell


def sh_stub(directory, name: str, sh_body: str) -> str:
    """Write a shell stand-in and return the path the code under test may call.

    `sh_body` is the script WITHOUT the shebang, exactly as it was written for
    Unix. On Windows the returned path is a `.cmd` wrapper around the same
    script, so the test keeps its meaning on both systems.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not sh_body.startswith("#!"):
        sh_body = "#!/bin/sh\n" + sh_body

    if not WINDOWS:
        launcher = directory / name
        launcher.write_text(sh_body, encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        return str(launcher)

    shell = require_sh()
    script = directory / f"{name}.sh"
    script.write_text(sh_body, encoding="utf-8", newline="\n")   # LF for sh
    launcher = directory / f"{name}.cmd"
    # @ keeps the command itself off stdout — callers parse stdout strictly.
    launcher.write_text(f'@"{shell}" "{script}" %*\r\n', encoding="utf-8")
    return str(launcher)


def python_stub(directory, name: str, python_body: str) -> str:
    """Same idea, but the fake is written in Python — no shell needed at all.
    Prefer this for new stand-ins; `sh_stub` exists for the ones that were
    already written as shell."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = directory / f"{name}.py"
    payload.write_text(python_body, encoding="utf-8")
    if WINDOWS:
        launcher = directory / f"{name}.cmd"
        launcher.write_text(f'@"{sys.executable}" "{payload}" %*\r\n',
                            encoding="utf-8")
        return str(launcher)
    launcher = directory / name
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{payload}" "$@"\n', encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return str(launcher)


def stop_before_teardown(test, dispatcher) -> None:
    """Make sure the dispatcher releases its board lock BEFORE tearDown runs.

    `addCleanup` callbacks run AFTER `tearDown`, so a test that removes its
    temp dir there was doing so while the dispatcher still held
    `.dispatcher.lock` open. On Unix you may unlink an open file and nobody
    noticed; on Windows that is `PermissionError [WinError 32]` — 52 of the 68
    errors in the first green-Windows attempt were exactly this, all in
    teardown, none in the code under test."""
    original = test.tearDown

    def tear_down_after_stopping():
        try:
            dispatcher.stop()
        finally:
            original()

    test.tearDown = tear_down_after_stopping


def temp_dir() -> Path:
    """A temp directory whose path is the REAL one.

    `tempfile.mkdtemp()` hands back the 8.3 short form on Windows
    (`C:\\Users\\RUNNER~1\\…`), while the code under test resolves paths and
    reports the long form (`…\\runneradmin\\…`). Comparing the two then fails
    for no reason the product is responsible for."""
    import tempfile
    return Path(tempfile.mkdtemp()).resolve()


def remove_tree(path) -> None:
    """`shutil.rmtree` that copes with Windows.

    Two things bite there and neither means the code is wrong: a file that is
    still open cannot be unlinked (retry briefly — handles are released
    asynchronously), and a read-only file refuses deletion outright, which is
    how git stores its pack files."""
    import shutil
    import stat as _stat
    import time as _time

    def drop_readonly(func, target, _exc):
        try:
            os.chmod(target, _stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    for attempt in range(5):
        try:
            shutil.rmtree(path, onerror=drop_readonly)
            return
        except OSError:
            if attempt == 4:
                shutil.rmtree(path, ignore_errors=True)
                return
            _time.sleep(0.2)


def sh_path(path) -> str:
    """A path a POSIX shell can actually use.

    Git Bash reads `C:\\Users\\x` as an escape soup — a stub that waits for
    such a path waits forever, which is how one un-skipped test hung the whole
    Windows job for 25 minutes. `/c/Users/x` is what it understands."""
    text = str(path)
    if not WINDOWS:
        return text
    text = text.replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return text


def sleeper_command(seconds: float) -> list:
    """A process that just stays alive. `sleep` is not a program on Windows."""
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def echo_command(text: str) -> list:
    """Print `text`. `/bin/echo` does not exist on Windows."""
    return [sys.executable, "-c",
            "import sys; sys.stdout.write(sys.argv[1])", text]


posix_only = unittest.skipIf(
    WINDOWS, "POSIX-only: Signale, Prozessgruppen oder /proc")

linux_only = unittest.skipUnless(
    sys.platform.startswith("linux"),
    "Linux-only: liest /proc/<pid>/environ — auf Windows und macOS gibt es das nicht")
