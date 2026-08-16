"""Dispatching a ticket to a Claude Code run when it is dragged to 'In Arbeit'.

The worker resumes the remembered ticket session of the target project. Per
ticket the user chooses (fork field, default "nein") whether it grows in place
as one continuous conversation or is forked (`--fork-session`). Without a
remembered ticket session the fallback (--continue/fresh) ALWAYS forks so no
foreign conversation is mutated.

Runs are serialized PER ASSORTMENT, not globally (WB-92). An opencode ticket
never touches ~/.claude.json — it talks to the local model on another box, so
it runs in its own lane and never waits behind a Claude run. The Claude lane
stalls one at a time because concurrent `claude -p` processes are known to
corrupt ~/.claude/claude.json (github.com/anthropics/claude-code issues
#29051, #28813). The opencode lane also runs one at a time, because the local
model would otherwise compete with itself. The assortment is the ticket's
`assignee` (`opencode` or anything else).
"""

import inspect
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from werkbank import filelock, opencode, setup, store


TICKER_THREAD_NAME = "werkbank-queue-ticker"


class DispatchError(Exception):
    pass


class LimitError(DispatchError):
    """The account's usage limit stopped the run (WB-57). `resets_at` is the
    unix time the CLI reported for the reset, if it told us."""

    def __init__(self, message, resets_at=None):
        super().__init__(message)
        self.resets_at = resets_at


def assortment(assignee) -> str:
    """Which slot a ticket runs in (WB-92). `opencode` runs against the local
    model and never touches ~/.claude.json, so it gets its own lane; everything
    else is a Claude run sharing the single-`claude` rule."""
    return "opencode" if (assignee or "") == "opencode" else "claude"


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
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


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


# Full workflow lives in .claude/skills/werkbank-work-ticket in THIS repo
# (WB-70). Foreign projects have no such skill, so their prompt keeps the old
# inline instructions — we cannot rely on a skill that does not exist there.
BUG_DISCIPLINE = """
Dies ist ein Bug-Ticket — arbeite es mit Debugging-Disziplin ab:
1. Stelle den Fehler zuerst nach (Ursache belegen, nicht raten).
2. Behebe die Ursache, nicht nur das Symptom.
3. Schreibe einen Regressionstest, der ohne den Fix fehlschlägt und mit ihm besteht.
Der Nachweis (wie nachgestellt, welcher Test) gehört in deine Abschlussantwort.
"""

WERKBANK_ROOT = str(Path(__file__).resolve().parents[2])

SAFETY_NET = (
    "UNVERHANDELBAR (doppelter Boden, egal was der Skill sagt): "
    "Nie selbst auf `erledigt` setzen — das ist die Spalte des Nutzers. "
    "Die Ticket-Datei fasst du nicht an — das erledigt die Werkbank. "
    "Starte niemals das Werkbank-Board neu; mit ihm stirbt der Schritt, der "
    "dein Ergebnis ins Ticket schreibt — falls nötig, in die Abschlussantwort "
    "schreiben.\n"
    "Hinterlasse KEINE laufenden Hintergrund-Jobs — keine Warteschleife, kein "
    "Übergabe-Watcher, kein `sleep`-Loop. Du BIST der Hintergrundlauf; auf eine "
    "Übergabe zu warten ergibt hier keinen Sinn. Ein solcher Job hält die "
    "Ausgabe offen, die Werkbank hält dich dann für beschäftigt und die "
    "Warteschlange steht still (WB-77).\n"
    "Deine letzte Antwort wird WÖRTLICH als Ergebnis übernommen: kurze, "
    "ehrliche Zusammenfassung auf Deutsch — was getan, was geprüft, was "
    "fehlgeschlagen oder offen blieb."
)


def _is_werkbank_ticket(t) -> bool:
    """The ticket runs inside the Werkbank checkout — then the skill exists."""
    try:
        return Path(t.project or "").resolve() == Path(WERKBANK_ROOT).resolve()
    except OSError:
        return False


