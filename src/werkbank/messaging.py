"""WB-258: direct delivery to a Claude Code chat session via its messaging
socket, so a handover reaches the session at once and a dead session is
detected immediately instead of after the 5-minute fallback wait.

Protocol is undocumented and gated per session file: only `peerProtocol == 1`
is spoken here; anything else falls through so the marker-only path (the one
this replaces for the happy case) still works.
"""

import json
import socket as _socket
import uuid
from enum import Enum
from pathlib import Path


SUPPORTED_PROTOCOL = 1
DEFAULT_SESSIONS_DIR = Path.home() / ".claude" / "sessions"
CONNECT_TIMEOUT_S = 2.0


class DeliveryResult(str, Enum):
    """Every outcome of a delivery attempt. Callers branch on this; nothing
    in this module raises."""

    DELIVERED = "delivered"
    NO_SESSION_FILE = "no_session_file"
    WRONG_PROTOCOL = "wrong_protocol"
    DEAD_SOCKET = "dead_socket"
    # WB-263: the platform has no unix sockets at all (Windows). Distinct from
    # ERROR so the caller can fall back instead of reporting a failure.
    NO_SOCKET_SUPPORT = "no_socket_support"
    ERROR = "error"


def find_session(session_id, sessions_dir=None):
    """Scan `sessions_dir` for a file with `sessionId == session_id` and
    return `(socket_path, protocol)`. Returns None if no match, or if the
    match has no socket path. Unreadable or malformed files are skipped;
    they are never a reason to abort the scan."""
    root = Path(sessions_dir) if sessions_dir else DEFAULT_SESSIONS_DIR
    if not root.is_dir():
        return None
    for entry in sorted(root.glob("*.json")):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("sessionId") != session_id:
            continue
        socket_path = data.get("messagingSocketPath")
        if not isinstance(socket_path, str) or not socket_path:
            return None
        return (socket_path, data.get("peerProtocol"))
    return None


def _payload(text: str) -> dict:
    return {
        "msgV": 1,
        "msg_id": str(uuid.uuid4()),
        "type": "user",
        "message": {
            "role": "user",
            "content": (
                '<cross-session-message from="werkbank" '
                'from-name="werkbank-board" from-mode="prompting">\n'
                + text + "\n</cross-session-message>"
            ),
        },
        "priority": "next",
        "from": "werkbank",
    }


def deliver(session_id: str, text: str, sessions_dir=None) -> "DeliveryResult":
    """Attempt to deliver `text` to the chat with `sessionId == session_id`.
    Never raises — every failure surfaces as a DeliveryResult value the
    caller can act on."""
    found = find_session(session_id, sessions_dir)
    if found is None:
        return DeliveryResult.NO_SESSION_FILE
    socket_path, protocol = found
    if protocol != SUPPORTED_PROTOCOL:
        return DeliveryResult.WRONG_PROTOCOL
    # WB-263: Windows has no AF_UNIX, so the attribute itself is missing and
    # `_socket.AF_UNIX` raises AttributeError — which this function promises
    # never to do. Uncaught, the dispatcher turned it into a FAILED ticket
    # instead of falling back to a background run, on the one platform where
    # the fallback is the ONLY path. Found by an adversarial review; nobody
    # has ever started the board on Windows, so nobody had seen it.
    if not hasattr(_socket, "AF_UNIX"):
        return DeliveryResult.NO_SOCKET_SUPPORT
    line = (json.dumps(_payload(text)) + "\n").encode("utf-8")
    try:
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as sock:
            sock.settimeout(CONNECT_TIMEOUT_S)
            sock.connect(socket_path)
            sock.sendall(line)
    except (ConnectionRefusedError, FileNotFoundError):
        return DeliveryResult.DEAD_SOCKET
    except OSError:
        return DeliveryResult.ERROR
    return DeliveryResult.DELIVERED


def handover_text(ticket_id: str, title: str) -> str:
    """The German poke the chat sees when the board hands it a ticket."""
    return (
        f"Übergabe von der Werkbank: du hast Ticket {ticket_id} "
        f"„{title}“ bekommen. Starte den werkbank-pull-ticket-Skill, "
        f"um die Übergabe anzunehmen."
    )
