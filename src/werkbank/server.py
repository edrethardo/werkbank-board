"""The Werkbank board: a dependency-free HTTP server over the ticket files.

Run with:  python3 src/werkbank/server.py
Serves board.html on / and a JSON API under /api/tickets. Reads config.json
from the repo root for port and default project.
"""

import errno
import ipaddress
import json
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from werkbank import auth, dispatch, guard, opencode, projects, setup, store, uploads

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TICKETS_DIR = REPO_ROOT / "tickets"
BOARD_HTML = Path(__file__).resolve().parent / "board.html"
UPLOAD_HTML = Path(__file__).resolve().parent / "upload.html"
# WB-104: uploads are private by default — gitignored, excluded from publish.
# Curated images that BELONG in the repo (README screenshots) live in
# docs/images/ and are committed deliberately, never via the upload page.
UPLOAD_DIR = REPO_ROOT / "uploads"


def load_config(path=None):
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
        # WB-204: after how many minutes a standing CHAT claim is called out
        # on the card. A board run has its own idle detection; a chat claim
        # is just a field, so time is the only honest signal there is.
        "chat_claim_warn_minutes": 10,
        # LAN mode (WB-44): reachable from other devices, password required.
        "lan": False,
        "password_hash": "",
        # WB-175: title-based assignee router. Regex FRAGMENTS (Python + JS
        # syntax stays a subset; keep them simple). Case-insensitive match
        # on the ticket TITLE. If both sides fire → claude wins (the WB-146
        # incident: "Claude-Läufe parallelisieren" ate $28.61, an opencode
        # misroute would have made the ticket unusable). If neither fires
        # → no suggestion. The user always overrides in the dialog; the
        # override is logged for later calibration.
        "assignee_router": {
            "opencode": ["doku", "typo", "kommentar", "test hinzuf(ü|ue)gen",
                         "readme", "changelog"],
            "claude": ["refactor", "design", "parallel", "epic", "security",
                       "nebenl(ä|ae)ufig", "concurren", "race", "rewrite"],
        },
    }
    path = Path(path) if path else REPO_ROOT / "config.json"
    cfg["config_exists"] = path.exists()
    cfg["repo_root"] = str(REPO_ROOT)
    if path.exists():
        # WB-236 round 2: the README tells the user to EDIT this file by hand,
        # and the commonest lay mistake — a trailing comma — used to end in a
        # raw JSONDecodeError traceback at import time: no German, no file
        # name, no remedy, in a tool that speaks German everywhere else. An
        # adversarial first-run review stopped exactly here.
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            print(f"Die Datei {path} ist nicht lesbar: In Zeile {e.lineno} "
                  f"stimmt etwas nicht ({e.msg}).\n"
                  "Das ist fast immer ein Komma zu viel oder zu wenig, oder "
                  "ein fehlendes Anführungszeichen. Häufigster Fall: nach dem "
                  "LETZTEN Eintrag darf kein Komma stehen.\n"
                  "Vergleiche mit config.example.json, oder benenne "
                  "config.json um und lass das Board eine neue anlegen.",
                  file=sys.stderr)
            raise SystemExit(1)
        cfg["config_exists"] = True
        cfg["repo_root"] = str(REPO_ROOT)   # never overridable from the file
    return cfg


CONFIG = load_config()
CONFIG_WARNING = setup.config_warning(CONFIG, CONFIG.get("config_exists", False),
                                      REPO_ROOT)
# WB-144: no dispatcher at import time. Importing this module (tests, tooling,
# help commands) used to spin up a real Dispatcher + ticker against the live
# tickets dir — the swarm night's rogue helpers got a foothold that way.
# get_dispatcher() lazily constructs the singleton on first real use (main()
# and every request handler); tests install their own with `server.DISPATCHER =`
# and stop it in tearDown.
DISPATCHER = None


# WB-140: adversarial reviewer runs. A background thread runs a fresh,
# tool-less `claude -p` (opencode.adversarial_review) so it does not block
# a request handler and does not compete for the dispatcher's lanes. One
# review at a time per ticket keeps the appended notes coherent and caps
# the (small) `claude.json` contention window; ticket-scoped set, not
# global, so different tickets can review in parallel if the user asks.
_REVIEWS_RUNNING: set = set()
_REVIEWS_LOCK = threading.Lock()


