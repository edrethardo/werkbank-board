"""One exclusive file lock that works on both Unix and Windows (WB-43).

The board, the dispatcher and every chat session write ticket files, so the
lock must hold ACROSS PROCESSES. POSIX has `fcntl.flock`, Windows has
`msvcrt.locking` — neither module exists on the other platform, so both
imports are optional and the choice is made once, here.
"""

import os
import time
from contextlib import contextmanager

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - platform dependent
    msvcrt = None

# Not available on Windows; 0 makes it a no-op flag there.
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _acquire(fd) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    if msvcrt is not None:  # pragma: no cover - Windows only
        os.lseek(fd, 0, os.SEEK_SET)
        deadline = time.monotonic() + 30
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)  # blocks ~10s, then raises
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise
    # No locking primitive at all: the in-process lock still serializes this
    # process, which is better than failing outright.


def _release(fd) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif msvcrt is not None:  # pragma: no cover - Windows only
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


@contextmanager
def exclusive(path):
    """Hold an exclusive cross-process lock on `path` (created if needed)."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | NOFOLLOW, 0o600)
    try:
        os.write(fd, b"0")  # msvcrt needs at least one byte to lock
        _acquire(fd)
        try:
            yield
        finally:
            _release(fd)
    finally:
        os.close(fd)


def replace_with_retry(src, dst, attempts: int = 20) -> None:
    """os.replace, but Windows can briefly refuse while a reader has the file
    open (POSIX never does). Retry instead of failing the write."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:  # pragma: no cover - Windows only
            if i == attempts - 1:
                raise
            time.sleep(0.05)
