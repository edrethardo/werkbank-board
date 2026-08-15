"""Dispatching a ticket to a Claude Code run when it is dragged to 'In Arbeit'.

The worker resumes the remembered ticket session of the target project. Per
ticket the user chooses (fork field, default "nein") whether it grows in place
as one continuous conversation or is forked (`--fork-session`). Without a
remembered ticket session the fallback (--continue/fresh) ALWAYS forks so no
foreign conversation is mutated.

Runs are strictly serialized: concurrent `claude -p` processes are known to
corrupt ~/.claude/claude.json (github.com/anthropics/claude-code issues
#29051, #28813).
"""

import inspect
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from werkbank import store


class DispatchError(Exception):
    pass


# Per-project memory of the last ticket session, so the next run forks the
# ticket lineage — NOT whatever conversation happened to be active last
# (--continue picks the most recently touched session; see WB-14).
DEFAULT_STATE = Path(__file__).resolve().parents[2] / "state.json"


def project_slug(project: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", project)


def project_has_history(project: str, projects_root=None) -> bool:
    root = Path(projects_root) if projects_root else Path.home() / ".claude" / "projects"
    return any((root / project_slug(project)).glob("*.jsonl"))


def load_last_entry(project: str, state_path=None):
    """Normalized state entry {"id": str, "interactive": bool} or None.
    Legacy plain-string entries (pre-WB-19) count as non-interactive."""
    try:
        data = json.loads(Path(state_path or DEFAULT_STATE).read_text(encoding="utf-8"))
        raw = data.get(str(project))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(raw, str) and raw:
        return {"id": raw, "interactive": False}
    if isinstance(raw, dict) and isinstance(raw.get("id"), str) and raw["id"]:
        return {"id": raw["id"], "interactive": bool(raw.get("interactive"))}
    return None


def load_last_session(project: str, state_path=None):
    entry = load_last_entry(project, state_path)
    return entry["id"] if entry else None


def save_last_session(project: str, session_id: str, state_path=None,
                      interactive: bool = False):
    path = Path(state_path or DEFAULT_STATE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    # Non-interactive entries keep the legacy plain-string form.
    data[str(project)] = ({"id": session_id, "interactive": True}
                          if interactive else session_id)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def register_ticket_session(project: str, session_id, state_path=None):
    """Called by INTERACTIVE sessions (work-tickets / pull flows) after working
    a ticket. The id must come from $CLAUDE_CODE_SESSION_ID — never guessed;
    an empty value is rejected so callers skip registration instead."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id fehlt — Registrierung übersprungen (nie raten)")
    save_last_session(project, session_id.strip(), state_path, interactive=True)


def attempt_modes(remembered_session, has_history: bool) -> list:
    """Fallback chain: remembered ticket session -> latest project session ->
    fresh. Each entry is (mode, resume_id)."""
    modes = []
    if remembered_session:
        modes.append(("resume", remembered_session))
    if has_history:
        modes.append(("continue", None))
    modes.append(("fresh", None))
    return modes


BUG_DISCIPLINE = """
Dies ist ein Bug-Ticket — arbeite es mit Debugging-Disziplin ab:
1. Stelle den Fehler zuerst nach (Ursache belegen, nicht raten).
2. Behebe die Ursache, nicht nur das Symptom.
3. Schreibe einen Regressionstest, der ohne den Fix fehlschlägt und mit ihm besteht.
Der Nachweis (wie nachgestellt, welcher Test) gehört in deine Abschlussantwort.
"""


def build_prompt(t) -> str:
    kind = "das Werkbank-Bug-Ticket" if t.type == "bug" else "das Werkbank-Ticket"
    return f"""Du setzt die letzte Session dieses Projekts fort und übernimmst jetzt {kind} {t.id} (Titel: {t.title}).

{t.body}
{BUG_DISCIPLINE if t.type == "bug" else ""}
Arbeite dieses Ticket JETZT im aktuellen Projektverzeichnis ab. Die Regeln des Projekts gelten (CLAUDE.md, Commit-Disziplin). Ändere nichts außerhalb des Projektverzeichnisses; pushe nur, wenn die Projektregeln das ausdrücklich vorsehen. Das Ticket-File selbst fasst du nicht an — das erledigt die Werkbank. Starte außerdem niemals das Werkbank-Board neu (den Server-Prozess auf dem Board-Port): Mit ihm stirbt der Schritt, der dein Ergebnis ins Ticket schreibt. Wenn ein Neustart nötig ist, schreibe das als Bitte in deine Abschlussantwort.

WICHTIG: Deine letzte Antwort wird wörtlich als Ergebnis in das Ticket übernommen. Sie muss eine kurze, ehrliche Zusammenfassung auf Deutsch sein: was getan wurde, was geprüft wurde, was fehlschlug oder offen blieb."""


def build_command(claude_bin: str, t, mode: str, cfg: dict, resume_id=None,
                  force_fork: bool = False) -> list:
    cmd = [claude_bin, "-p"]
    if mode == "resume":
        # fork=nein (default): the remembered ticket session grows as one
        # continuous conversation. fork=ja: work on a forked copy instead.
        # force_fork: the remembered session is an INTERACTIVE one (WB-19) —
        # an open conversation is never written into (two-writers problem),
        # so it is always forked regardless of the checkbox.
        cmd += ["--resume", resume_id]
        if force_fork or getattr(t, "fork", "nein") == "ja":
            cmd += ["--fork-session"]
    elif mode == "continue":
        # Safety rule (Werkbank, not user): no remembered ticket session means
        # --continue would grow an arbitrary foreign conversation (e.g. the
        # user's live chat). ALWAYS fork here, whatever the ticket says.
        cmd += ["--continue", "--fork-session"]
    cmd += [
        "--permission-mode", cfg.get("agent_permission_mode", "acceptEdits"),
        "--allowedTools", cfg.get("agent_allowed_tools", "Bash"),
        # stream-json (WB-37): events arrive WHILE the agent works, so the board
        # can show progress and notice a stall instead of waiting for the end.
        "--output-format", "stream-json", "--verbose",
        build_prompt(t),
    ]
    return cmd


# Phrases that mean "not your code's fault, the account ran out" (WB-37).
LIMIT_HINTS = ("usage limit", "rate limit", "session limit", "quota",
               "credit balance", "insufficient credit")


def classify_failure(text: str):
    """Turn an agent failure into a plain-German cause, or None if unknown."""
    low = (text or "").lower()
    if any(h in low for h in LIMIT_HINTS):
        return ("Nutzungslimit erreicht — der Agent durfte nicht weiterarbeiten. "
                "Später mit „Erneut versuchen“ nochmal starten.")
    if "authentication" in low or "unauthorized" in low or "login" in low:
        return "Anmeldung bei Claude fehlgeschlagen — bitte einmal neu anmelden."
    return None


def _consume_event(ev: dict, progress: dict) -> None:
    """Fold one stream-json event into the live progress picture."""
    if ev.get("session_id"):
        progress["session"] = ev["session_id"]
    # The CLI reports quota state itself (verified live 2026-08-15): status
    # allowed | allowed_warning | rejected, utilization 0..1, rateLimitType.
    info = ev.get("rate_limit_info")
    if isinstance(info, dict):
        progress["limit"] = {
            "percent": int(round((info.get("utilization") or 0) * 100)),
            "kind": info.get("rateLimitType"),
            "resets_at": info.get("resetsAt"),
            "blocked": info.get("status") not in (None, "allowed", "allowed_warning"),
        }
    for c in ((ev.get("message") or {}).get("content") or []):
        if isinstance(c, dict) and c.get("type") == "tool_use":
            progress["steps"] += 1
            progress["last_tool"] = c.get("name")
    usage = ev.get("usage") or (ev.get("message") or {}).get("usage") or {}
    if usage:
        total = (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0)
        progress["tokens"] = max(progress["tokens"], total)


def log_dir() -> Path:
    """Private log directory (WB-35, F6): /tmp is world-readable and its names
    are predictable, so a local user could read agent transcripts or pre-plant
    a symlink. Logs live in the user's state dir with 0700."""
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    p = Path(base) / "werkbank" / "logs"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def _log_path(ticket_id: str) -> Path:
    return log_dir() / f"werkbank-agent-{ticket_id}.log"


def _open_log(path: Path):
    """Append-only, no symlink following, owner-readable only."""
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "ab")


def run_claude(t, cfg: dict, on_start=None, on_event=None):
    """Run a claude process for the ticket; returns (result_text, session_id).
    session_id is the run's own id from the JSON output, or None if unknown.
    on_start (WB-20) is called per attempt with what is certain at that moment:
    {"parent": resumed session or None, "forked": bool, "mode": str}.
    Raises DispatchError on failure."""
    project = t.project or cfg.get("default_project", ".")
    if not Path(project).is_dir():
        raise DispatchError(f"Zielprojekt existiert nicht: {project}")
    claude_bin = cfg.get("claude_bin") or shutil.which("claude")
    if not claude_bin:
        raise DispatchError("Das Programm 'claude' wurde nicht gefunden.")
    timeout = cfg.get("agent_timeout_minutes", 30) * 60
    log = _log_path(t.id)
    state_path = cfg.get("state_path") or DEFAULT_STATE

    entry = load_last_entry(project, state_path)
    remembered = entry["id"] if entry else None
    force_fork = bool(entry and entry["interactive"])
    last_error = "unbekannter Fehler"
    for mode, resume_id in attempt_modes(remembered, project_has_history(project)):
        cmd = build_command(claude_bin, t, mode, cfg, resume_id=resume_id,
                            force_fork=force_fork)
        if on_start:
            forked = ("--fork-session" in cmd) if mode != "fresh" else False
            on_start({"parent": resume_id, "forked": forked, "mode": mode})
        progress = {"steps": 0, "last_tool": None, "tokens": 0,
                    "session": None, "error": None, "limit": None}
        result_text = session_id = agent_error = None
        killed = []
        proc = subprocess.Popen(cmd, cwd=project, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        watchdog = threading.Timer(timeout, lambda: (killed.append(True), proc.kill()))
        watchdog.daemon = True
        watchdog.start()
        try:
            with _open_log(log) as f:
                f.write(b"=== cmd: " + " ".join(cmd[:-1]).encode() + b" <prompt>\n")
                for raw in proc.stdout:          # live: one event per line
                    f.write(raw)
                    f.flush()                    # the log is the user's window in
                    try:
                        ev = json.loads(raw.decode("utf-8", "replace"))
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    _consume_event(ev, progress)
                    if ev.get("type") == "result" or ("result" in ev and "type" not in ev):
                        result_text = ev.get("result")
                        failed = bool(ev.get("is_error")) or (
                            ev.get("subtype") not in (None, "success"))
                        if failed:
                            agent_error = result_text or ev.get("subtype") or "unbekannt"
                            progress["error"] = agent_error
                    if on_event:
                        on_event(dict(progress))
                proc.wait()
                stderr = proc.stderr.read().decode(errors="replace")
                f.write(b"\n--- stderr ---\n" + stderr.encode() + b"\n")
        finally:
            watchdog.cancel()
            proc.stdout.close()
            proc.stderr.close()
        if killed:
            raise DispatchError(
                f"Zeitlimit ({cfg.get('agent_timeout_minutes', 30)} min) überschritten; Log: {log}")
        if agent_error:
            raise DispatchError(
                f"{classify_failure(agent_error) or agent_error} — Log: {log}")
        session_id = progress["session"]
        if proc.returncode == 0 and result_text is not None:
            if session_id:
                save_last_session(project, session_id, state_path)
            return (result_text or "(leere Antwort des Agenten)", session_id)
        raw_error = stderr.strip()[-500:] or f"Fehlercode {proc.returncode}"
        limit = classify_failure(raw_error)
        if limit:
            raise DispatchError(f"{limit} — Log: {log}")   # retrying won't help
        last_error = raw_error
    raise DispatchError(f"Agent-Lauf fehlgeschlagen: {last_error} — Log: {log}")


def _interactive_ids(state_path=None) -> set:
    """All session ids state.json marks as interactive (any project)."""
    try:
        data = json.loads(Path(state_path or DEFAULT_STATE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, dict):
        return set()
    return {v["id"] for v in data.values()
            if isinstance(v, dict) and v.get("interactive") and v.get("id")}


def sweep_orphaned(tickets_dir, state_path=None) -> list:
    """Called once at server startup, before any dispatch: a ticket still in
    in_arbeit cannot have a live finalizer (the queue starts empty), so its run
    was cut off — most likely a board restart or crash (WB-17). Surface it in
    fehlgeschlagen instead of letting it sit in in_arbeit forever."""
    swept = []
    for t in store.load_tickets(tickets_dir):
        if t.status != "in_arbeit":
            continue
        # WB-22: a pending handover is not an orphan (its fallback timer is
        # re-armed at startup), and neither is a ticket a LIVE chat session
        # has claimed (its session matches the interactive state entry).
        if t.handover:
            continue
        if t.session and t.session in _interactive_ids(state_path):
            continue
        store.set_result(tickets_dir, t.id,
                         "Fehlgeschlagen: Die Werkbank wurde neu gestartet, während dieses "
                         "Ticket in Arbeit war — der Abschluss des Laufs ist dabei verloren "
                         "gegangen. Ob die eigentliche Arbeit fertig wurde, zeigen git-Verlauf "
                         "und Journal des Zielprojekts. Bei Bedarf einfach erneut versuchen.")
        store.update_ticket(tickets_dir, t.id, {"status": "fehlgeschlagen"})
        swept.append(t.id)
    return swept


class Dispatcher:
    """FIFO, single-worker dispatcher. One claude run at a time, ever."""

    def __init__(self, tickets_dir, cfg=None, runner=None):
        self.tickets_dir = tickets_dir
        self.cfg = cfg or {}
        self.runner = runner or (lambda t, on_start=None, on_event=None:
                                 run_claude(t, self.cfg, on_start=on_start,
                                            on_event=on_event))
        self._queue = queue.Queue()
        self._pending = set()
        self._handover_failed = set()  # tickets whose chat handover expired (WB-22)
        self._runs = {}  # ticket id -> what is known about the ACTIVE run (WB-20)
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()

    def active_runs(self) -> dict:
        """Live picture per running ticket, incl. how long it has been silent
        (WB-37) so the board can show 'hängt vielleicht' instead of nothing."""
        now = time.time()
        with self._lock:
            out = {}
            for k, v in self._runs.items():
                info = dict(v)
                info["idle_seconds"] = int(now - info.get("last_ts", now))
                out[k] = info
            return out

    def dispatch(self, ticket_id: str) -> bool:
        with self._lock:
            if ticket_id in self._pending:
                return False
            self._pending.add(ticket_id)
        self._queue.put(ticket_id)
        return True

    def _work(self):
        while True:
            ticket_id = self._queue.get()
            try:
                self._run_one(ticket_id)
            except Exception as e:  # never let the worker die silently
                try:
                    store.set_result(self.tickets_dir, ticket_id,
                                     f"Fehlgeschlagen (interner Fehler der Werkbank): {e}")
                    store.update_ticket(self.tickets_dir, ticket_id,
                                        {"status": "fehlgeschlagen"})
                except Exception:
                    pass
            finally:
                with self._lock:
                    self._pending.discard(ticket_id)
                self._queue.task_done()
                self.pump_queue()  # WB-40: pull the next queued ticket

    def _run_one(self, ticket_id):
        all_tickets = store.load_tickets(self.tickets_dir)
        tickets = {t.id: t for t in all_tickets}
        t = tickets.get(ticket_id)
        if t is None or t.status != "in_arbeit":
            return  # moved away or deleted while queued — nothing to do
        # Order can still be violated by the time a queued ticket comes up
        # (e.g. its blocker bounced to review instead of erledigt). Exclusion
        # needs no recheck: runs are strictly serialized.
        reasons = store.blocking_reasons(all_tickets, t, include_exclusion=False)
        if reasons:
            store.set_result(self.tickets_dir, ticket_id,
                             "Nicht gestartet — " + "; ".join(reasons)
                             + ". Ticket zurück in Offen.")
            store.update_ticket(self.tickets_dir, ticket_id, {"status": "offen"})
            return
        # WB-22: an interactive lineage gets the ticket handed over to the live
        # chat session (visible work there) instead of a silent background run —
        # unless the user forced a background fork (checkbox) or a previous
        # handover for this ticket already expired unclaimed.
        project = t.project or self.cfg.get("default_project", "")
        entry = load_last_entry(project, self.cfg.get("state_path"))
        if (getattr(t, "fork", "nein") != "ja" and entry and entry.get("interactive")
                and ticket_id not in self._handover_failed):
            store.update_ticket(self.tickets_dir, ticket_id,
                                {"handover": entry["id"]})
            self.arm_handover_fallback(ticket_id)
            return
        self._handover_failed.discard(ticket_id)

        def on_start(info):
            with self._lock:
                self._runs[ticket_id] = {
                    **info, "started": datetime.now().strftime("%H:%M"),
                    "last": datetime.now().strftime("%H:%M:%S"),
                    "last_ts": time.time(),
                    "steps": 0, "last_tool": None, "tokens": 0, "error": None}

        def on_event(progress):
            with self._lock:
                info = self._runs.get(ticket_id)
                if info is not None:
                    info.update(progress)
                    info["last"] = datetime.now().strftime("%H:%M:%S")
                    info["last_ts"] = time.time()

        session = None
        try:
            # Pass only what this runner accepts (tests use simpler signatures).
            params = inspect.signature(self.runner).parameters
            kwargs = {}
            if "on_start" in params:
                kwargs["on_start"] = on_start
            if "on_event" in params:
                kwargs["on_event"] = on_event
            try:
                res = self.runner(t, **kwargs)
            except TypeError:
                res = self.runner(t)  # last resort for exotic runners
            result, session = res if isinstance(res, tuple) else (res, None)
            status = "review"
        except DispatchError as e:
            result = f"Fehlgeschlagen: {e}"
            status = "fehlgeschlagen"
        finally:
            with self._lock:
                self._runs.pop(ticket_id, None)
        store.set_result(self.tickets_dir, ticket_id, result)
        changes = {"status": status}
        if session:
            changes["session"] = session
        store.update_ticket(self.tickets_dir, ticket_id, changes)

    # --- queue column (WB-40) -------------------------------------------
    PRIORITY_ORDER = {"hoch": 0, "normal": 1, "niedrig": 2}

    def _project_of(self, t):
        """A ticket without its own project belongs to the default project —
        the same fallback run_claude uses."""
        return t.project or self.cfg.get("default_project", "")

    def _queue_blocked_reason(self, all_tickets, t):
        """Why a queued ticket may not start YET (German, for the board).
        Scoped to the ticket's own project — another project's work must not
        stall this queue; the global one-run-at-a-time rule is enforced by the
        dispatcher's pending set, not here."""
        project = self._project_of(t)
        if any(x.status == "in_arbeit" and self._project_of(x) == project
               for x in all_tickets):
            return "wartet, bis das laufende Ticket fertig ist"
        nonblocking = (self.cfg.get("nonblocking_review") or {}).get(project)
        if not nonblocking and any(x.status == "review" and self._project_of(x) == project
                                   for x in all_tickets):
            return "wartet auf deine Abnahme in Review"
        reasons = store.blocking_reasons(all_tickets, t)
        return "; ".join(reasons) if reasons else None

    def pump_queue(self):
        """Start the next queued ticket if nothing blocks it. Called after every
        finished run and after board status changes."""
        try:
            all_tickets = store.load_tickets(self.tickets_dir)
        except OSError:
            return
        queued = [t for t in all_tickets if t.status == "zu_bearbeiten"]
        queued.sort(key=lambda t: (self.PRIORITY_ORDER.get(t.priority, 1),
                                   store._ticket_number(t.id)))
        for t in queued:
            if self._queue_blocked_reason(all_tickets, t):
                continue
            with self._lock:
                if self._pending:
                    return  # a run is already queued/active — try again later
            store.update_ticket(self.tickets_dir, t.id, {"status": "in_arbeit"})
            self.dispatch(t.id)
            return  # strictly one at a time

    def arm_handover_fallback(self, ticket_id):
        """Deadline for the chat session to claim (clear) the handover marker;
        afterwards the ticket goes to a normal background run. Also called at
        server startup for handovers that survived a restart."""
        timeout = self.cfg.get("chat_handover_minutes", 5) * 60
        timer = threading.Timer(timeout, self._handover_fallback, args=(ticket_id,))
        timer.daemon = True
        timer.start()

    def _handover_fallback(self, ticket_id):
        try:
            tickets = {x.id: x for x in store.load_tickets(self.tickets_dir)}
            t = tickets.get(ticket_id)
            if t is None or t.status != "in_arbeit" or not t.handover:
                return  # claimed, finished, or gone — nothing to do
            store.update_ticket(self.tickets_dir, ticket_id, {"handover": ""})
            self._handover_failed.add(ticket_id)
            self.dispatch(ticket_id)
        except Exception:
            pass  # never let a timer thread die loudly; the sweep catches strays

    def join(self, timeout=None):
        """Wait until the queue is drained (tests only)."""
        end = threading.Event()
        threading.Thread(target=lambda: (self._queue.join(), end.set()), daemon=True).start()
        end.wait(timeout)
