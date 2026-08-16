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
from werkbank import auth, dispatch, guard, projects, setup, store, uploads

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TICKETS_DIR = REPO_ROOT / "tickets"
BOARD_HTML = Path(__file__).resolve().parent / "board.html"
UPLOAD_HTML = Path(__file__).resolve().parent / "upload.html"
UPLOAD_DIR = REPO_ROOT / "docs" / "images"


def load_config():
    cfg = {
        "port": 8765,
        "default_project": str(REPO_ROOT),
        # Settings for agent runs started by dragging a ticket to 'In Arbeit':
        "agent_permission_mode": "acceptEdits",
        "agent_allowed_tools": "Bash",
        "agent_timeout_minutes": 30,
        # The opencode lane's own budget (WB-94) — local runs are slower.
        "opencode_timeout_minutes": 60,
        # Per-project memory of the last ticket session (WB-14); local, not in git.
        "state_path": str(REPO_ROOT / "state.json"),
        # Named project list (WB-24); config.json normally carries its own.
        "projects": {},
        # Per-project: does a pending review block the queue? (WB-40)
        "nonblocking_review": {},
        # LAN mode (WB-44): reachable from other devices, password required.
        "lan": False,
        "password_hash": "",
    }
    path = REPO_ROOT / "config.json"
    cfg["config_exists"] = path.exists()
    cfg["repo_root"] = str(REPO_ROOT)
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
        cfg["config_exists"] = True
        cfg["repo_root"] = str(REPO_ROOT)   # never overridable from the file
    return cfg


CONFIG = load_config()
CONFIG_WARNING = setup.config_warning(CONFIG, CONFIG.get("config_exists", False),
                                      REPO_ROOT)
DISPATCHER = dispatch.Dispatcher(TICKETS_DIR, CONFIG)
LAN = bool(CONFIG.get("lan"))
SECRET = auth.load_or_create_secret(dispatch.log_dir().parent / "session-secret")
LOGIN_GATE = auth.LoginGate()


def auth_required() -> bool:
    """Only guard the board when it is reachable beyond this machine."""
    return LAN and bool(CONFIG.get("password_hash"))