def _run_review(ticket_id: str) -> None:
    """Body of the review thread. Never raises — the outcome (either the
    reviewer's report or a short failure note) always lands on the ticket."""
    try:
        tickets = store.load_tickets(TICKETS_DIR)
        t = next((x for x in tickets if x.id == ticket_id), None)
        if t is None:
            return
        project = t.project or CONFIG.get("default_project", str(REPO_ROOT))
        # git log --grep matches our commit-message convention
        # ("WB-N: ..." / "Work tickets: WB-N ..."). --all so branches count too.
        try:
            hashes = subprocess.check_output(
                ["git", "-C", project, "log", "--all",
                 f"--grep={ticket_id}", "--pretty=%H"],
                text=True, timeout=30).split()
        except (subprocess.SubprocessError, OSError) as e:
            hashes = []
        diff = ""
        if hashes:
            try:
                diff = subprocess.check_output(
                    ["git", "-C", project, "show", "--format=%h %s",
                     "--stat", "--patch"] + hashes,
                    text=True, timeout=60)
            except (subprocess.SubprocessError, OSError) as e:
                diff = f"(git show fehlgeschlagen: {e})"
        else:
            # Fall back to the working-tree diff if no commit mentions us —
            # honest failure mode is "nothing to review", not "silent silence".
            try:
                diff = subprocess.check_output(
                    ["git", "-C", project, "diff", "HEAD"],
                    text=True, timeout=30)
            except (subprocess.SubprocessError, OSError):
                diff = ""
            if not diff:
                diff = (f"(kein committeter Diff und kein ungespeicherter "
                        f"Diff für {ticket_id} gefunden)")
        try:
            report, truncated, usage = opencode.adversarial_review(t.body, diff)
        except subprocess.SubprocessError as e:
            report, truncated, usage = (f"Reviewer-Lauf fehlgeschlagen: {e}",
                                        False, None)
        if truncated:
            report += "\n\n(Diff war zu groß und wurde für den Review gekürzt.)"
        try:
            # WB-170: usage carries this run's cost + tokens for the
            # section footer and the cumulative `review_cost_usd` field.
            store.append_review_note(TICKETS_DIR, ticket_id, report, usage=usage)
        except Exception:
            pass
    finally:
        with _REVIEWS_LOCK:
            _REVIEWS_RUNNING.discard(ticket_id)


def reviews_running() -> list:
    """WB-200: which tickets have a reviewer thread right now. The board shows
    it on the card and in the open ticket; before this, the only trace of a
    running review was the clicked button's own DOM state, which the board's
    five-second rebuild threw away — leaving one to two minutes in which a
    paid-for run looked like a button that did nothing."""
    with _REVIEWS_LOCK:
        return sorted(_REVIEWS_RUNNING)


def start_review(ticket_id: str) -> bool:
    """Kick off a review thread. False if one is already running for this
    ticket — the caller turns that into a 429."""
    with _REVIEWS_LOCK:
        if ticket_id in _REVIEWS_RUNNING:
            return False
        _REVIEWS_RUNNING.add(ticket_id)
    threading.Thread(target=_run_review, args=(ticket_id,),
                     daemon=True, name=f"werkbank-review-{ticket_id}").start()
    return True


def get_dispatcher():
    """Construct the singleton Dispatcher on first call. Tests may override
    by assigning to `server.DISPATCHER` before the first request lands."""
    global DISPATCHER
    if DISPATCHER is None:
        DISPATCHER = dispatch.Dispatcher(TICKETS_DIR, CONFIG)
    return DISPATCHER
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


def _router_log_path() -> Path:
    """WB-175: overrides land next to state.json — one line per event so a
    later `wc -l` / `jq` pass tells the owner how often the router misroutes."""
    state = CONFIG.get("state_path") or str(REPO_ROOT / "state.json")
    return Path(state).parent / "router_overrides.jsonl"


