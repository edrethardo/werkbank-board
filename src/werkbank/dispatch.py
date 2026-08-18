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


# Windows has no SIGKILL. Referencing it raises AttributeError where it is
# evaluated — inside a timer thread that then delivers nothing at all, so the
# run watchdog silently stopped working there (found by an adversarial
# cross-platform review). SIGTERM is what Windows has; taking it is better than
# taking nothing.
SIGKILL_OR_TERM = getattr(signal, "SIGKILL", signal.SIGTERM)

TICKER_THREAD_NAME = "werkbank-queue-ticker"


class DispatchError(Exception):
    pass


class LimitError(DispatchError):
    """The account's usage limit stopped the run (WB-57). `resets_at` is the
    unix time the CLI reported for the reset, if it told us."""

    def __init__(self, message, resets_at=None):
        super().__init__(message)
        self.resets_at = resets_at


def known_assignee(assignee) -> bool:
    """Only these assignees may start AUTOMATICALLY (WB-105). Anything else
    (a human's name, a typo) must never spawn a Bash-enabled agent on the
    ticket body — the drag path already refuses this; queue and orphan
    adoption go through this same gate."""
    return (assignee or "claude") in ("claude",) + LOCAL_ASSIGNEES


def claude_config_root(cfg=None) -> Path:
    """WB-146: root directory that holds one Claude config subdir per project.
    Owner-only (0700) — every subdir holds a symlink to the user's real
    `.credentials.json`, which is 0600 upstream and stays that way."""
    if cfg and cfg.get("claude_config_root"):
        p = Path(cfg["claude_config_root"]).expanduser()
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        p = Path(base) / "werkbank" / "claude-configs"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def claude_config_dir_for(project: str, cfg=None) -> Path:
    """One config dir per PROJECT (WB-146). Per-project keeps the session
    history for --resume/--fork-session on the very same project — a
    per-run dir would lose that lineage. Different projects therefore stop
    sharing ~/.claude.json (issues #29051/#28813 no longer apply between
    them). The credentials file is symlinked in — never copied — so a
    rotated login on the source updates every project's dir at once."""
    d = claude_config_root(cfg) / project_slug(project or "default")
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    link = d / ".credentials.json"
    src = Path.home() / ".claude" / ".credentials.json"
    if src.exists() and not link.exists():
        try:
            os.symlink(src, link)
        except OSError:
            # Windows needs Developer Mode or admin rights for symlinks, and a
            # normal user has neither. Swallowing that used to leave the run
            # pointed at a config dir with NO credentials — every dispatch then
            # failed with "bitte neu anmelden", on a path that is on by default
            # (found by an adversarial cross-platform review). A shared config
            # dir is worse for parallelism and better than not working at all.
            return None
    return d


# WB-219: the assignees that run against the LOCAL model. They share one slot
# on purpose — opencode and dsh talk to the same vLLM box on the same GPU, so
# giving dsh its own lane would let two local runs fight over one card and make
# both slower than running them in turn. The slot keeps its historical name
# because it is a dict key in the queues, the pending sets and every test.
LOCAL_ASSIGNEES = ("opencode", "dsh")
LOCAL_SLOT = "opencode"


def is_local(assignee) -> bool:
    """True for a ticket that runs on the local model (WB-219)."""
    return (assignee or "").strip().lower() in LOCAL_ASSIGNEES


