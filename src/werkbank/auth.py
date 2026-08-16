"""Password login for the board (WB-44).

Only needed when the board is reachable beyond this machine (`lan: true`).
The password is never stored — only a salted PBKDF2 hash. A successful login
hands out a signed, expiring token that the browser keeps in a cookie; the
signing secret lives in the state dir so a board restart does not log the
phone out again.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

ITERATIONS = 240_000
TOKEN_TTL = 30 * 24 * 3600  # a phone should not have to log in every day


def hash_password(password: str) -> str:
    password = (password or "").strip()
    if not password:
        raise ValueError("Das Passwort darf nicht leer sein.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", (password or "").encode(),
                                     bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def load_or_create_secret(path) -> bytes:
    """Signing secret, persisted 0600 so sessions survive a restart."""
    path = Path(path)
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(secret)
    return secret


def rotate_secret(path) -> bytes:
    """Throw the signing secret away and make a new one — every session token
    ever issued stops verifying.

    This is what "I changed the password" has to mean. A 30-day cookie on a lost
    phone is a working key to a board whose tickets run shell commands, and
    changing the password alone would not have taken it away."""
    path = Path(path)
    if path.exists():
        path.unlink()
    return load_or_create_secret(path)


def make_token(secret: bytes, ttl_seconds: int = TOKEN_TTL) -> str:
    expires = str(int(time.time()) + int(ttl_seconds))
    sig = hmac.new(secret, expires.encode(), hashlib.sha256).digest()
    return f"{expires}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def check_token(token: str, secret: bytes) -> bool:
    try:
        expires, sig = (token or "").split(".", 1)
        if int(expires) < time.time():
            return False
        expected = hmac.new(secret, expires.encode(), hashlib.sha256).digest()
        expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(sig, expected_b64)


class LoginGate:
    """Slows down guessing: N failures per client address lock it for a while."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._failures = {}

    def record_failure(self, client: str) -> None:
        now = time.time()
        kept = [t for t in self._failures.get(client, []) if now - t < self.window]
        kept.append(now)
        self._failures[client] = kept

    def record_success(self, client: str) -> None:
        self._failures.pop(client, None)

    def is_locked(self, client: str) -> bool:
        now = time.time()
        kept = [t for t in self._failures.get(client, []) if now - t < self.window]
        self._failures[client] = kept
        return len(kept) >= self.max_attempts
