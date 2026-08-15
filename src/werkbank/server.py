"""The Werkbank board: a dependency-free HTTP server over the ticket files.

Run with:  python3 src/werkbank/server.py
Serves board.html on / and a JSON API under /api/tickets. Reads config.json
from the repo root for port and default project.
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from werkbank import dispatch, guard, projects, store

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TICKETS_DIR = REPO_ROOT / "tickets"
BOARD_HTML = Path(__file__).resolve().parent / "board.html"


def load_config():
    cfg = {
        "port": 8765,
        "default_project": str(REPO_ROOT),
        # Settings for agent runs started by dragging a ticket to 'In Arbeit':
        "agent_permission_mode": "acceptEdits",
        "agent_allowed_tools": "Bash",
        "agent_timeout_minutes": 30,
        # Per-project memory of the last ticket session (WB-14); local, not in git.
        "state_path": str(REPO_ROOT / "state.json"),
        # Named project list (WB-24); config.json normally carries its own.
        "projects": {},
        # Per-project: does a pending review block the queue? (WB-40)
        "nonblocking_review": {},
    }
    path = REPO_ROOT / "config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


CONFIG = load_config()
DISPATCHER = dispatch.Dispatcher(TICKETS_DIR, CONFIG)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        # Defense in depth (WB-35): no framing, no external resources.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    MAX_BODY = 1 << 20  # 1 MiB is plenty for a ticket (WB-35, F9)

    def _json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > self.MAX_BODY:
            raise ValueError("Anfrage zu groß.")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def _guarded(self, checker):
        """Refuse requests that a hostile web page could have caused (F1/F2)."""
        ok, reason = checker(self.headers, CONFIG.get("port", 8765))
        if not ok:
            self._send(403, {"error": reason})
        return ok

    def do_GET(self):
        if not self._guarded(guard.check_read):
            return
        if self.path in ("/", "/index.html"):
            self._send(200, BOARD_HTML.read_bytes(), "text/html; charset=utf-8")
        elif self.path.startswith("/api/browse"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            try:
                roots = [Path.home()] + [Path(v) for v in
                                         (CONFIG.get("projects") or {}).values()]
                self._send(200, projects.list_dirs(
                    (q.get("path") or [None])[0], roots=roots))
            except ValueError as e:
                self._send(400, {"error": str(e)})
        elif self.path == "/api/tickets":
            tickets, errors = store.load_tickets_with_errors(TICKETS_DIR)
            self._send(200, {"tickets": [t.to_dict() for t in tickets],
                             "errors": errors, "runs": DISPATCHER.active_runs(),
                             "config": CONFIG})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._guarded(guard.check_write):
            return
        try:
            if self.path == "/api/tickets":
                b = self._json_body()
                if not b.get("title", "").strip():
                    raise ValueError("Titel darf nicht leer sein")
                t = store.create_ticket(
                    TICKETS_DIR,
                    title=b["title"],
                    description=b.get("description", ""),
                    assignee=b.get("assignee", "claude"),
                    project=b.get("project") or CONFIG["default_project"],
                    priority=b.get("priority", "normal"),
                    type=b.get("type", "aufgabe"),
                    nach=b.get("nach", ""),
                    nicht_mit=b.get("nicht_mit", ""),
                    fork=b.get("fork", "nein"),
                )
                self._send(200, t.to_dict())
                return
            if self.path == "/api/projects":
                b = self._json_body()
                CONFIG["projects"] = projects.add_project(
                    REPO_ROOT / "config.json", b.get("name", ""), b.get("path", ""))
                self._send(200, {"projects": CONFIG["projects"]})
                return
            if self.path == "/api/projects/review-mode":
                b = self._json_body()
                CONFIG["nonblocking_review"] = projects.set_review_mode(
                    REPO_ROOT / "config.json", b.get("path", ""),
                    bool(b.get("nonblocking")))
                DISPATCHER.pump_queue()  # the change may unblock a queue
                self._send(200, {"nonblocking_review": CONFIG["nonblocking_review"]})
                return
            m = re.fullmatch(r"/api/tickets/(WB-\d+)", self.path)
            if m:
                b = self._json_body()
                all_tickets = store.load_tickets(TICKETS_DIR)
                before = {x.id: x for x in all_tickets}.get(m.group(1))
                # A blocked ticket must not start: reject BEFORE any change.
                if (before and b.get("status") == "in_arbeit"
                        and before.status in ("offen", "fehlgeschlagen")):
                    reasons = store.blocking_reasons(all_tickets, before)
                    if reasons:
                        self._send(409, {"error": "Nicht gestartet — " + "; ".join(reasons)})
                        return
                t = store.update_ticket(TICKETS_DIR, m.group(1), b)
                # Dragging offen/fehlgeschlagen -> in_arbeit starts the assigned
                # agent (from fehlgeschlagen it is a retry).
                if (before and t.status == "in_arbeit"
                        and before.status in ("offen", "fehlgeschlagen")):
                    if t.assignee == "claude":
                        DISPATCHER.dispatch(t.id)
                    else:
                        store.set_result(
                            TICKETS_DIR, t.id,
                            f"Automatischer Start für Bearbeiter '{t.assignee}' wird noch "
                            "nicht unterstützt — bitte im Chat abarbeiten lassen.")
                        t = store.update_ticket(TICKETS_DIR, t.id,
                                                {"status": "fehlgeschlagen"})
                # Accepting/rejecting a review or queueing a ticket can release
                # the project's queue (WB-40).
                DISPATCHER.pump_queue()
                self._send(200, t.to_dict())
                return
            self._send(404, {"error": "not found"})
        except store.ConflictError as e:
            self._send(409, {"error": str(e)})
        except (ValueError, KeyError) as e:
            self._send(400, {"error": str(e)})

    def do_DELETE(self):
        if not self._guarded(guard.check_write):
            return
        m = re.fullmatch(r"/api/tickets/(WB-\d+)", self.path)
        if not m:
            self._send(404, {"error": "not found"})
            return
        try:
            store.delete_ticket(TICKETS_DIR, m.group(1))
            self._send(200, {"deleted": m.group(1)})
        except KeyError:
            self._send(404, {"error": f"Ticket {m.group(1)} gibt es nicht (mehr)."})

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet; errors surface via HTTP status codes


def main():
    swept = dispatch.sweep_orphaned(TICKETS_DIR, CONFIG.get("state_path"))
    if swept:
        print(f"Verwaiste In-Arbeit-Tickets nach Fehlgeschlagen verschoben: {', '.join(swept)}")
    # Handovers that survived a restart get their claim deadline re-armed.
    for t in store.load_tickets(TICKETS_DIR):
        if t.status == "in_arbeit" and t.handover:
            DISPATCHER.arm_handover_fallback(t.id)
    port = CONFIG["port"]
    # host stays 127.0.0.1 unless the OWNER explicitly opts into LAN exposure
    # (WB-34): the board has no login and its agents execute commands.
    host = CONFIG.get("host", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Werkbank-Board läuft: http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