def assortment(assignee) -> str:
    """Which slot a ticket runs in (WB-92). Local runs never touch
    ~/.claude.json and share one lane of their own; everything else is a
    Claude run sharing the single-`claude` rule."""
    return LOCAL_SLOT if is_local(assignee) else "claude"


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
    """Remember the last session that worked THIS project. Two concerns share
    the same file: the id feeds --resume for the next dispatch (`id`), and a
    separate `interactive_ids` list holds every session a chat has claimed
    (WB-144). A background run may update `id` freely but MUST NOT drop the
    interactive claim — the claim guards sweep_orphaned and adopt_orphans
    against stealing a ticket the chat still holds. Non-interactive updates
    without any prior chat claim keep the legacy plain-string shape."""
    path = Path(state_path or DEFAULT_STATE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    key = str(project)
    if interactive:
        # A chat session claiming this project: it is the ONE currently
        # interactive id (any earlier one has handed off), and it is also
        # the id to resume from.
        data[key] = {"id": session_id, "interactive": True,
                     "interactive_ids": [session_id]}
        path.write_text(json.dumps(data, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
        return
    # Non-interactive write: preserve any interactive_ids the chat claimed.
    prior = data.get(key)
    keep = []
    if isinstance(prior, dict):
        keep = list(prior.get("interactive_ids") or [])
        # Legacy dict without the list but marked interactive: its id counts.
        if not keep and prior.get("interactive") and prior.get("id"):
            keep = [prior["id"]]
    if keep:
        data[key] = {"id": session_id, "interactive": False,
                     "interactive_ids": keep}
    else:
        # No chat claim to protect — keep the historical compact form.
        data[key] = session_id
    path.write_text(json.dumps(data, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def register_ticket_session(project: str, session_id, state_path=None):
    """Called by INTERACTIVE sessions (work-tickets / pull flows) after working
    a ticket. The id must come from $CLAUDE_CODE_SESSION_ID — never guessed;
    an empty value is rejected so callers skip registration instead."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id fehlt — Registrierung übersprungen (nie raten)")
    save_last_session(project, session_id.strip(), state_path, interactive=True)


# The CLI's wording when the id we remembered cannot be resumed — the session
# was deleted, is held open elsewhere, or belongs to another project.
STALE_SESSION_HINTS = ("no conversation found with session id",
                       "no conversation found")


def is_stale_session(text) -> bool:
    low = (text or "").lower()
    return any(hint in low for hint in STALE_SESSION_HINTS)


def forget_session(project: str, state_path=None) -> None:
    """Drop a remembered ticket session that cannot be resumed, so the next
    dispatch does not walk into the same wall."""
    path = Path(state_path or DEFAULT_STATE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict) or str(project) not in data:
        return
    data.pop(str(project), None)
    try:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8",
                        newline="\n")
    except OSError:
        pass


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

# WB-161: an epic is a PLANNING ticket — its "work" is planning with the user
# and writing the child tickets, not implementing anything itself.
EPIC_PLANNING = """
Dies ist ein Epic — plane das Paket mit dem Nutzer und schreibe die
Kind-Tickets, statt jetzt zu implementieren:
1. Kläre mit dem Nutzer den Umfang: was gehört rein, was bleibt draussen.
2. Zerlege in konkrete, einzeln arbeitbare Kind-Tickets — jedes eines fuer sich
   „fertig" machbar, mit Zweck und Akzeptanzkriterium in der Beschreibung.
3. Lege jedes Kind ueber die Werkbank an und trage es mit dem Frontmatter-Feld
   `epic: <id-dieses-Epics>` als Kind aus. Beispiel: `epic: WB-42`.
4. Fasse in deinem Ergebnis zusammen: welche Kinder mit welchem Zweck, plus
   ggf. Reihenfolge/Abhaengigkeiten. Danach geht das Epic in „Review".
Das Epic selbst implementiert nichts; die Kinder werden anschliessend einzeln
gezogen und normal abgearbeitet.
"""

WERKBANK_ROOT = str(Path(__file__).resolve().parents[2])

RUECKFRAGE_MARKER = "RÜCKFRAGE AN DEN NUTZER:"

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
    f"BRAUCHST DU EINE ENTSCHEIDUNG DES NUTZERS: NICHT raten und NICHT "
    f"scheitern lassen. Beende deinen Lauf sauber, und lass deine "
    f"Abschlussantwort mit einer Zeile beginnen, die genau so lautet: "
    f"‚{RUECKFRAGE_MARKER}‘. Darunter die konkrete(n) Frage(n) und die "
    f"Möglichkeiten in einem Satz. Die Werkbank stellt das Ticket dann in "
    f"die Spalte Rückfrage; die Antwort des Nutzers setzt DEINE Session "
    f"fort — kein Kontext geht verloren. Kein Marker, keine Rückfrage: der "
    f"Rest deiner Antwort wird als Ergebnis übernommen (WB-123).\n"
    "Deine letzte Antwort wird WÖRTLICH als Ergebnis übernommen: kurze, "
    "ehrliche Zusammenfassung auf Deutsch — was getan, was geprüft, was "
    "fehlgeschlagen oder offen blieb."
)


def is_query_result(result_text) -> bool:
    """The agent's final message begins with the RÜCKFRAGE marker (WB-123).
    Leading whitespace, a code fence, or a bold marker are tolerated; a
    marker embedded ten paragraphs down is NOT counted — the marker must be
    the FIRST non-whitespace content, so a random mention in prose does not
    accidentally pause the ticket."""
    if not result_text:
        return False
    s = str(result_text).lstrip().lstrip("*").lstrip("_").lstrip("`").lstrip()
    return s.startswith(RUECKFRAGE_MARKER)


def _is_werkbank_ticket(t) -> bool:
    """The ticket runs inside the Werkbank checkout — then the skill exists."""
    try:
        return Path(t.project or "").resolve() == Path(WERKBANK_ROOT).resolve()
    except OSError:
        return False


def build_prompt(t) -> str:
    kind = ("das Werkbank-Epic" if t.type == "epic"
            else "das Werkbank-Bug-Ticket" if t.type == "bug"
            else "das Werkbank-Ticket")
    header = (f"Du setzt die letzte Session dieses Projekts fort und übernimmst "
              f"jetzt {kind} {t.id} (Titel: {t.title}).")
    epic_block = EPIC_PLANNING if t.type == "epic" else ""
    if _is_werkbank_ticket(t):
        return (
            f"{header}\n\n{t.body}\n{epic_block}"
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
        f"{epic_block}"
        "Arbeite dieses Ticket JETZT im aktuellen Projektverzeichnis ab. Die "
        "Regeln des Projekts gelten (CLAUDE.md, Commit-Disziplin). Ändere "
        "nichts außerhalb des Projektverzeichnisses; pushe nur, wenn die "
        f"Projektregeln das ausdrücklich vorsehen. {SAFETY_NET}"
    )


def build_command(claude_bin: str, t, mode: str, cfg: dict, resume_id=None,
                  force_fork: bool = False, prompt: str = None) -> list:
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
    elif mode == "answer":
        # WB-123: user replied to a rueckfrage. Feed the answer back into the
        # SAME session — NEVER fork here, or the answer branches off before
        # the question and is functionally lost. The `prompt` is the answer
        # text, not the ticket body.
        cmd += ["--resume", resume_id]
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
        prompt if prompt is not None else build_prompt(t),
    ]
    return cmd


def answer_ticket(tickets_dir, ticket_id: str, body: dict, dispatcher):
    """WB-123: apply the user's answer to a rueckfrage ticket. Returns
    (http_code, json_payload). Kept out of server.py so the transitions
    (empty answer, wrong status, stale version) are unit-testable without
    a live HTTP server."""
    answer = (body.get("answer") or "").strip()
    if not answer:
        return 400, {"error": "Antwort darf nicht leer sein"}
    # Frontmatter format is one line per field (WB-35 F4) — a stray newline
    # in the answer would silently smuggle a whole extra field. Fold rather
    # than refuse, so a user pasting a two-line note is not turned away.
    answer = re.sub(r"[\r\n]+", " ", answer)
    tickets = {x.id: x for x in store.load_tickets(tickets_dir)}
    current = tickets.get(ticket_id)
    if not current:
        return 404, {"error": "Ticket nicht gefunden"}
    if current.status != "rueckfrage":
        return 409, {"error":
            "Dieses Ticket wartet gerade nicht auf eine Antwort."}
    try:
        t = store.update_ticket(tickets_dir, ticket_id, {
            "answer": answer, "status": "in_arbeit",
            "version": body.get("version")})
    except store.ConflictError as e:
        return 409, {"error": str(e)}
    dispatcher.dispatch(t.id)
    return 200, t.to_dict()


def build_answer_prompt(t) -> str:
    """WB-123: the resume prompt is JUST the user's answer, prefixed with a
    short frame so the agent knows what it is picking up. The frame is one
    line; the answer body follows verbatim so nothing gets paraphrased."""
    answer = (getattr(t, "answer", "") or "").strip() or "(leere Antwort)"
    return (
        f"ANTWORT AUF DEINE RÜCKFRAGE zu {t.id}:\n{answer}\n\n"
        "Setze die Arbeit an dem Ticket auf Basis dieser Antwort fort. "
        "Wenn du wieder eine Entscheidung brauchst, beende erneut mit dem "
        f"‚{RUECKFRAGE_MARKER}‘-Marker; sonst liefere das Ergebnis ab.")


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


# WB-213: the lookup lives in setup.py so the STARTUP WARNING and the
# dispatcher cannot disagree about whether claude exists. dispatch imports
# setup, so it could not go the other way round. Re-exported here because
# this is where callers and tests have always found it.
resolve_claude = setup.resolve_claude


def worker_error_text(e: Exception) -> str:
    """WB-236 round 2: what a crashed worker writes into the ticket.

    A wrong `claude_bin` used to surface as "Fehlgeschlagen (interner Fehler
    der Werkbank): [Errno 2] No such file or directory: '/nirgendwo/claude'".
    The path was there, but "[Errno 2]" is jargon and "interner Fehler"
    blames the board for the user's configuration — while the README promises
    a plain-language reason instead of an error code."""
    if isinstance(e, FileNotFoundError) and getattr(e, "filename", None):
        return (f"Fehlgeschlagen: Das Programm {e.filename!r} gibt es auf "
                "diesem Rechner nicht. Meist steht in config.json ein falscher "
                "Pfad unter 'claude_bin' — oder Claude Code ist nicht "
                "installiert. Trag den richtigen Pfad ein und versuch es "
                "erneut.")
    return f"Fehlgeschlagen (interner Fehler der Werkbank): {e}"


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
    # WB-137: the CLI's result event carries the run's authoritative totals —
    # input/output/cache tokens plus total_cost_usd. Capture them so the
    # finalizer can persist them into the ticket for benchmarking.
    if ev.get("type") == "result":
        if usage:
            progress["tokens_in"] = int(usage.get("input_tokens") or 0)
            progress["tokens_out"] = int(usage.get("output_tokens") or 0)
            progress["tokens_cache"] = int(
                (usage.get("cache_creation_input_tokens") or 0)
                + (usage.get("cache_read_input_tokens") or 0))
        cost = ev.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            progress["cost_usd"] = float(cost)


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
            _signal_group(group, proc, SIGKILL_OR_TERM)
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


def run_claude(t, cfg: dict, on_start=None, on_event=None, on_pid=None,
               owner_dir=None):
    """Run a claude process for the ticket; returns (result_text, session_id).
    session_id is the run's own id from the JSON output, or None if unknown.
    on_start (WB-20) is called per attempt with what is certain at that moment:
    {"parent": resumed session or None, "forked": bool, "mode": str}.
    on_pid (WB-75) is called with the OS pid right after Popen, so the caller
    can persist it — a board restart mid-run must be able to find and kill the
    orphaned process, not just mark the ticket failed.
    owner_dir (WB-142 follow-up) is the tickets dir of the board that owns this
    run; it is stamped into the run's environment so the reaper can tell OUR
    runs from those of another board or a test suite on the same machine.
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
    # WB-123: an answered rueckfrage takes the FIXED answer path — no
    # continue/fresh fallback, since resuming the RIGHT session is the whole
    # point (a fresh run would lose the question). The answer prompt replaces
    # the ticket-body prompt for this one turn.
    answer_prompt = None
    if getattr(t, "answer", "") and getattr(t, "session", ""):
        modes = [("answer", t.session)]
        answer_prompt = build_answer_prompt(t)
    else:
        modes = attempt_modes(remembered, project_has_history(project))
    last_error = "unbekannter Fehler"
    for mode, resume_id in modes:
        cmd = build_command(claude_bin, t, mode, cfg, resume_id=resume_id,
                            force_fork=force_fork,
                            prompt=answer_prompt if mode == "answer" else None)
        if on_start:
            forked = ("--fork-session" in cmd) if mode != "fresh" else False
            on_start({"parent": resume_id, "forked": forked, "mode": mode})
        progress = {"steps": 0, "last_tool": None, "tokens": 0,
                    "session": None, "error": None, "limit": None}
        result_text = session_id = agent_error = None
        killed = []
        forced = saw_result = False
        # WB-142: identify the run by the ticket it belongs to. Read by
        # find_ownerless_agents via /proc/<pid>/environ; the argv already
        # names the ticket for claude runs, this covers both channels.
        # The OWNER dir scopes the id: ticket numbers repeat across boards
        # (every Werkbank counts WB-1, WB-2, …), so the id alone must never
        # be kill evidence for a foreign board's reaper.
        env = dict(os.environ)
        env["WERKBANK_TICKET_ID"] = getattr(t, "id", "") or ""
        if owner_dir:
            env["WERKBANK_TICKETS_DIR"] = str(Path(owner_dir).resolve())
        # WB-146: each project gets its OWN Claude config dir, so ~/.claude.json
        # is not shared across projects and issues #29051/#28813 stop applying
        # between them. Session history for --resume/--fork-session stays with
        # THIS project because the dir is per-project.
        if cfg.get("claude_per_project", True):
            try:
                own_dir = claude_config_dir_for(project, cfg)
            except OSError:
                own_dir = None      # cannot even create it — use the shared one
            if own_dir is not None:
                env["CLAUDE_CONFIG_DIR"] = str(own_dir)
        proc = subprocess.Popen(cmd, cwd=project, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env,
                                **_own_process_group())
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
            timeout, lambda: (killed.append(True), _signal_group(group, proc, SIGKILL_OR_TERM)))
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
            if resume_id and is_stale_session(agent_error + " " + (stderr or "")):
                # The remembered session cannot be resumed. That is exactly what
                # the fallback chain exists for — but the CLI reports it as a
                # RESULT event, and every result error used to end the run. So a
                # single dead session id failed every ticket of that project
                # instantly, forever, instead of falling back to a fresh run.
                forget_session(project, state_path)
                last_error = agent_error
                continue
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
    """All session ids state.json marks as interactive (any project). WB-144:
    the per-project entry carries an `interactive_ids` list so a background
    run's non-interactive write cannot silently drop the chat claim; legacy
    dict entries (interactive:true + id, no list) still count."""
    try:
        data = json.loads(Path(state_path or DEFAULT_STATE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, dict):
        return set()
    ids: set = set()
    for v in data.values():
        if not isinstance(v, dict):
            continue
        for x in (v.get("interactive_ids") or []):
            if isinstance(x, str) and x:
                ids.add(x)
        if v.get("interactive") and isinstance(v.get("id"), str):
            ids.add(v["id"])
    return ids


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
        dispatcher._bump_pause_until(float(until))
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
        # -ww: macOS `ps` truncates to the terminal width by default, so the
        # ticket id can fall off the end and an orphaned run is never
        # recognised — it then keeps burning quota with nobody to receive its
        # output. Linux accepts the same flag.
        r = subprocess.run(["ps", "-ww", "-p", str(int(pid)), "-o", "args="],
                           capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _process_matches_ticket(pid: int, ticket_id: str) -> bool:
    """True only when the PID is provably this ticket's own agent run —
    guards against PID reuse and against killing something innocent that
    happens to share the id. Two evidence paths (WB-142):

    - Claude runs put the ticket id and 'claude' in argv (WB-75 pattern).
    - Every agent run (claude AND opencode) now carries WERKBANK_TICKET_ID
      in its environment; /proc/<pid>/environ is authoritative and does not
      depend on how the wrapper program named itself.

    If neither is readable (unsupported OS), refuse — we only ever kill
    what we can PROVE is ours."""
    cmdline = _read_cmdline(pid)
    if cmdline and ticket_id in cmdline and "claude" in cmdline.lower():
        return True
    return _process_env(pid).get("WERKBANK_TICKET_ID") == ticket_id


def _process_env(pid: int) -> dict:
    """Environment of a process as {key: value}. Returns {} on any failure.
    Reads /proc/<pid>/environ — the same source ps and htop use, unavailable
    only on non-Linux platforms."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            raw = f.read()
    except OSError:
        return {}
    env = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        key, sep, value = entry.partition(b"=")
        if sep:
            try:
                env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
            except Exception:
                pass
    return env


def find_ownerless_agents(tickets_dir, projects_root=None):
    """WB-142: agent processes on the system that no ticket owns.

    Scans /proc for processes whose environment carries WERKBANK_TICKET_ID.
    A process is OWNERLESS when its ticket is not currently `in_arbeit` —
    the ticket was closed, deleted or moved on, but the process kept
    running. Returns a list of `(pid, ticket_id)` pairs; the caller decides
    whether to terminate.

    Only /proc entries readable by the current user are considered; the
    caller cannot see processes owned by other users, which is exactly
    the right scope — we should not touch strangers' processes."""
    try:
        alive = {int(p.name) for p in Path("/proc").iterdir()
                 if p.name.isdigit()}
    except OSError:
        return []
    try:
        tickets = {t.id: t for t in store.load_tickets(tickets_dir)}
    except OSError:
        tickets = {}
    own = str(Path(tickets_dir).resolve())
    ownerless = []
    for pid in alive:
        env = _process_env(pid)
        tid = env.get("WERKBANK_TICKET_ID")
        if not tid:
            continue
        # WB-142 follow-up: the id alone is NOT ownership. Ticket numbers
        # repeat across boards (every Werkbank counts WB-1, WB-2, …), and the
        # test suite spawns marked stand-ins while the real board runs. Only a
        # process stamped with OUR tickets dir may be judged — anything else
        # (foreign board, unmarked process) is not ours to kill.
        owner = env.get("WERKBANK_TICKETS_DIR")
        if not owner:
            continue
        try:
            if str(Path(owner).resolve()) != own:
                continue
        except OSError:
            continue
        t = tickets.get(tid)
        if t is None or t.status != "in_arbeit":
            ownerless.append((pid, tid))
    return ownerless


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
    # No /proc: macOS and the BSDs. `ps` knows the state, and a zombie must
    # count as dead here too — otherwise a successful kill looks like a failed
    # one, and the ticket says "the restart lost the run" when the run was in
    # fact ended (found on the macOS CI job, 2026-08-17).
    if os.name != "nt":
        try:
            r = subprocess.run(["ps", "-p", str(int(pid)), "-o", "state="],
                               capture_output=True, text=True, timeout=2)
            state = (r.stdout or "").strip()
            if r.returncode != 0 or not state:
                return False              # ps knows nothing about it → gone
            return not state.startswith("Z")
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if os.name == "nt":
        # os.kill(pid, 0) on Windows is TerminateProcess — probing with it
        # would kill the very process we are asking about. Without a stdlib
        # way to ask, "unknown" is the honest answer, and callers already
        # treat verification failure as "do not touch it".
        return False
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
        os.kill(pid, SIGKILL_OR_TERM)
    except OSError:
        pass
    end = time.time() + 0.5
    while time.time() < end:
        if not _is_running(pid):
            return True
        time.sleep(0.05)
    return False


CHAT_CLAIM_SECONDS = 60 * 60


def freshly_claimed(t, window: float = CHAT_CLAIM_SECONDS) -> bool:
    """True while a chat session's claim on this ticket is recent (WB-181).

    The claim lives in the TICKET (`claimed_at`), so it cannot drift out of
    sync with it — unlike the state file, which only learns about a session
    once that session registers, which its skill does when it FINISHES."""
    try:
        claimed = int(getattr(t, "claimed_at", "") or 0)
    except (TypeError, ValueError):
        return False
    return bool(claimed) and (time.time() - claimed) < window


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
        if freshly_claimed(t):
            continue          # WB-181: a chat claimed it minutes ago and works
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


def reap_ownerless_agents(tickets_dir) -> list:
    """WB-142: kill agent processes whose ticket is no longer `in_arbeit`.

    Cheap enough to run from the ticker: one /proc scan and one env read per
    /proc entry the caller owns. Returns the list of `(pid, ticket_id)` that
    were terminated — the caller journals or logs, this function only reaps.

    NEVER touches a process whose ticket IS still in_arbeit: that is the
    legitimate live run (its pid may or may not be recorded on the ticket
    yet; racing to kill it would recreate the exact bug this fights).

    The status is re-read FRESH right before killing (round 2): find takes
    its ticket snapshot before scanning /proc, so a run that starts in
    between — pump writes in_arbeit, then Popen — was judged by a stale
    status and got shot seconds after birth (measured: the lane self-heal
    test lost its second run to this on every pytest run). The write order
    makes the recheck watertight: the process can only exist in the scan
    AFTER its in_arbeit was written, so a fresh reload always sees it."""
    reaped = []
    candidates = find_ownerless_agents(tickets_dir)
    if not candidates:
        return reaped
    try:
        current = {t.id: t.status for t in store.load_tickets(tickets_dir)}
    except OSError:
        return reaped
    for pid, tid in candidates:
        if current.get(tid) == "in_arbeit":
            continue
        if _terminate_process(pid):
            reaped.append((pid, tid))
    return reaped


class Dispatcher:
    """FIFO dispatcher with one run per assortment, never more (WB-92). The
    Claude lane is single because of ~/.claude.json; the opencode lane is
    single because the local model would otherwise compete with itself."""

    def __init__(self, tickets_dir, cfg=None, runner=None):
        self.tickets_dir = tickets_dir
        self.cfg = cfg or {}
        # WB-142 round 2: ONE dispatcher per tickets dir, enforced with a
        # cross-process file lock. The swarm of 2026-08-16 came from extra
        # Dispatcher instances (a test harness importing werkbank.server
        # spawned one against the REAL board dir); each instance has its own
        # _pending, so every extra instance re-dispatched already-running
        # tickets and its death orphaned the runs. A dispatcher that cannot
        # take the lock stays INERT: it never starts, adopts, pumps or
        # hands over anything. flock dies with the process, so a crashed
        # board never leaves a stale lock. If the lock file cannot even be
        # opened (read-only or missing dir), we behave as before — an inert
        # board with a working store would be worse than a rare double.
        self._lock_fd = None
        self._lock_path = None
        self.exclusive = True
        try:
            # WB-184: on a FRESH install `tickets/` does not exist yet. Opening
            # the lock inside a missing directory fails, try_exclusive returns
            # None, and the board then considers itself "not this board" —
            # permanently, for the life of the process. Symptom: a brand-new
            # user drags a ticket to In Arbeit and NOTHING happens, with no
            # error anywhere, until they restart the board. The board owns this
            # directory; creating it is its job.
            Path(tickets_dir).mkdir(parents=True, exist_ok=True)
            lock_path = Path(tickets_dir) / ".dispatcher.lock"
            self._lock_fd = filelock.try_exclusive(lock_path)
            self.exclusive = self._lock_fd is not None
            # WB-229: remembered so the ticker can try again later. Losing the
            # race at boot must not be a life sentence.
            self._lock_path = lock_path
        except OSError:
            self.exclusive = True
        # The default runner MUST accept on_pid: _run_one filters kwargs by the
        # runner's signature, and the old lambda without on_pid silently
        # dropped the callback — so WB-75's pid recording never ran in
        # production (found 2026-08-16: WB-142's live run, ticket pid empty).
        self.runner = runner or (lambda t, on_start=None, on_event=None,
                                        on_pid=None:
                                 run_claude(t, self.cfg, on_start=on_start,
                                            on_event=on_event, on_pid=on_pid,
                                            owner_dir=str(self.tickets_dir)))
        # One queue AND one worker per assortment (WB-92 round 2): the per-lane
        # slot bookkeeping alone did not help while a single worker thread
        # executed all runs in series — an opencode ticket went in_arbeit and
        # then still waited behind the active Claude run inside the worker.
        self._queues = {"claude": queue.Queue(), "opencode": queue.Queue()}
        self._slot = {}  # ticket id -> its assortment (WB-92)
        # WB-92: one slot per assortment instead of one global slot, so an
        # opencode run never waits behind a Claude run (see `assortment`).
        self._pending = {"claude": set(), "opencode": set()}
        # WB-146: per-project locks for the claude lane. Different projects
        # run in parallel; same-project claude runs still serialise on this.
        self._project_locks = {}
        # WB-183: one FIFO queue AND one worker per project for the claude lane.
        # WB-146 gave every ticket its own thread so different projects could
        # run in parallel — but then two tickets of the SAME project raced for
        # the project lock, and whoever won ran first. The board promises order
        # (priority, ticket number, "move to front"), so order has to survive.
        self._project_queues = {}
        self._project_workers = {}
        self._handover_failed = set()  # tickets whose chat handover expired (WB-22)
        # WB-57/109: written by the worker thread (LimitError), by adopt_orphans
        # (ticker thread), read by pump_queue/pause_reason from multiple threads.
        # Always go through _get_pause_until / _bump_pause_until — a raw
        # `pause_until = max(pause_until, x)` is a read-modify-write race that
        # can drop the later of two near-simultaneous quota stops.
        self.pause_until = 0
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
        """Stop the background ticker and release the board lock (tests,
        shutdown). Idempotent."""
        self._stopped.set()
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None

    def _retry_exclusive(self) -> bool:
        """WB-229: try to become the owning dispatcher after a failed start.

        `exclusive` used to be decided once in __init__ and never revisited, so
        a board that booted while ANOTHER dispatcher held the lock stayed
        passive for the rest of its life — including long after that other
        process was gone. Nothing said so; the queue simply never moved."""
        if self.exclusive or self._lock_path is None:
            return self.exclusive
        fd = filelock.try_exclusive(self._lock_path)
        if fd is None:
            return False
        self._lock_fd = fd
        self.exclusive = True
        return True

    def _tick(self):
        interval = float(self.cfg.get("queue_poll_seconds", 15))
        while not self._stopped.wait(interval):
            if not self.exclusive and not self._retry_exclusive():
                continue   # another dispatcher owns this board (WB-142 r2)
            try:
                self.sweep_handovers()
                reap_ownerless_agents(self.tickets_dir)   # WB-142
                self.adopt_orphans()
                self.pump_queue()
            except Exception:
                pass   # a stalled tick must never kill the ticker

    def sweep_handovers(self):
        """WB-66: hand a ticket to a background run when the chat session did
        not claim it in time. The deadline comes from the ticket file, so board
        restarts cannot keep granting fresh windows (the old in-memory timer
        did exactly that — 'der nächste startet nicht')."""
        if not self.exclusive:
            return          # not this instance's board (WB-142 round 2)
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
        if not self.exclusive:
            return False   # not this instance's board (WB-142 round 2)
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
            # WB-146: claude runs of different projects may go in parallel
            # (own CLAUDE_CONFIG_DIR each — no shared ~/.claude.json). The
            # opencode lane stays strictly sequential: one local model.
            if lane == "claude":
                self._enqueue_for_project(ticket_id)
                continue
            self._execute_run(lane, ticket_id)

    def _enqueue_for_project(self, ticket_id: str) -> None:
        """Hand the ticket to its project's own worker: parallel ACROSS
        projects, strictly in order WITHIN one (WB-183)."""
        t = self._ticket_by_id(ticket_id)
        key = self._project_of(t) if t else ""
        with self._lock:
            q = self._project_queues.get(key)
            if q is None:
                q = self._project_queues[key] = queue.Queue()
            worker = self._project_workers.get(key)
            if worker is None or not worker.is_alive():
                worker = threading.Thread(
                    target=self._work_project, args=(key, q), daemon=True,
                    name=f"werkbank-project-{key or 'default'}")
                self._project_workers[key] = worker
                worker.start()
        q.put(ticket_id)

    def _work_project(self, key: str, q) -> None:
        """One consumer per project — that IS the ordering guarantee."""
        while True:
            ticket_id = q.get()
            self._execute_run("claude", ticket_id)

    def _project_lock(self, key: str) -> threading.Lock:
        """WB-146: one lock per project so several claude sub-threads never
        race on the same project's files. Cheap and self-cleaning — the map
        just holds Lock objects, tiny and only touched during dispatch."""
        with self._lock:
            lock = self._project_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._project_locks[key] = lock
        return lock

    def _execute_run(self, lane, ticket_id):
        # WB-146: enforce "one claude run per project" here, not only in
        # pump_queue — a direct dispatch() (adopt_orphans, handover wakeup,
        # etc.) would otherwise slip past the queue-side check.
        proj_lock = None
        if lane == "claude":
            t_for_lock = self._ticket_by_id(ticket_id)
            key = self._project_of(t_for_lock) if t_for_lock else ""
            proj_lock = self._project_lock(key)
            proj_lock.acquire()
        try:
            self._run_one(ticket_id)
        except Exception as e:  # never let the worker die silently
            try:
                # WB-236 round 2: a wrong `claude_bin` used to surface as
                # "[Errno 2] No such file or directory: '/nirgendwo/claude'" —
                # the path is there, but the errno is jargon, and the README
                # promises a plain-language reason instead of an error code.
                store.set_result(self.tickets_dir, ticket_id,
                                 worker_error_text(e))
                store.update_ticket(self.tickets_dir, ticket_id,
                                    {"status": "fehlgeschlagen"})
            except Exception:
                pass
        finally:
            with self._lock:
                slot = self._slot.pop(ticket_id, None)
                if slot is not None:
                    self._pending[slot].discard(ticket_id)
            if proj_lock is not None:
                proj_lock.release()
            self._queues[lane].task_done()
            try:
                self.pump_queue()  # WB-40: pull the next queued ticket
            except Exception:
                # This pump is opportunistic; the 15 s ticker re-pumps
                # anyway. Unguarded, it runs AFTER task_done() — i.e.
                # after join() unblocks — and a vanished tickets dir
                # (test teardown) killed the worker thread here, caught
                # only as a pytest thread-exception warning during
                # WB-102's gate run.
                pass

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
        reasons = store.blocking_reasons(all_tickets, t, include_exclusion=False,
                                         review_ok_projects=self._review_ok_projects())
        if reasons:
            store.set_result(self.tickets_dir, ticket_id,
                             "Nicht gestartet — " + "; ".join(reasons)
                             + ". Ticket zurück in Offen.")
            store.update_ticket(self.tickets_dir, ticket_id, {"status": "offen"})
            return
        # WB-123: a ticket with a pending answer is a resumed rueckfrage — go
        # straight to the runner, skip the handover branch entirely (the
        # answer belongs to the SAME background session that asked; handing
        # it to a chat would fork the conversation on both sides). The
        # answer stays on the ticket until the outcome lands so a mid-run
        # crash can be retried with the SAME answer via "Erneut versuchen".
        is_answer_resume = bool(getattr(t, "answer", "")) and bool(getattr(t, "session", ""))
        if not is_answer_resume:
            # WB-22: an interactive lineage gets the ticket handed over to the
            # live chat session (visible work there) instead of a silent
            # background run — unless the user forced a background fork
            # (checkbox) or a previous handover for this ticket already
            # expired unclaimed.
            project = t.project or self.cfg.get("default_project", "")
            entry = load_last_entry(project, self.cfg.get("state_path"))
            # WB-68: "already tried" must survive restarts, so it lives in
            # the ticket — otherwise a fresh dispatcher hands the same ticket
            # to the same silent session again, forever.
            if (getattr(t, "fork", "nein") != "ja" and entry and entry.get("interactive")
                    and getattr(t, "handover_expired", "") != "ja"
                    and ticket_id not in self._handover_failed
                    # WB-103: the assignee names the worker. A LOCAL-lane
                    # ticket never goes to a chat session — WB-102 (the
                    # lane's declared test run) would have been silently
                    # worked by Claude. WB-219: same for dsh.
                    and not is_local(getattr(t, "assignee", ""))):
                store.update_ticket(self.tickets_dir, ticket_id,
                                    {"handover": entry["id"],
                                     "handover_at": str(int(time.time()))})
                self.arm_handover_fallback(ticket_id)
                return
            # WB-161 / WB-168: an epic MUST be planned interactively — the whole
            # point is that the user is in the conversation. WB-168 extends the
            # same behaviour to any ticket that opts in via `interactive: ja`.
            # If we got past the WB-22 branch above, no live chat session was
            # available for the project. Bounce back to offen with instructions
            # rather than silently running in the background.
            wants_chat = (getattr(t, "type", "") == "epic"
                          or getattr(t, "interactive", "nein") == "ja")
            if wants_chat and not is_local(getattr(t, "assignee", "")):
                project = t.project or self.cfg.get("default_project", "")
                if getattr(t, "type", "") == "epic":
                    msg = (f"Epic wartet auf eine Chat-Session in „{project}“. "
                           "Öffne Claude Code in dem Projekt und sag „zieh dir "
                           "dein Ticket“ — die Session plant das Epic mit dir "
                           "und schreibt die Kind-Tickets. Solange bleibt das "
                           "Ticket in „Offen“.")
                else:
                    msg = (f"Ticket wartet auf eine Chat-Session in „{project}“ "
                           "(interaktiv angefordert). Öffne Claude Code in dem "
                           "Projekt und sag „zieh dir dein Ticket“. Solange "
                           "bleibt das Ticket in „Offen“.")
                store.set_result(self.tickets_dir, ticket_id, msg)
                store.update_ticket(self.tickets_dir, ticket_id, {"status": "offen"})
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

        # A LOCAL-lane ticket never reaches the Claude runner: the local model
        # implements it and its GATE decides whether it worked. No gate ->
        # refused. WB-219: dsh joins this path unchanged — its wrapper answers
        # the same contract, so the exit-code mapping below covers both.
        if is_local(getattr(t, "assignee", "")):
            # WB-76: keep the card alive. A local run can take many minutes and
            # an in_arbeit card showing nothing is the "I cannot see what is
            # happening" complaint this tool already has.
            on_start({"model": (getattr(t, "assignee", "") or "opencode"),
                      "ticket": ticket_id})

            def on_progress(step):
                with self._lock:
                    info = self._runs.get(ticket_id)
                    if info is not None:
                        info["last_tool"] = step
                        info["last"] = datetime.now().strftime("%H:%M:%S")
                        info["last_ts"] = time.time()

            t0 = time.monotonic()   # WB-139
            try:
                outcome = opencode.work_ticket(t, self.cfg,
                                               on_progress=on_progress,
                                               on_pid=on_pid,
                                               owner=str(self.tickets_dir))
            finally:
                with self._lock:
                    self._runs.pop(ticket_id, None)
            changes = {"status": outcome.status, "limit_until": "", "pid": "",
                       "duration_s": str(round(time.monotonic() - t0))}
            changes.update(outcome.changes)
            self._finalize_run(ticket_id, outcome.result, changes)
            return

        session = None
        t0 = time.monotonic()   # WB-139: measure the actual work, not the queue wait
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
            # WB-123: the agent may end its run with a marker asking the user
            # a question. Park in a dedicated status so the lane is free right
            # away; the answer endpoint flips this back to in_arbeit and
            # resumes the SAME session (no wasted context).
            status = "rueckfrage" if is_query_result(result) else "review"
        except LimitError as e:
            # WB-57: the account ran out, not the work. Park the ticket back in
            # the queue; the reset restarts it — no "continue" needed.
            until = self._bump_pause_until(e.resets_at or (time.time() + 3600))
            when = datetime.fromtimestamp(until).strftime("%H:%M")
            with self._lock:
                self._runs.pop(ticket_id, None)
            store.set_result(self.tickets_dir, ticket_id,
                             f"Pausiert — Claude-Kontingent aufgebraucht: {e} "
                             f"Das Ticket bleibt in Arbeit (rot markiert) und wird ab "
                             f"{when} Uhr automatisch fortgesetzt. Du musst nichts tun.")
            store.update_ticket(self.tickets_dir, ticket_id,
                                {"limit_until": str(int(until)),
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
                run_info = self._runs.pop(ticket_id, None) or {}
        # WB-69: pause cleared. WB-75: process is dead, pid must not linger
        # or the next sweep would try to kill a random reused PID. WB-123:
        # the answer (if any) has now been consumed by this run — clear it
        # so a manual retry does not re-send yesterday's reply to today's
        # question. A retry of a FAILED answered run wants the answer, so
        # only clear on success/rueckfrage; on fehlgeschlagen keep it.
        changes = {"status": status, "limit_until": "", "pid": "",
                   "duration_s": str(round(time.monotonic() - t0))}
        if session:
            changes["session"] = session
        if status != "fehlgeschlagen" and getattr(t, "answer", ""):
            changes["answer"] = ""
        # WB-137: persist what the run actually cost so benchmarks have real
        # numbers. Only the claude lane emits the result event — opencode's
        # runner sets neither, so run_info stays empty for those and no
        # fields are written. `cost_usd` is stringified to fit the flat
        # frontmatter; store's validator accepts any string.
        for key in ("tokens_in", "tokens_out", "tokens_cache"):
            if run_info.get(key) is not None:
                changes[key] = str(run_info[key])
        if isinstance(run_info.get("cost_usd"), (int, float)):
            # 4 places is a bit finer than a cent — enough to keep the
            # aggregate honest across hundreds of runs.
            changes["cost_usd"] = f"{run_info['cost_usd']:.4f}"
        self._finalize_run(ticket_id, result, changes)

    def _finalize_run(self, ticket_id, result, changes):
        """Write the run's outcome UNLESS the ticket has moved on since we
        started (WB-135 finding 3, extended). `_run_one` only enters when the
        ticket is `in_arbeit`, and normal internal transitions (pid write via
        on_pid, quota pause via limit_until) keep the status. Any OTHER
        current status is therefore a user decision that arrived while we
        worked: `erledigt` (accepted), `offen` (rejected via 'Ablehnen mit
        Grund'), `zu_bearbeiten` (manually requeued). Overwriting any of them
        silently erases the decision; we skip. `set_result` and
        `update_ticket` are each atomic under the store's lock; between the
        two writes another writer could still win, in which case
        `ConflictError` on the second one keeps us out instead of overwriting."""
        try:
            current = {x.id: x for x in store.load_tickets(self.tickets_dir)}.get(ticket_id)
        except OSError:
            return
        # `rueckfrage` is a legitimate intermediate state a resumed run may
        # arrive at (its own previous invocation set it) — accept as well.
        if current is None or current.status not in ("in_arbeit", "rueckfrage"):
            return
        try:
            store.set_result(self.tickets_dir, ticket_id, result)
            # After set_result the version has bumped; carry that forward as
            # our baseline for the status write so a user's mid-flight edit
            # still wins.
            latest = {x.id: x for x in store.load_tickets(self.tickets_dir)}[ticket_id]
            changes = dict(changes)
            changes.setdefault("version", latest.version)
            store.update_ticket(self.tickets_dir, ticket_id, changes)
        except store.ConflictError:
            pass

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
        assignee = (getattr(t, "assignee", "") or "").strip()
        if not known_assignee(assignee):
            return (f"Bearbeiter „{assignee}“ startet nicht automatisch — "
                    "im Chat abarbeiten lassen oder Bearbeiter ändern")
        project = self._project_of(t)
        if any(x.status == "in_arbeit" and self._project_of(x) == project
               for x in all_tickets):
            return "wartet, bis das laufende Ticket fertig ist"
        nonblocking = (self.cfg.get("nonblocking_review") or {}).get(project)
        if not nonblocking and any(x.status == "review" and self._project_of(x) == project
                                   for x in all_tickets):
            return "wartet auf deine Abnahme in Review"
        reasons = store.blocking_reasons(all_tickets, t,
                                         review_ok_projects=self._review_ok_projects())
        return "; ".join(reasons) if reasons else None

    def _review_ok_projects(self) -> set:
        """WB-136: which projects the user marked as nonblocking_review — a
        review-status blocker in one of these does not hold back `nach`-linked
        tickets. Same source of truth as the same-project review check above."""
        return {p for p, on in (self.cfg.get("nonblocking_review") or {}).items() if on}

    def _freshly_claimed(self, t) -> bool:
        """Has a chat session claimed this ticket recently? (see freshly_claimed)

        WB-181, observed live: a chat session claims a ticket exactly as its
        skill prescribes (status in_arbeit + its own session id) and starts
        working. The board saw an in_arbeit ticket with no run, called it
        stranded, handed it to a chat — and when that handover deadline passed,
        put the ticket BACK INTO OFFEN while the session was still working on
        it. From the outside the ticket simply never moved.

        The old guard asked the state file whether the session is "interactive",
        which is only true after the session REGISTERS — and the skill registers
        when it FINISHES. So the guard could not protect the very window it
        existed for. A timestamp in the ticket cannot drift out of sync with
        the ticket, so that is where the claim now lives."""
        window = float(self.cfg.get("chat_claim_minutes", 60)) * 60
        return freshly_claimed(t, window)

    def adopt_orphans(self):
        """WB-68b: a ticket in in_arbeit with no handover marker and no run is
        stuck where nobody looks. Pick it up — unless a chat session is
        actively working it (its own session id is the interactive one), or the
        account is out of quota (WB-69: then it is waiting on purpose)."""
        if not self.exclusive or self.pause_reason():
            return
        interactive = _interactive_ids(self.cfg.get("state_path"))
        with self._lock:
            pending = set().union(*self._pending.values()) | set(self._runs)
        for t in store.load_tickets(self.tickets_dir):
            if t.status != "in_arbeit" or t.handover or t.id in pending:
                continue
            if t.session and t.session in interactive:
                continue          # someone is working it in a chat right now
            if self._freshly_claimed(t):
                continue          # WB-181: a chat claimed it minutes ago
            if t.limit_until and int(t.limit_until or 0) > int(time.time()):
                continue          # WB-69: paused on quota, resume is armed
            if not known_assignee(getattr(t, "assignee", "")):
                continue          # WB-105: a human's ticket is not an orphan
            self.dispatch(t.id)

    def _get_pause_until(self) -> float:
        with self._lock:
            return self.pause_until

    def _bump_pause_until(self, candidate: float) -> float:
        """Atomically raise pause_until to at least `candidate`. Returns the
        new value. Never lowers; two concurrent quota stops both take effect."""
        with self._lock:
            if candidate > self.pause_until:
                self.pause_until = candidate
            return self.pause_until

    def pause_reason(self):
        """German reason why the whole queue is resting, or None (WB-57)."""
        # Snapshot once — two reads of self.pause_until could otherwise see
        # two different values across a concurrent bump.
        until = self._get_pause_until()
        if time.time() >= until:
            return None
        when = datetime.fromtimestamp(until).strftime("%H:%M")
        return ("Claude-Kontingent aufgebraucht — die Warteschlange macht um "
                f"{when} Uhr von selbst weiter.")

    def pump_queue(self):
        """Start the next queued ticket if nothing blocks it. Called after every
        finished run and after board status changes."""
        if not self.exclusive or self.pause_reason():
            return
        try:
            all_tickets = store.load_tickets(self.tickets_dir)
        except OSError:
            return
        queued = [t for t in all_tickets if t.status == "zu_bearbeiten"]
        # WB-138: within a priority, honour the user's manual order
        # (effective_queue_pos = explicit queue_pos or ticket number).
        queued.sort(key=lambda t: (self.PRIORITY_ORDER.get(t.priority, 1),
                                   store.effective_queue_pos(t),
                                   store._ticket_number(t.id)))
        # WB-146: keep starting tickets until every remaining candidate is
        # blocked — claude runs of different projects may now go in parallel.
        # Reload after each start so the same-project reason sees the new
        # in_arbeit ticket and stops us from double-dispatching.
        started = 0
        for t in queued:
            if self._queue_blocked_reason(all_tickets, t):
                continue
            if self._slot_taken(t):
                continue
            store.update_ticket(self.tickets_dir, t.id, {"status": "in_arbeit"})
            self.dispatch(t.id)
            started += 1
            try:
                all_tickets = store.load_tickets(self.tickets_dir)
            except OSError:
                break
        return started

    def _slot_taken(self, t) -> bool:
        """True while this ticket's assortment already has a queued/active run
        it cannot join. WB-92: opencode is single-lane globally (one local
        model). WB-146: claude is single-lane PER PROJECT — each project has
        its own CLAUDE_CONFIG_DIR and thus its own ~/.claude.json, so two
        projects no longer share the file that made concurrent runs unsafe."""
        slot = assortment(getattr(t, "assignee", None))
        with self._lock:
            pending = set(self._pending[slot])
        if slot == LOCAL_SLOT:
            return bool(pending)
        # claude: same-project only
        if not pending:
            return False
        my_project = self._project_of(t)
        by_id = {x.id: x for x in store.load_tickets(self.tickets_dir)}
        return any((by_id.get(tid) and self._project_of(by_id[tid]) == my_project)
                   for tid in pending)

    def _arm_resume(self, ticket_id=None):
        """Wake the queue when the quota reset time arrives (WB-57/69). If a
        ticket_id is given, pick THAT ticket up (it stayed in in_arbeit)."""
        delay = max(5, self._get_pause_until() - time.time() + 5)
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