def _log_router_override(suggestion, chosen: str, title: str, ticket_id: str):
    """Best-effort append. A crash in logging must NOT prevent the ticket from
    being created (the user's action succeeded; a missing log line is not that
    important)."""
    if not suggestion or suggestion == chosen:
        return
    try:
        from datetime import datetime
        entry = {"ts": datetime.now().isoformat(timespec="seconds"),
                 "ticket": ticket_id, "title": title,
                 "suggested": suggestion, "chosen": chosen}
        with _router_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


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

    def _hot_reload(self):
        """WB-124: pick up projects a session registered by writing config.json
        (the register skill's path — no password, no HTTP). One stat per
        request; only projects/review-modes/gates are re-read (see HOT_KEYS)."""
        projects.refresh_from_disk(CONFIG, REPO_ROOT / "config.json")

    def do_GET(self):
        if not self._guarded(guard.check_read):
            return
        self._hot_reload()
        if self.path in ("/", "/index.html"):
            self._send(200, BOARD_HTML.read_bytes(), "text/html; charset=utf-8")
            return
        if self.path in ("/upload", "/upload/"):
            self._send(200, UPLOAD_HTML.read_bytes(), "text/html; charset=utf-8")
            return
        if not self._authenticated():
            self._send(401, {"error": "Bitte am Board anmelden."})
            return
        if self.path.startswith("/api/browse"):
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
            d = get_dispatcher()
            self._send(200, {"tickets": [t.to_dict() for t in tickets],
                             "errors": errors, "runs": d.active_runs(),
                             "reviews_running": reviews_running(),
                             "pause": d.pause_reason(),
                             "setup_warning": CONFIG_WARNING,
                             "config": public_config(CONFIG)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._guarded(guard.check_write):
            return
        self._hot_reload()
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
                    epic=b.get("epic", ""),
                    interactive=b.get("interactive", "nein"),
                )
                # WB-175: if the client sent along the router's suggestion
                # AND the user picked a different assignee, log the override
                # so the owner can calibrate the regex list. Silent no-op when
                # there is no suggestion or when the suggestion was taken.
                _log_router_override(b.get("router_suggestion"),
                                     t.assignee, t.title, t.id)
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
                get_dispatcher().pump_queue()  # the change may unblock a queue
                self._send(200, {"nonblocking_review": CONFIG["nonblocking_review"]})
                return
            m_bug = re.fullmatch(r"/api/tickets/(WB-\d+)/bug", self.path)
            if m_bug:
                b = self._json_body()
                bug = store.create_bug_for(TICKETS_DIR, m_bug.group(1),
                                           b.get("description", ""))
                self._send(200, bug.to_dict())
                return
            m_rev = re.fullmatch(r"/api/tickets/(WB-\d+)/review", self.path)
            if m_rev:
                # WB-140: an ADVERSARIAL reviewer, on demand. Runs in its own
                # thread with a fresh, tool-less claude -p (opencode.
                # adversarial_review); does not block the request or the
                # dispatcher lanes. One review at a time PER TICKET so
                # concurrent clicks do not double-charge or race the append.
                tid = m_rev.group(1)
                if not any(t.id == tid for t in store.load_tickets(TICKETS_DIR)):
                    self._send(404, {"error": "Ticket nicht gefunden"})
                    return
                if not start_review(tid):
                    self._send(429, {"error":
                        "Ein Review für dieses Ticket läuft bereits."})
                    return
                self._send(202, {"status": "review-läuft", "ticket": tid})
                return
            m_rq = re.fullmatch(r"/api/tickets/(WB-\d+)/requeue", self.path)
            if m_rq:
                # WB-204: take back a chat claim that is not moving. A LIVE
                # board run is refused — that ticket really is being worked,
                # and pulling it out from under the run would strand it
                # (WB-150 keeps the same promise for plain status writes).
                tid = m_rq.group(1)
                if tid in (get_dispatcher().active_runs() or {}):
                    self._send(409, {"error": "Für dieses Ticket läuft gerade ein "
                                              "Agenten-Lauf — es wird wirklich "
                                              "bearbeitet."})
                    return
                try:
                    t = store.release_claim(TICKETS_DIR, tid)
                except KeyError as e:
                    self._send(404, {"error": str(e)})
                    return
                except ValueError as e:
                    self._send(400, {"error": str(e)})
                    return
                get_dispatcher().pump_queue()
                self._send(200, t.to_dict())
                return
            m_to = re.fullmatch(r"/api/tickets/(WB-\d+)/move-to", self.path)
            if m_to:
                # WB-203: drag and drop INSIDE the queue column. The board
                # sends the target position within the ticket's own lane
                # (same project and priority); the store clamps it.
                try:
                    idx = int(self._json_body().get("index", 0))
                except (TypeError, ValueError):
                    self._send(400, {"error": "index muss eine Zahl sein"})
                    return
                try:
                    t = store.move_queued_to(TICKETS_DIR, m_to.group(1), idx)
                    self._send(200, t.to_dict())
                except KeyError as e:
                    self._send(404, {"error": str(e)})
                except ValueError as e:
                    self._send(400, {"error": str(e)})
                return
            m_up = re.fullmatch(r"/api/tickets/(WB-\d+)/move-up", self.path)
            if m_up:
                # WB-138: move a queued ticket one place forward within its
                # priority. Empty body is fine; a locked helper does the swap.
                try:
                    t = store.move_queued_up(TICKETS_DIR, m_up.group(1))
                    self._send(200, t.to_dict())
                except KeyError as e:
                    self._send(404, {"error": str(e)})
                except ValueError as e:
                    self._send(400, {"error": str(e)})
                return
            m_ans = re.fullmatch(r"/api/tickets/(WB-\d+)/answer", self.path)
            if m_ans:
                # WB-123: pure function in dispatch keeps the transitions
                # testable without spinning up an HTTP server.
                code, payload = dispatch.answer_ticket(
                    TICKETS_DIR, m_ans.group(1), self._json_body(),
                    get_dispatcher())
                self._send(code, payload)
                return
            m = re.fullmatch(r"/api/tickets/(WB-\d+)", self.path)
            if m:
                b = self._json_body()
                all_tickets = store.load_tickets(TICKETS_DIR)
                before = {x.id: x for x in all_tickets}.get(m.group(1))
                # WB-150: an in-progress ticket cannot be dragged out — the
                # agent is still running, and moving the card would only lie
                # about it. `rueckfrage` is the same lane paused for the
                # user's answer; leaving it goes through the answer form
                # (POST /answer), not by rewriting the status. Both stay put
                # until the run itself moves them to review/fehlgeschlagen.
                # Dispatcher writes use `store.update_ticket` directly and
                # bypass this check on purpose.
                if (before and "status" in b
                        and b["status"] != before.status
                        and before.status in ("in_arbeit", "rueckfrage")):
                    if before.status == "in_arbeit":
                        msg = ("Ticket in „In Arbeit“ kann nicht verschoben "
                               "werden — der Agent muss zuerst fertig werden.")
                    else:
                        msg = ("Ticket in „Rückfrage“ kann nicht verschoben "
                               "werden — bitte über das Antwortfeld auf der Karte "
                               "reagieren.")
                    self._send(409, {"error": msg})
                    return
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
                    if dispatch.known_assignee(t.assignee):
                        get_dispatcher().dispatch(t.id)
                    else:
                        store.set_result(
                            TICKETS_DIR, t.id,
                            f"Automatischer Start für Bearbeiter '{t.assignee}' wird noch "
                            "nicht unterstützt — bitte im Chat abarbeiten lassen.")
                        t = store.update_ticket(TICKETS_DIR, t.id,
                                                {"status": "fehlgeschlagen"})
                # Accepting/rejecting a review or queueing a ticket can release
                # the project's queue (WB-40).
                get_dispatcher().pump_queue()
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


# Every interface, in the spellings a socket accepts. "" is the dangerous one:
# `bind(("", port))` means INADDR_ANY, so the value that reads like "nothing
# configured" is exactly the one that opens the board to the whole network.
ALL_INTERFACES = {"", "0.0.0.0", "::", "*", "0", "::0"}


def binds_locally_only(host) -> bool:
    """True only when this address can be reached from THIS machine alone.

    Judged by what the address means to the socket layer, not by matching
    strings: an adversarial review found that `host: ""` slipped through a
    string allow-list while binding 0.0.0.0 — with `lan` off there is no login
    at all, so that was an unauthenticated path to running commands on this
    machine. A hostname we cannot resolve to a loopback address counts as
    exposing: refusing to start is recoverable, guessing wrong is not."""
    text = (host or "").strip()
    if text.lower() in ALL_INTERFACES:
        return False
    if text.lower() in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(text.strip("[]")).is_loopback
    except ValueError:
        return False


def exposure_refusal(host: str, lan: bool, password_hash: str):
    """German reason to refuse startup, or None.

    The rule "no network without a password" lived only in `setup.set_lan`, i.e.
    in the CLI helper — not at the boundary it protects. Hand-editing
    `config.json` (the obvious thing to try, since the field is called `lan`)
    produced a board bound to 0.0.0.0 with `auth_required()` False: NO login,
    on the whole network, on a tool whose tickets execute shell commands.
    The check belongs where the socket is opened."""
    if binds_locally_only(host):
        return None
    # NOTE: a locally-bound board with `lan: true` starts and simply ignores
    # the flag. That is the SAFE direction, but it is also a silent no-op —
    # `setup_note_for` below says so out loud at startup, because "nothing
    # happens and nobody says why" is this project's most-hit failure mode.
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


def lan_note(host: str, lan: bool, password_hash: str):
    """WB-236 round 2: `lan: true` with a local `host` is not an error — the
    board serves normally — but the network mode the user asked for does not
    happen. An adversarial first-run review called it out as a silent no-op,
    and the README claimed a refusal that only fires for a non-local host."""
    if not lan or not binds_locally_only(host):
        return None
    return (f"Hinweis: In config.json steht 'lan': true, aber host={host!r} — "
            "das Board ist damit NUR auf diesem Rechner erreichbar, der "
            "Netzwerk-Modus bleibt wirkungslos. Beides zusammen setzt: "
            "'python3 src/werkbank/server.py --set-password', dann '--lan-on'.")


def boot():
    """Everything that must happen before the socket opens — sweep, dispatcher,
    handover deadlines. Split out of `main()` so it can be tested (WB-229): the
    bug it fixes lived exactly here and no test could reach it.

    WB-229: the Dispatcher is now built UNCONDITIONALLY. It used to be
    constructed lazily, and `main()` only reached `get_dispatcher()` when a
    handover marker happened to survive the restart — otherwise the first call
    came from an AUTHENTICATED request handler. With no browser tab open that
    never happens: no dispatcher, no ticker thread, and the queue lies still
    with no error anywhere. Measured on the running service 2026-08-18: one
    thread, no `.dispatcher.lock` held, a queued ticket untouched for 20+
    minutes. During the day a tab is always open, which is why this survived
    so long; headless it means "tickets never start"."""
    swept = dispatch.sweep_orphaned(TICKETS_DIR, CONFIG.get("state_path"))
    if swept:
        print(f"Verwaiste In-Arbeit-Tickets nach Fehlgeschlagen verschoben: {', '.join(swept)}")
    d = get_dispatcher()
    # Handovers that survived a restart get their claim deadline re-armed.
    for t in store.load_tickets(TICKETS_DIR):
        if t.status == "in_arbeit" and t.handover:
            d.arm_handover_fallback(t.id)
    return d


def main():
    # WB-184: refuse BEFORE touching anything. A board that will not start must
    # not first sweep tickets to fehlgeschlagen on its way out — the fresh-
    # machine test caught exactly that: the refusal path marked a healthy
    # in_arbeit ticket as failed and then exited.
    refusal = exposure_refusal(CONFIG.get("host", "0.0.0.0" if LAN else "127.0.0.1"),
                               LAN, CONFIG.get("password_hash", ""))
    if refusal:
        print(refusal, file=sys.stderr)
        raise SystemExit(1)
    if CONFIG_WARNING:
        print("ACHTUNG: " + CONFIG_WARNING)
    claude_warning = setup.claude_warning(cfg=CONFIG)
    if claude_warning:
        print("Hinweis: " + claude_warning)
    note = lan_note(CONFIG.get("host", "127.0.0.1" if not LAN else "0.0.0.0"),
                    LAN, CONFIG.get("password_hash", ""))
    if note:
        print(note)
    boot()
    port = CONFIG["port"]
    # host stays 127.0.0.1 unless the OWNER explicitly opts into LAN exposure
    # (WB-34): the board's agents execute commands, so reaching it is enough.
    host = CONFIG.get("host", "0.0.0.0" if LAN else "127.0.0.1")
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        # WB-49: a first-time user must not meet a raw traceback here.
        if e.errno == errno.EADDRINUSE:   # 98 Linux, 48 macOS, 100 Windows            # Linux/macOS: address already in use
            print(setup.port_busy_message(port))
            raise SystemExit(1)
        raise
    # flush: off a terminal (systemd, nohup, a pipe) Python buffers this
    # line, so the one message that says the board is up can arrive minutes
    # late or not at all.
    print(f"Werkbank-Board läuft: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # WB-236 round 2: the README says "stop it with Ctrl-C", and Ctrl-C
        # printed a KeyboardInterrupt traceback every single time — a wall of
        # English that reads like a crash on the one action the docs teach.
        print("\nBoard beendet. Die Tickets liegen weiter in tickets/ — "
              "beim nächsten Start ist alles wieder da.")
    finally:
        server.server_close()


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