def public_config(cfg: dict) -> dict:
    """What the board page may see. The password hash never leaves the server —
    it is the credential material, and a browser has no use for it. Gates are
    reduced to their NAMES: the commands stay here, so a page (or anything that
    reads its traffic) never learns what runs on this machine."""
    safe = {k: v for k, v in cfg.items() if k != "password_hash"}
    safe["gates"] = {project: sorted(names)
                     for project, names in
                     ((p, (g if isinstance(g, dict) else {"standard": g}))
                      for p, g in (cfg.get("gates") or {}).items())}
    return safe


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

    def _json_body(self, max_bytes=None):
        length = int(self.headers.get("Content-Length", 0))
        if length > (max_bytes or self.MAX_BODY):
            raise ValueError("Anfrage zu groß.")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def _authenticated(self) -> bool:
        if not auth_required():
            return True
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "werkbank_session" and auth.check_token(value, SECRET):
                return True
        return False

    def _login(self):
        client = self.client_address[0]
        if LOGIN_GATE.is_locked(client):
            self._send(429, {"error": "Zu viele Fehlversuche — bitte kurz warten."})
            return
        body = self._json_body()
        if not auth.verify_password(body.get("password", ""),
                                    CONFIG.get("password_hash", "")):
            LOGIN_GATE.record_failure(client)
            self._send(401, {"error": "Falsches Passwort."})
            return
        LOGIN_GATE.record_success(client)
        token = auth.make_token(SECRET)
        data = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie",
                         f"werkbank_session={token}; Path=/; Max-Age={auth.TOKEN_TTL}; "
                         "HttpOnly; SameSite=Strict")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _guarded(self, checker):
        """Refuse requests that a hostile web page could have caused (F1/F2)."""
        ok, reason = checker(self.headers, CONFIG.get("port", 8765), LAN)
        if not ok:
            self._send(403, {"error": reason})
        return ok

    def do_GET(self):
        if not self._guarded(guard.check_read):
            return
        if self.path in ("/", "/index.html"):
            self._send(200, BOARD_HTML.read_bytes(), "text/html; charset=utf-8")
            return
        if self.path in ("/upload", "/upload/"):
            self._send(200, UPLOAD_HTML.read_bytes(), "text/html; charset=utf-8")
            return
        if not self._authenticated():
            self._send(401, {"error": "Bitte am Board anmelden."})
            return
        if False:
            pass
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
        elif self.path == "/api/uploads":
            names = sorted(p.name for p in UPLOAD_DIR.glob("*")
                           if p.is_file() and not p.name.endswith(".py"))
            self._send(200, {"files": names, "dir": str(UPLOAD_DIR)})
        elif self.path == "/api/tickets":
            tickets, errors = store.load_tickets_with_errors(TICKETS_DIR)
            self._send(200, {"tickets": [t.to_dict() for t in tickets],
                             "errors": errors, "runs": DISPATCHER.active_runs(),
                             "pause": DISPATCHER.pause_reason(),
                             "setup_warning": CONFIG_WARNING,
                             "config": public_config(CONFIG)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._guarded(guard.check_write):
            return
        try:
            if self.path == "/api/login":
                self._login()
                return
            if not self._authenticated():
                self._send(401, {"error": "Bitte am Board anmelden."})
                return
            if self.path == "/api/upload":
                b = self._json_body(max_bytes=25 * 1024 * 1024)
                raw = uploads.decode_payload(b.get("data", ""))
                name = uploads.save_image(UPLOAD_DIR, b.get("name", ""), raw)
                self._send(200, {"saved": name, "dir": str(UPLOAD_DIR)})
                return
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
                    gate=b.get("gate", ""),
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
            m_bug = re.fullmatch(r"/api/tickets/(WB-\d+)/bug", self.path)
            if m_bug:
                b = self._json_body()
                bug = store.create_bug_for(TICKETS_DIR, m_bug.group(1),
                                           b.get("description", ""))
                self._send(200, bug.to_dict())
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
                    if t.assignee in ("claude", "opencode"):
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
        if not self._authenticated():
            self._send(401, {"error": "Bitte am Board anmelden."})
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


def exposure_refusal(host: str, lan: bool, password_hash: str):
    """German reason to refuse startup, or None.

    The rule "no network without a password" lived only in `setup.set_lan`, i.e.
    in the CLI helper — not at the boundary it protects. Hand-editing
    `config.json` (the obvious thing to try, since the field is called `lan`)
    produced a board bound to 0.0.0.0 with `auth_required()` False: NO login,
    on the whole network, on a tool whose tickets execute shell commands.
    The check belongs where the socket is opened."""
    local = {"127.0.0.1", "localhost", "::1", ""}
    if (host or "").strip() in local:
        return None
    if not lan:
        return ("Start abgebrochen: In config.json steht host="
                f"{host!r}, aber der Netzwerk-Modus ist aus. Von Hand gesetztes "
                "host öffnet das Board für das ganze Netz OHNE Anmeldung — und "
                "wer das Board erreicht, kann darüber Befehle auf diesem Rechner "
                "ausführen lassen.\n"
                "Richtig: erst 'python3 src/werkbank/server.py --set-password', "
                "dann '--lan-on'. Oder host wieder auf 127.0.0.1 setzen.")
    if not password_hash:
        return ("Start abgebrochen: Netzwerk-Modus ist an, aber es ist kein "
                "Passwort gesetzt — das Board wäre für jeden im Netz ohne "
                "Anmeldung offen, und darüber lassen sich Befehle auf diesem "
                "Rechner ausführen.\n"
                "Setz erst ein Passwort: "
                "'python3 src/werkbank/server.py --set-password'.")
    return None


def main():
    if CONFIG_WARNING:
        print("ACHTUNG: " + CONFIG_WARNING)
    claude_warning = setup.claude_warning()
    if claude_warning:
        print("Hinweis: " + claude_warning)
    swept = dispatch.sweep_orphaned(TICKETS_DIR, CONFIG.get("state_path"))
    if swept:
        print(f"Verwaiste In-Arbeit-Tickets nach Fehlgeschlagen verschoben: {', '.join(swept)}")
    # Handovers that survived a restart get their claim deadline re-armed.
    for t in store.load_tickets(TICKETS_DIR):
        if t.status == "in_arbeit" and t.handover:
            DISPATCHER.arm_handover_fallback(t.id)
    port = CONFIG["port"]
    # host stays 127.0.0.1 unless the OWNER explicitly opts into LAN exposure
    # (WB-34): the board's agents execute commands, so reaching it is enough.
    host = CONFIG.get("host", "0.0.0.0" if LAN else "127.0.0.1")
    refusal = exposure_refusal(host, LAN, CONFIG.get("password_hash", ""))
    if refusal:
        print(refusal, file=sys.stderr)
        raise SystemExit(1)
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        # WB-49: a first-time user must not meet a raw traceback here.
        if e.errno in (98, 48):            # Linux/macOS: address already in use
            print(setup.port_busy_message(port))
            raise SystemExit(1)
        raise
    print(f"Werkbank-Board läuft: http://{host}:{port}")
    server.serve_forever()


def cli(argv):
    """Small command line so nobody needs Python to configure the board (WB-50)."""
    import getpass
    cfg_path = REPO_ROOT / "config.json"
    arg = argv[1] if len(argv) > 1 else ""
    try:
        if arg == "--set-password":
            pw = getpass.getpass("Neues Board-Passwort: ")
            again = getpass.getpass("Nochmal zur Sicherheit: ")
            if pw != again:
                print("Die beiden Eingaben sind nicht gleich — nichts geändert.")
                return 1
            print(setup.set_password(
                cfg_path, pw,
                secret_path=dispatch.log_dir().parent / "session-secret"))
            return 0
        if arg in ("--lan-on", "--lan-off"):
            print(setup.set_lan(cfg_path, arg == "--lan-on"))
            return 0
        if arg in ("-h", "--help"):
            print("Werkbank-Board\n"
                  "  (ohne Argument)     Board starten\n"
                  "  --set-password      Passwort für den Netzwerk-Modus setzen\n"
                  "  --lan-on            Board im Heimnetz erreichbar machen\n"
                  "  --lan-off           wieder nur auf diesem Rechner")
            return 0
    except ValueError as e:
        print(str(e))
        return 1
    if arg:
        print(f"Unbekannte Option: {arg} — siehe --help")
        return 1
    main()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv))