def build_prompt(t) -> str:
    kind = "das Werkbank-Bug-Ticket" if t.type == "bug" else "das Werkbank-Ticket"
    header = (f"Du setzt die letzte Session dieses Projekts fort und übernimmst "
              f"jetzt {kind} {t.id} (Titel: {t.title}).")
    if _is_werkbank_ticket(t):
        return (
            f"{header}\n\n{t.body}\n"
            "Arbeite dieses Ticket nach dem projekt-lokalen Skill "
            "`werkbank-work-ticket` (.claude/skills/werkbank-work-ticket) — "
            "das ist die einzige Quelle der Wahrheit für den Ablauf "
            "(Klarheits-Prüfung, Arbeit, Prüfen, Journal-/Doku-Pflicht, "
            "ehrliches Ergebnis, ggf. Bug-Disziplin). Findest du den Skill "
            "nicht: NICHT einfach loslegen — schreib das ins Ergebnis und "
            f"stopp.\n\n{SAFETY_NET}"
        )
    # Foreign project: no such skill exists there — keep the inline workflow.
    return (
        f"{header}\n\n{t.body}\n"
        f"{BUG_DISCIPLINE if t.type == 'bug' else ''}"
        "Arbeite dieses Ticket JETZT im aktuellen Projektverzeichnis ab. Die "
        "Regeln des Projekts gelten (CLAUDE.md, Commit-Disziplin). Ändere "
        "nichts außerhalb des Projektverzeichnisses; pushe nur, wenn die "
        f"Projektregeln das ausdrücklich vorsehen. {SAFETY_NET}"
    )


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
# The CLI's real wording is "You've hit your limit · resets 6:40am (…)" — that
# was missing and cost three tickets a wrong "fehlgeschlagen" (WB-76).
LIMIT_HINTS = ("usage limit", "rate limit", "session limit", "quota",
               "credit balance", "insufficient credit",
               "hit your limit", "your limit ", "limit reached",
               "limit · resets", "limit - resets")


def parse_reset_time(text: str):
    """Read the reset moment out of a limit message ('resets 6:40am').
    Returns a unix timestamp in the future, or None if the text has no time."""
    m = re.search(r"resets?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text or "",
                  re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1)) % 24
    minute = int(m.group(2) or 0)
    suffix = (m.group(3) or "").lower()
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    now = datetime.now()
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when <= now:                       # already past today → it means tomorrow
        when = when + timedelta(days=1)
    return when.timestamp()


def resolve_claude(cfg: dict, which=shutil.which, candidates=None):
    """Find the claude binary. A systemd service does not necessarily carry the
    user's PATH, so fall back to the usual install locations (WB-76)."""
    configured = (cfg or {}).get("claude_bin")
    if configured:
        return configured
    found = which("claude")
    if found:
        return found
    if candidates is None:
        candidates = [Path.home() / ".local" / "bin" / "claude",
                      Path("/usr/local/bin/claude"), Path("/usr/bin/claude")]
    for path in candidates:
        if Path(path).exists():
            return str(path)
    return None


def classify_failure(text: str):
    """Turn an agent failure into a plain-German cause, or None if unknown."""
    low = (text or "").lower()
    if any(h in low for h in LIMIT_HINTS):
        return ("Nutzungslimit erreicht — das Claude-Kontingent ist aufgebraucht. "
                "Das Ticket wartet und wird nach dem Reset automatisch "
                "fortgesetzt; du musst nichts tun.")
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
    if os.name == "nt":  # WB-43
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    p = Path(base) / "werkbank" / "logs"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def _log_path(ticket_id: str) -> Path:
    return log_dir() / f"werkbank-agent-{ticket_id}.log"


def _open_log(path: Path):
    """Append-only, no symlink following, owner-readable only."""
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | filelock.NOFOLLOW, 0o600)
    return os.fdopen(fd, "ab")


