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


def _ensure_lock_byte(fd) -> None:
    """Windows locks a BYTE RANGE, so the file needs at least one byte. Writing
    it unconditionally is what broke under contention: the byte is exactly what
    another process has locked, and writing into a locked range is refused with
    `PermissionError [Errno 13]` — measured in CI 2026-08-17, the second of two
    processes died there before doing any work. Write it only when the file is
    empty, and treat a refusal as proof that somebody else already did."""
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
    except (OSError, ValueError):
        pass          # already there, or locked by the holder — both are fine
    try:
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError:
        pass


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


def try_exclusive(path):
    """Try to take a NON-blocking exclusive lock on `path` (created if
    needed). Returns the open fd while the lock is held, or None if another
    process holds it. The lock dies with the fd — close it to release, or let
    process death release it (flock semantics), so a crashed holder can never
    leave a stale lock behind (WB-142: one dispatcher per tickets dir)."""
    try:
        fd = open_lock_file(path)
    except OSError:
        return None
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:  # pragma: no cover - Windows only
            os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return fd
    except OSError:
        os.close(fd)
        return None


def open_lock_file(path, attempts: int = 40, pause: float = 0.05) -> int:
    """Open (creating) the lock file, tolerating Windows' transient refusals.

    POSIX opens a lock file whatever else is going on. Windows can answer
    `PermissionError [Errno 13]` for a few milliseconds while another process
    is busy with the same file — measured in CI on 2026-08-17: two processes
    updating one ticket, one of them died on the LOCK, before doing any work.
    Retrying turns a sharing hiccup back into what it is: a wait."""
    last = None
    for _ in range(attempts):
        try:
            return os.open(path, os.O_CREAT | os.O_RDWR | NOFOLLOW, 0o600)
        except PermissionError as e:      # Windows only; POSIX does not do this
            last = e
            time.sleep(pause)
    raise last


@contextmanager
def exclusive(path):
    """Hold an exclusive cross-process lock on `path` (created if needed)."""
    fd = open_lock_file(path)
    try:
        _ensure_lock_byte(fd)
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