def _own_process_group() -> dict:
    """Popen kwargs that put the run in a process group of its own, so it can
    be ended TOGETHER WITH everything it started (WB-77)."""
    if os.name == "posix":
        return {"start_new_session": True}
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": flags} if flags else {}


def _group_of(proc):
    """The run's process group, captured while the process is certainly alive.
    Looking it up later would fail once the process is reaped."""
    if os.name != "posix":
        return None
    try:
        return os.getpgid(proc.pid)
    except OSError:
        return None


def _signal_group(group, proc, sig) -> None:
    """Signal the whole run. Errors are normal and ignored: an empty group
    (nothing left to kill) raises ESRCH, which is the outcome we wanted."""
    if group:
        try:
            os.killpg(group, sig)
            return
        except OSError:
            pass
    try:
        proc.kill() if sig == getattr(signal, "SIGKILL", None) else proc.terminate()
    except OSError:
        pass


def _end_run(proc, group, grace: float) -> bool:
    """Close a run down after its result arrived. Returns True if the run had
    to be forced — which is not an error: its work was already delivered.

    WB-77: a background job the agent left behind keeps the stdout pipe open,
    so waiting for EOF waits forever. We wait a short grace period for a clean
    exit, then end the group. Either way nothing of the run may outlive it —
    a stray job would hold the pipe, burn quota and stall the next ticket."""
    forced = False
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        forced = True
        _signal_group(group, proc, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _signal_group(group, proc, signal.SIGKILL)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    _signal_group(group, proc, signal.SIGTERM)   # sweep up leftover children
    return forced


def _drain_pipe(pipe, sink: list) -> None:
    """Read stderr while stdout is being read. A blocking read at the END of
    the run would hang on exactly the stray-child case this guards (WB-77)."""
    try:
        for chunk in iter(lambda: pipe.read(4096), b""):
            sink.append(chunk)
    except (ValueError, OSError):
        pass        # closed under us when the run ended — nothing left to read


def run_claude(t, cfg: dict, on_start=None, on_event=None, on_pid=None):
    """Run a claude process for the ticket; returns (result_text, session_id).
    session_id is the run's own id from the JSON output, or None if unknown.
    on_start (WB-20) is called per attempt with what is certain at that moment:
    {"parent": resumed session or None, "forked": bool, "mode": str}.
    on_pid (WB-75) is called with the OS pid right after Popen, so the caller
    can persist it — a board restart mid-run must be able to find and kill the
    orphaned process, not just mark the ticket failed.
    Raises DispatchError on failure."""
    project = t.project or cfg.get("default_project", ".")
    if not Path(project).is_dir():
        raise DispatchError(f"Zielprojekt existiert nicht: {project}")
    claude_bin = resolve_claude(cfg)
    if not claude_bin:
        raise DispatchError(
            "Das Programm 'claude' wurde nicht gefunden — weder im Suchpfad des "
            "Boards noch an den üblichen Orten. Trage den vollen Pfad als "
            "'claude_bin' in config.json ein.")
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
        forced = saw_result = False
        proc = subprocess.Popen(cmd, cwd=project, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, **_own_process_group())
        group = _group_of(proc)
        if on_pid:
            try:
                on_pid(proc.pid)
            except Exception:
                pass                            # persistence is best-effort
        stderr_parts = []
        drain = threading.Thread(target=_drain_pipe, args=(proc.stderr, stderr_parts))
        drain.daemon = True
        drain.start()
        watchdog = threading.Timer(
            timeout, lambda: (killed.append(True), _signal_group(group, proc, signal.SIGKILL)))
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
                        saw_result = True
                        failed = bool(ev.get("is_error")) or (
                            ev.get("subtype") not in (None, "success"))
                        if failed:
                            agent_error = result_text or ev.get("subtype") or "unbekannt"
                            progress["error"] = agent_error
                    if on_event:
                        on_event(dict(progress))
                    if saw_result:
                        break       # WB-77: the answer is in; do not wait for EOF
                forced = _end_run(proc, group, float(cfg.get("exit_grace_seconds", 10)))
                drain.join(2)
                stderr = b"".join(stderr_parts).decode(errors="replace")
                f.write(b"\n--- stderr ---\n" + stderr.encode() + b"\n")
                if forced:
                    f.write("--- Der Lauf war nach seinem Ergebnis noch am Leben "
                            "(Hintergrund-Job des Agenten) und wurde beendet.\n"
                            .encode())
        finally:
            watchdog.cancel()
            proc.stdout.close()
            proc.stderr.close()
        if killed:
            raise DispatchError(
                f"Zeitlimit ({cfg.get('agent_timeout_minutes', 30)} min) überschritten; Log: {log}")
        if agent_error:
            cause = classify_failure(agent_error)
            if cause and "Nutzungslimit" in cause:
                raise LimitError(f"{cause} — Log: {log}",
                                 resets_at=(progress.get("limit") or {}).get("resets_at")
                                 or parse_reset_time(agent_error))
            raise DispatchError(f"{cause or agent_error} — Log: {log}")
        session_id = progress["session"]
        # WB-77: `forced` means we ended a run that had already answered — its
        # exit code says how it died, not whether the work succeeded.
        if result_text is not None and (proc.returncode == 0 or forced):
            if session_id:
                save_last_session(project, session_id, state_path)
            return (result_text or "(leere Antwort des Agenten)", session_id)
        raw_error = stderr.strip()[-500:] or f"Fehlercode {proc.returncode}"
        cause = classify_failure(raw_error)
        if cause and "Nutzungslimit" in cause:
            # WB-57: not a failure to shrug at — wait for the reset and resume.
            raise LimitError(f"{cause} — Log: {log}",
                             resets_at=(progress.get("limit") or {}).get("resets_at")
                             or parse_reset_time(raw_error))
        if cause:
            raise DispatchError(f"{cause} — Log: {log}")   # retrying won't help
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


def rearm_limit_resumes(tickets_dir, dispatcher) -> None:
    """WB-69: after a restart, re-arm the resume for every ticket whose
    limit_until is still in the future. Reset already passed → dispatch now."""
    now = int(time.time())
    for t in store.load_tickets(tickets_dir):
        if not t.limit_until:
            continue
        try:
            until = int(t.limit_until)
        except ValueError:
            continue
        if until <= now:
            store.update_ticket(tickets_dir, t.id, {"limit_until": ""})
            dispatcher.dispatch(t.id)
            continue
        dispatcher.pause_until = max(dispatcher.pause_until, float(until))
        dispatcher._arm_resume(t.id)


def _read_cmdline(pid: int):
    """Return the process's full command line as a string, or None. Reads
    /proc on Linux, falls back to `ps` on other POSIX systems. Windows is
    unsupported — no reliable stdlib way, so no verification, hence no kill."""
    try:
        proc_file = Path(f"/proc/{int(pid)}/cmdline")
        if proc_file.exists():
            data = proc_file.read_bytes()
            return data.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    except (OSError, ValueError):
        return None
    if os.name == "nt":
        return None
    try:
        r = subprocess.run(["ps", "-p", str(int(pid)), "-o", "args="],
                           capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _process_matches_ticket(pid: int, ticket_id: str) -> bool:
    """True only when PID's command line names BOTH the ticket and 'claude' —
    guards against PID reuse and against killing something innocent that happens
    to share the id. If we cannot read the cmdline (unsupported OS), refuse."""
    cmdline = _read_cmdline(pid)
    if not cmdline:
        return False
    return ticket_id in cmdline and "claude" in cmdline.lower()


def _is_running(pid: int) -> bool:
    """True while the process is still executing. A zombie counts as dead —
    otherwise os.kill(pid,0) keeps saying 'alive' until someone reaps, and in
    tests nobody does. In production the ex-board's child is reparented to
    init, which reaps immediately, so this is only visible in tests."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("State:"):
                    return not any(mark in line for mark in ("Z", "X"))
        return True
    except OSError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate_process(pid: int, grace: float = 1.5) -> bool:
    """SIGTERM, wait up to `grace` seconds, then SIGKILL. Returns True if the
    process is gone afterwards. Silently False if we could not signal at all."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    end = time.time() + grace
    while time.time() < end:
        if not _is_running(pid):
            return True
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    end = time.time() + 0.5
    while time.time() < end:
        if not _is_running(pid):
            return True
        time.sleep(0.05)
    return False


def sweep_orphaned(tickets_dir, state_path=None) -> list:
    """Called once at server startup, before any dispatch: a ticket still in
    in_arbeit cannot have a live finalizer (the queue starts empty), so its run
    was cut off — most likely a board restart or crash (WB-17). Surface it in
    fehlgeschlagen instead of letting it sit in in_arbeit forever.

    WB-75: if the run's OS process is still alive (recorded pid, cmdline
    matches ticket AND 'claude'), kill it — otherwise it keeps burning quota
    with no dispatcher to receive its output."""
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
        if t.limit_until:                     # WB-69: paused on quota, not stranded
            continue
        killed_pid = None
        if t.pid:
            try:
                pid = int(t.pid)
            except ValueError:
                pid = 0
            if pid > 0 and _process_matches_ticket(pid, t.id):
                if _terminate_process(pid):
                    killed_pid = pid
        if killed_pid:
            store.set_result(tickets_dir, t.id,
                             f"Fehlgeschlagen: Die Werkbank wurde neu gestartet, während dieses "
                             f"Ticket in Arbeit war. Der weiterlaufende Agenten-Prozess (PID "
                             f"{killed_pid}) wurde beim Aufräumen beendet — sein Ergebnis konnte "
                             f"nirgends mehr ankommen. Ob die Arbeit fertig wurde, zeigen "
                             f"git-Verlauf und Journal des Zielprojekts. Bei Bedarf einfach "
                             f"erneut versuchen.")
        else:
            store.set_result(tickets_dir, t.id,
                             "Fehlgeschlagen: Die Werkbank wurde neu gestartet, während dieses "
                             "Ticket in Arbeit war — der Abschluss des Laufs ist dabei verloren "
                             "gegangen. Ob die eigentliche Arbeit fertig wurde, zeigen git-Verlauf "
                             "und Journal des Zielprojekts. Bei Bedarf einfach erneut versuchen.")
        store.update_ticket(tickets_dir, t.id,
                            {"status": "fehlgeschlagen", "pid": ""})
        swept.append(t.id)
    return swept


class Dispatcher:
    """FIFO dispatcher with one run per assortment, never more (WB-92). The
    Claude lane is single because of ~/.claude.json; the opencode lane is
    single because the local model would otherwise compete with itself."""

    def __init__(self, tickets_dir, cfg=None, runner=None):
        self.tickets_dir = tickets_dir
        self.cfg = cfg or {}
        self.runner = runner or (lambda t, on_start=None, on_event=None:
                                 run_claude(t, self.cfg, on_start=on_start,
                                            on_event=on_event))
        # One queue AND one worker per assortment (WB-92 round 2): the per-lane
        # slot bookkeeping alone did not help while a single worker thread
        # executed all runs in series — an opencode ticket went in_arbeit and
        # then still waited behind the active Claude run inside the worker.
        self._queues = {"claude": queue.Queue(), "opencode": queue.Queue()}
        self._slot = {}  # ticket id -> its assortment (WB-92)
        # WB-92: one slot per assortment instead of one global slot, so an
        # opencode run never waits behind a Claude run (see `assortment`).
        self._pending = {"claude": set(), "opencode": set()}
        self._handover_failed = set()  # tickets whose chat handover expired (WB-22)
        self.pause_until = 0           # quota reset time; queue rests until then (WB-57)
        self._runs = {}  # ticket id -> what is known about the ACTIVE run (WB-20)
        self._lock = threading.Lock()
        self._threads = [threading.Thread(target=self._work, args=(lane,), daemon=True)
                         for lane in self._queues]
        for th in self._threads:
            th.start()
        # WB-59: the queue must not depend on someone poking the API. A chat
        # session finalizing a ticket writes through the store, so no request
        # ever reaches the server — without this ticker the queue stood still
        # with a free agent and an unblocked ticket.
        self._stopped = threading.Event()
        # Named so a leaked ticker is findable — tests assert none outlive them
        # (WB-93: 15 unstopped dispatchers turned the suite into its own load
        # generator and made timing-sensitive queue tests fail under pressure).
        self._ticker = threading.Thread(target=self._tick, daemon=True,
                                        name=TICKER_THREAD_NAME)
        self._ticker.start()

    def stop(self):
        """Stop the background ticker (tests, shutdown). Idempotent."""
        self._stopped.set()

    def _tick(self):
        interval = float(self.cfg.get("queue_poll_seconds", 15))
        while not self._stopped.wait(interval):
            try:
                self.sweep_handovers()
                self.adopt_orphans()
                self.pump_queue()
            except Exception:
                pass   # a stalled tick must never kill the ticker

    def sweep_handovers(self):
        """WB-66: hand a ticket to a background run when the chat session did
        not claim it in time. The deadline comes from the ticket file, so board
        restarts cannot keep granting fresh windows (the old in-memory timer
        did exactly that — 'der nächste startet nicht')."""
        limit = float(self.cfg.get("chat_handover_minutes", 5)) * 60
        now = time.time()
        for t in store.load_tickets(self.tickets_dir):
            if t.status != "in_arbeit" or not t.handover:
                continue
            try:
                set_at = int(t.handover_at or 0)
            except ValueError:
                set_at = 0
            if set_at and now - set_at < limit:
                continue                      # still the chat session's turn
            store.update_ticket(self.tickets_dir, t.id,
                                {"handover": "", "handover_at": "",
                                 "handover_expired": "ja"})
            self._handover_failed.add(t.id)
            self.dispatch(t.id)

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

    def _ticket_by_id(self, ticket_id):
        """Ticket object or None (WB-92). Reads the board files; used only to
        learn a ticket's assortment, so a missing file sorts into the Claude
        lane — the original behaviour."""
        try:
            for x in store.load_tickets(self.tickets_dir):
                if x.id == ticket_id:
                    return x
        except OSError:
            pass
        return None

    def dispatch(self, ticket_id: str) -> bool:
        t = self._ticket_by_id(ticket_id)
        slot = assortment(getattr(t, "assignee", None))
        with self._lock:
            if ticket_id in self._slot:
                return False
            self._slot[ticket_id] = slot
            self._pending[slot].add(ticket_id)
        self._queues[slot].put(ticket_id)
        return True

    def _work(self, lane):
        while True:
            ticket_id = self._queues[lane].get()
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
                    slot = self._slot.pop(ticket_id, None)
                    if slot is not None:
                        self._pending[slot].discard(ticket_id)
                self._queues[lane].task_done()
                self.pump_queue()  # WB-40: pull the next queued ticket

    def _run_one(self, ticket_id):
        all_tickets = store.load_tickets(self.tickets_dir)
        tickets = {t.id: t for t in all_tickets}
        t = tickets.get(ticket_id)
        if t is None or t.status != "in_arbeit":
            return  # moved away or deleted while queued — nothing to do
        # Order can still be violated by the time a queued ticket comes up
        # (e.g. its blocker bounced to review instead of erledigt). Exclusion
        # needs no recheck: only runs of one project's own assortment can be
        # active at once, and those are handled by the project-scoped reason.
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
        # WB-68: "already tried" must survive restarts, so it lives in the
        # ticket — otherwise a fresh dispatcher hands the same ticket to the
        # same silent session again, forever.
        if (getattr(t, "fork", "nein") != "ja" and entry and entry.get("interactive")
                and getattr(t, "handover_expired", "") != "ja"
                and ticket_id not in self._handover_failed):
            store.update_ticket(self.tickets_dir, ticket_id,
                                {"handover": entry["id"],
                                 "handover_at": str(int(time.time()))})
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

        def on_pid(pid):
            # WB-75: persist so a restart can find and kill the orphan process.
            try:
                store.update_ticket(self.tickets_dir, ticket_id,
                                    {"pid": str(pid)})
            except Exception:
                pass

        # WB-48: an unconfigured board must not aim an agent at itself.
        refusal = setup.dispatch_refusal(self.cfg, self._project_of(t))
        if refusal:
            with self._lock:
                self._runs.pop(ticket_id, None)
            store.set_result(self.tickets_dir, ticket_id, refusal)
            store.update_ticket(self.tickets_dir, ticket_id,
                                {"status": "fehlgeschlagen"})
            return

        # An opencode ticket never reaches the Claude runner: the local model
        # implements it and its GATE decides whether it worked. No gate -> refused.
        if (getattr(t, "assignee", "") or "") == "opencode":
            # WB-76: keep the card alive. A local run can take many minutes and
            # an in_arbeit card showing nothing is the "I cannot see what is
            # happening" complaint this tool already has.
            on_start({"model": "opencode", "ticket": ticket_id})

            def on_progress(step):
                with self._lock:
                    info = self._runs.get(ticket_id)
                    if info is not None:
                        info["last_tool"] = step
                        info["last"] = datetime.now().strftime("%H:%M:%S")
                        info["last_ts"] = time.time()

            try:
                outcome = opencode.work_ticket(t, self.cfg, on_progress=on_progress)
            finally:
                with self._lock:
                    self._runs.pop(ticket_id, None)
            store.set_result(self.tickets_dir, ticket_id, outcome.result)
            changes = {"status": outcome.status, "limit_until": "", "pid": ""}
            changes.update(outcome.changes)
            store.update_ticket(self.tickets_dir, ticket_id, changes)
            return

        session = None
        try:
            # Pass only what this runner accepts (tests use simpler signatures).
            params = inspect.signature(self.runner).parameters
            kwargs = {}
            if "on_start" in params:
                kwargs["on_start"] = on_start
            if "on_event" in params:
                kwargs["on_event"] = on_event
            if "on_pid" in params:
                kwargs["on_pid"] = on_pid
            try:
                res = self.runner(t, **kwargs)
            except TypeError:
                res = self.runner(t)  # last resort for exotic runners
            result, session = res if isinstance(res, tuple) else (res, None)
            status = "review"
        except LimitError as e:
            # WB-57: the account ran out, not the work. Park the ticket back in
            # the queue; the reset restarts it — no "continue" needed.
            self.pause_until = e.resets_at or (time.time() + 3600)
            when = datetime.fromtimestamp(self.pause_until).strftime("%H:%M")
            with self._lock:
                self._runs.pop(ticket_id, None)
            store.set_result(self.tickets_dir, ticket_id,
                             f"Pausiert — Claude-Kontingent aufgebraucht: {e} "
                             f"Das Ticket bleibt in Arbeit (rot markiert) und wird ab "
                             f"{when} Uhr automatisch fortgesetzt. Du musst nichts tun.")
            store.update_ticket(self.tickets_dir, ticket_id,
                                {"limit_until": str(int(self.pause_until)),
                                 "pid": ""})
            # WB-69: stays in in_arbeit (red on the board) — the resume timer
            # picks THIS ticket up again once the pause is over.
            self._arm_resume(ticket_id)
            return
        except DispatchError as e:
            result = f"Fehlgeschlagen: {e}"
            status = "fehlgeschlagen"
        finally:
            with self._lock:
                self._runs.pop(ticket_id, None)
        store.set_result(self.tickets_dir, ticket_id, result)
        # WB-69: pause cleared. WB-75: process is dead, pid must not linger
        # or the next sweep would try to kill a random reused PID.
        changes = {"status": status, "limit_until": "", "pid": ""}
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
        stall this queue. The per-assortment one-run rule (a run of the SAME
        kind already active) is enforced by `_slot_taken`, not here — keep this
        list to the project-scoped reasons so the board's `queueWaitReason`
        stays a truthful mirror of it (WB-92)."""
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

    def adopt_orphans(self):
        """WB-68b: a ticket in in_arbeit with no handover marker and no run is
        stuck where nobody looks. Pick it up — unless a chat session is
        actively working it (its own session id is the interactive one), or the
        account is out of quota (WB-69: then it is waiting on purpose)."""
        if self.pause_reason():
            return
        interactive = _interactive_ids(self.cfg.get("state_path"))
        with self._lock:
            pending = set().union(*self._pending.values()) | set(self._runs)
        for t in store.load_tickets(self.tickets_dir):
            if t.status != "in_arbeit" or t.handover or t.id in pending:
                continue
            if t.session and t.session in interactive:
                continue          # someone is working it in a chat right now
            if t.limit_until and int(t.limit_until or 0) > int(time.time()):
                continue          # WB-69: paused on quota, resume is armed
            self.dispatch(t.id)

    def pause_reason(self):
        """German reason why the whole queue is resting, or None (WB-57)."""
        if time.time() >= self.pause_until:
            return None
        when = datetime.fromtimestamp(self.pause_until).strftime("%H:%M")
        return ("Claude-Kontingent aufgebraucht — die Warteschlange macht um "
                f"{when} Uhr von selbst weiter.")

    def pump_queue(self):
        """Start the next queued ticket if nothing blocks it. Called after every
        finished run and after board status changes."""
        if self.pause_reason():
            return
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
            if self._slot_taken(t):
                continue  # this assortment's lane is busy — try the next one
            store.update_ticket(self.tickets_dir, t.id, {"status": "in_arbeit"})
            self.dispatch(t.id)
            return  # only one of THIS assortment at a time (WB-92)

    def _slot_taken(self, t) -> bool:
        """True while this ticket's assortment already has a queued/active run
        (WB-92). A Claude run never blocks an opencode ticket, and vice versa —
        each lane is one at a time, so the other lane can start beside it."""
        slot = assortment(getattr(t, "assignee", None))
        with self._lock:
            return bool(self._pending[slot])

    def _arm_resume(self, ticket_id=None):
        """Wake the queue when the quota reset time arrives (WB-57/69). If a
        ticket_id is given, pick THAT ticket up (it stayed in in_arbeit)."""
        delay = max(5, self.pause_until - time.time() + 5)
        def wake():
            if ticket_id:
                try:
                    store.update_ticket(self.tickets_dir, ticket_id,
                                        {"limit_until": ""})
                    self.dispatch(ticket_id)
                except Exception:
                    pass
            self.pump_queue()
        timer = threading.Timer(delay, wake)
        timer.daemon = True
        timer.start()

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
            store.update_ticket(self.tickets_dir, ticket_id,
                                {"handover": "", "handover_at": "",
                                 "handover_expired": "ja"})
            self._handover_failed.add(ticket_id)
            self.dispatch(ticket_id)
        except Exception:
            pass  # never let a timer thread die loudly; the sweep catches strays

    def join(self, timeout=None):
        """Wait until the queue is drained (tests only)."""
        end = threading.Event()
        threading.Thread(target=lambda: ([q.join() for q in self._queues.values()],
                                         end.set()), daemon=True).start()
        end.wait(timeout)
