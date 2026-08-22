"""Ticket storage: one markdown file per ticket with flat key: value frontmatter.

The files are the source of truth — the board and the agents both read and write
through this module, but a hand-edited ticket file is equally valid.
"""

import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from werkbank import filelock

# One lock for all ticket writes (WB-9). The RLock serializes board handler
# threads and the dispatcher worker inside the server process; the flock in
# _locked() additionally serializes OTHER processes (chat sessions write
# tickets through this module too).
_WRITE_LOCK = threading.RLock()


@contextmanager
def _locked(tickets_dir):
    """One full read-modify-write cycle. NOT nestable: code already holding it
    must call the _*_locked internals, never the public write functions."""
    with _WRITE_LOCK:
        d = Path(tickets_dir)
        d.mkdir(parents=True, exist_ok=True)
        # F7 (WB-35): the lock refuses to follow a planted symlink where the
        # platform supports it. WB-43: works on Unix and Windows alike.
        with filelock.exclusive(d / ".lock"):
            yield


class ConflictError(ValueError):
    """A write based on a stale version — rejected instead of overwriting."""

# zu_bearbeiten (WB-40) is the queue: the board pulls the next one by itself.
# rueckfrage (WB-123): the run paused with a question for the user; its lane
# is free so other tickets keep moving. An answer via the board flips this
# back to in_arbeit and resumes the same session.
STATUSES = ["offen", "zu_bearbeiten", "in_arbeit", "rueckfrage",
            "review", "fehlgeschlagen", "erledigt"]
PRIORITIES = ["hoch", "normal", "niedrig"]
# WB-161: `epic` is a planning ticket — worked interactively in the target
# project's chat, its "work" is writing child tickets (each carries
# `epic: WB-<parent>` in its frontmatter, see KEYS below).
TYPES = ["aufgabe", "bug", "epic"]

# Frontmatter keys, in the order they are written to disk. Older ticket files
# may lack `type`, `nach` or `nicht_mit`; parsing falls back to the dataclass
# defaults. `nach`/`nicht_mit` are comma lists of ticket ids (WB-12).
# WB-161: `epic` on a child ticket names its parent epic id — empty on the
# epic itself and on any ticket that is not part of one.
# WB-168: `interactive: ja` forces the dispatch to prefer a chat session
# and bounce back to Offen when none is registered (same semantics as an
# epic, opted in per ticket for non-epic types).
# WB-170: `review_cost_usd` is the cumulative $ spent by the on-demand
# Review-Bot on this ticket (multiple clicks add up); empty = never
# reviewed OR the CLI returned non-JSON that one time.
# WB-226: `gate_gap` is free-form German prose naming what the configured
# check does NOT cover for this ticket (typical: UI/animation/layout,
# where a green "compiles and unchanged logic still runs" gate does not
# equal an accepted result). Non-empty → dispatch refuses to autostart
# and bounces back to Offen with the gap text, for BOTH lanes. Empty →
# normal behaviour.
KEYS = ["id", "title", "type", "status", "assignee", "project", "priority",
        "nach", "nicht_mit", "fork", "gate", "gate_gap", "review", "version",
        "session", "handover", "handover_at", "handover_expired",
        "limit_until", "pid", "answer", "tokens_in", "tokens_out",
        "tokens_cache", "cost_usd", "duration_s", "queue_pos", "epic",
        "interactive", "review_cost_usd", "orphaned", "backend",
        "claimed_at",
        "created", "updated"]
FORK_VALUES = ["ja", "nein"]
INTERACTIVE_VALUES = ["ja", "nein"]
# WB-238: per-ticket choice for the dsh runner (opencode ignores it; a
# `backend` value on an opencode ticket is rejected, not silently dropped).
# "" and "local" mean the local model (default, current behaviour); "claude"
# routes the run through the local Claude CLI and thus the subscription
# quota — the surface the form warning names.
BACKEND_VALUES = ["", "local", "claude"]
# A gate NAME, never a command: no shell metacharacters can survive this.
GATE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")


def is_gate_name(value) -> bool:
    return isinstance(value, str) and (value == "" or bool(GATE_NAME_RE.fullmatch(value)))


@dataclass
class Ticket:
    id: str
    title: str
    type: str = "aufgabe"
    status: str = "offen"
    assignee: str = "claude"
    project: str = ""
    priority: str = "normal"
    nach: str = ""        # must run after these tickets are erledigt
    nicht_mit: str = ""   # must not be worked at the same time as these
    fork: str = "nein"    # ja = agent works on a fork of the ticket session
    gate: str = ""        # shell command that decides "fertig" — NEVER settable via the API
    gate_gap: str = ""    # WB-226: what the gate does NOT cover — non-empty blocks autostart
    orphaned: str = ""    # WB-230: "ja" once detected as a live orphan — no autostart, user decides
    backend: str = ""     # WB-238: dsh runner backend — "" | "local" | "claude"
    review: str = ""      # "nein" skips the paid diff review after a green gate
    version: str = "1"    # write counter for lost-update detection (WB-9)
    session: str = ""     # session id of the run that worked this ticket (WB-20)
    handover: str = ""    # session id a dragged ticket was handed to (WB-22)
    handover_at: str = "" # unix time the handover was set — survives restarts (WB-66)
    handover_expired: str = ""  # "ja" once a handover went unclaimed (WB-68)
    limit_until: str = ""       # unix time the run pauses on quota (WB-69)
    pid: str = ""               # OS pid of the live claude process — kills orphans (WB-75)
    answer: str = ""            # user reply to a rueckfrage — consumed on next dispatch (WB-123)
    # WB-137: what the run actually cost — captured from the CLI's result event.
    # All strings (frontmatter is flat), empty = not measured. Only claude runs
    # report cost; opencode reports tokens but no cost (local model).
    tokens_in: str = ""         # input_tokens from the result event
    tokens_out: str = ""        # output_tokens
    tokens_cache: str = ""      # cache_creation + cache_read (both count against quota differently)
    cost_usd: str = ""          # total_cost_usd (claude only)
    # WB-139: wall-clock of the LAST attempt in whole seconds. Includes what
    # the agent does (tool calls, gate, docs, journal) but NOT the wait in the
    # queue or a quota pause — a paused run starts a fresh attempt whose time
    # overwrites this, so benchmarks see actual work, not a 4-hour pause.
    duration_s: str = ""
    # WB-138: manual queue rank inside a priority. Empty = fall back to the
    # ticket number (historical order). Move-up swaps this with the peer above
    # so the user can walk a ticket forward one click at a time without
    # touching the priority.
    queue_pos: str = ""
    # WB-161: parent epic id on child tickets ("WB-N"), empty otherwise.
    epic: str = ""
    # WB-168: "ja" forces dispatch to prefer a chat session — bounces back
    # to Offen (with instructions) when no interactive lineage is registered
    # for the project, instead of falling through to a background run.
    interactive: str = "nein"
    # WB-170: cumulative $ the on-demand Review-Bot has spent on this ticket.
    # String because the frontmatter is flat text; formatted to 4 decimals
    # by append_review_note, empty until the first successful (JSON) review.
    review_cost_usd: str = ""
    # WB-181: unix time a CHAT session claimed this ticket. The board must not
    # treat a ticket somebody is visibly working on as stranded — and the proof
    # of that claim belongs in the ticket, not in a side file that can drift.
    claimed_at: str = ""
    created: str = ""
    updated: str = ""
    body: str = ""

    def to_dict(self):
        return {k: getattr(self, k) for k in KEYS + ["body"]}


def parse_ticket(text: str) -> Ticket:
    # Error texts are German: the board shows them to the user (WB-8).
    m = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text, re.DOTALL)
    if not m:
        raise ValueError("Kein sauberer Frontmatter-Block (--- … ---) am Dateianfang")
    meta = {}
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"Unlesbare Frontmatter-Zeile: {line!r}")
        key = key.strip()
        if key in meta:  # F4: a duplicate key silently overrode the first one
            raise ValueError(f"Feld '{key}' steht doppelt im Frontmatter.")
        meta[key] = value.strip()
    missing = [k for k in ("id", "title") if k not in meta]
    if missing:
        raise ValueError(f"Pflichtfelder fehlen im Frontmatter: {', '.join(missing)}")
    kwargs = {k: meta[k] for k in KEYS if k in meta}
    return Ticket(body=m.group(2).lstrip("\n"), **kwargs)


def serialize_ticket(t: Ticket) -> str:
    lines = []
    for k in KEYS:
        value = str(getattr(t, k))
        # F4 (WB-35): a newline in any field would smuggle extra frontmatter
        # lines — later keys override earlier ones on re-read, which let a
        # ticket rewrite its own id/status/project. Refuse instead.
        if "\n" in value or "\r" in value:
            raise ValueError(f"Zeilenumbruch im Feld '{k}' ist nicht erlaubt.")
        lines.append(f"{k}: {value}".rstrip())
    return "---\n" + "\n".join(lines) + "\n---\n\n" + t.body


def link_ids(value: str) -> list:
    """Parse a comma list of ticket ids from a link field."""
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def normalize_links(value: str) -> str:
    ids = link_ids(value)
    for x in ids:
        if not re.fullmatch(r"WB-\d+", x):
            raise ValueError(
                f"Ungültige Ticket-Verknüpfung: '{x}' (erwartet WB-Nummern, kommagetrennt)")
    return ", ".join(dict.fromkeys(ids))  # dedupe, keep order


STATUS_LABEL = {
    "offen": "noch offen", "zu_bearbeiten": "in der Warteschlange",
    "in_arbeit": "in Arbeit",
    "rueckfrage": "wartet auf deine Antwort",
    "review": "in Review, noch nicht abgenommen",
    "fehlgeschlagen": "fehlgeschlagen", "erledigt": "erledigt",
}


def blocking_reasons(all_tickets, t, include_exclusion: bool = True,
                     review_ok_projects=None) -> list:
    """German reasons why `t` may not start now. References to unknown ids never
    block. Exclusion can be skipped: the dispatcher runs strictly one at a time,
    so at run time only the `nach` order can still be violated.

    WB-136: a blocker in `review` for a project the user marked as
    `nonblocking_review` counts as done for the queue — from the agent's
    perspective the work is finished; only the user's acceptance is pending,
    and the user chose that this shouldn't hold anything back."""
    review_ok_projects = review_ok_projects or set()
    by_id = {x.id: x for x in all_tickets}
    reasons = []
    for lid in link_ids(t.nach):
        other = by_id.get(lid)
        if not other or other.status == "erledigt":
            continue
        if other.status == "review" and (other.project or "") in review_ok_projects:
            continue
        reasons.append(f"wartet auf {lid} ({STATUS_LABEL[other.status]})")
    if include_exclusion:
        conflicting = set()
        for lid in link_ids(t.nicht_mit):
            other = by_id.get(lid)
            if other and other.status == "in_arbeit":
                conflicting.add(lid)
        for other in all_tickets:
            if (other.id != t.id and other.status == "in_arbeit"
                    and t.id in link_ids(other.nicht_mit)):
                conflicting.add(other.id)
        for lid in sorted(conflicting, key=_ticket_number):
            reasons.append(f"nicht gleichzeitig mit {lid} (in Arbeit)")
    return reasons


def _slug(title: str) -> str:
    s = title.lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40] or "ticket"


def _ticket_number(ticket_id: str) -> int:
    m = re.fullmatch(r"WB-(\d+)", ticket_id)
    return int(m.group(1)) if m else 0


def _write_ticket_file(path: Path, text: str) -> None:
    """Atomic write (WB-32): plain write_text truncates first, so the board's
    reader thread could see a half-written file and flag the ticket as broken.
    Write a hidden sibling, then os.replace — readers see old or new, never
    partial."""
    # F7 (WB-35): mkstemp never follows a planted symlink and is unguessable.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".wb-", suffix=".tmp")
    try:
        # newline="\n" keeps ticket files byte-identical on every platform
        # (Windows would otherwise write \r\n) — WB-43.
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(tmp_name, 0o644)
        filelock.replace_with_retry(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _paths(tickets_dir) -> list:
    # F8 (WB-35): never read or write through a symlink placed in tickets/.
    return sorted(p for p in Path(tickets_dir).glob("WB-*.md")
                  if not p.is_symlink())


def _read_with_retry(path, attempts: int = 5, pause: float = 0.05) -> str:
    """Read a ticket, tolerating the instant a writer is replacing it.

    Writes are atomic (`os.replace` onto a fully written temp file), so a
    reader never sees half a ticket. On Windows it can, however, meet the
    replace itself: opening a file that is being swapped raises
    PermissionError. Retrying briefly turns that into the non-event it is —
    without it the board reports a perfectly healthy ticket as broken."""
    last = None
    for attempt in range(attempts):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError as e:      # Windows: file busy during replace
            last = e
            time.sleep(pause)
    raise last


OPENCODE_SECTIONS = ("## Tests / Abnahme", "## Fertig, wenn")


def opencode_ticket_gaps(ticket, gates_configured: bool = True,
                         gate: str = None) -> list:
    """What a ticket still lacks before a LOCAL model can work it (WB-197).

    opencode is a small model. It does not fill gaps, it guesses — and a guess
    that fails the check twice escalates to Claude, which costs more than
    writing the ticket properly would have. So a ticket for the local lane has
    to carry its own instructions: numbered steps decided in advance, the exact
    commands that prove the work, a tick-list for "done", and the gate that
    makes the board enforce it.

    Returns plain German gaps, empty when the ticket is ready. It judges the
    TEXT, not the model — no check can tell a precise step from a vague one,
    so this catches the structural omissions and leaves the judgement to whoever
    writes the ticket."""
    body = getattr(ticket, "body", None)
    if body is None:
        body = str(ticket)
    gaps = []
    for heading in OPENCODE_SECTIONS:
        if heading.lower() not in body.lower():
            gaps.append(f"Abschnitt „{heading}“ fehlt")
    description = body.split("## Ergebnis")[0]
    if not re.search(r"^\s*\d+[.)]\s+\S", description, re.M):
        gaps.append("keine nummerierten Schritte — ein kleines Modell braucht "
                    "eine Reihenfolge, keine Absicht")
    # WB-263 round 4: the skill tells the agent to run this against a DRAFT —
    # a plain string, which has no `gate` attribute, so the check reported the
    # gate as missing even for a perfect draft and could never say "ready".
    # The gate is chosen before creation; let the caller say which one.
    chosen = gate if gate is not None else (getattr(ticket, "gate", "") or "")
    if gates_configured and not chosen.strip():
        gaps.append("kein `gate:` gesetzt — ohne Prüfung startet das Board das "
                    "Ticket gar nicht")
    return gaps


def claim_ticket(tickets_dir, ticket_id: str, session_id: str):
    """A CHAT session takes this ticket: in_arbeit, its own session id, and the
    timestamp that tells the board somebody is on it (WB-181).

    One call, because three separate fields are three chances to forget one —
    and the field that got forgotten was exactly the one that kept the board
    from taking the ticket back mid-work. Also clears a handover marker: if the
    ticket was handed over, claiming IS the answer to it."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id fehlt — ohne Session kein Anspruch "
                         "(nie raten; $CLAUDE_CODE_SESSION_ID benutzen)")
    return update_ticket(tickets_dir, ticket_id, {
        "status": "in_arbeit",
        "session": session_id.strip(),
        "claimed_at": str(int(time.time())),
        "handover": "",
        "handover_at": "",
    })


def release_claim(tickets_dir, ticket_id: str):
    """WB-204: give a stalled chat claim back to the queue. Undoes exactly what
    `claim_ticket` did — status, session and the claim stamp — so the next run
    starts clean instead of resuming a session that has moved on.

    Only from `in_arbeit`: releasing anything else would silently requeue work
    that is genuinely under way, and the caller (the board) must first make
    sure no board run holds the ticket."""
    t = next((x for x in load_tickets(tickets_dir) if x.id == ticket_id), None)
    if t is None:
        raise KeyError(f"no ticket {ticket_id}")
    if t.status != "in_arbeit":
        raise ValueError("Nur Tickets in „In Arbeit“ lassen sich zurücklegen.")
    return update_ticket(tickets_dir, ticket_id, {
        "status": "zu_bearbeiten", "session": "", "claimed_at": "",
        "handover": "", "handover_at": "",
    })


def load_tickets_with_errors(tickets_dir):
    """A broken file must only affect itself (WB-8): readable tickets are
    returned normally, unreadable files land in the error list."""
    tickets, errors = [], []
    for p in _paths(tickets_dir):
        try:
            tickets.append(parse_ticket(_read_with_retry(p)))
        except (ValueError, OSError, UnicodeDecodeError) as e:
            errors.append({"file": p.name, "error": str(e)})
    tickets.sort(key=lambda t: _ticket_number(t.id))
    return tickets, errors


def load_tickets(tickets_dir) -> list:
    return load_tickets_with_errors(tickets_dir)[0]


def _find_path(tickets_dir, ticket_id: str) -> Path:
    matches = [p for p in _paths(tickets_dir)
               if p.name.startswith(ticket_id + "-") or p.stem == ticket_id]
    if len(matches) > 1:
        # WB-101: a duplicate id once let updates land on the wrong ticket
        # (first match by filename). Failing loudly beats silent misdirection.
        raise ValueError(
            f"Ticket-Nummer {ticket_id} ist mehrfach vergeben "
            f"({', '.join(p.name for p in matches)}) — bitte zuerst umnummerieren.")
    if matches:
        return matches[0]
    raise KeyError(f"no ticket {ticket_id}")


def create_ticket(tickets_dir, title: str, description: str, assignee: str = "claude",
                  project: str = "", priority: str = "normal",
                  type: str = "aufgabe", nach: str = "", nicht_mit: str = "",
                  fork: str = "nein", gate: str = "", epic: str = "",
                  interactive: str = "nein", gate_gap: str = "",
                  backend: str = "") -> Ticket:
    tickets_dir = Path(tickets_dir)
    tickets_dir.mkdir(parents=True, exist_ok=True)
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    if type not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    nach, nicht_mit = normalize_links(nach), normalize_links(nicht_mit)
    if fork not in FORK_VALUES:
        raise ValueError(f"fork must be one of {FORK_VALUES}")
    if interactive not in INTERACTIVE_VALUES:
        raise ValueError(f"interactive must be one of {INTERACTIVE_VALUES}")
    if not is_gate_name(gate):
        raise ValueError("gate must be a plain check name")
    # WB-226: same one-line normalisation as `_update_locked` applies later —
    # do it here too so the on-disk shape is identical whether the field was
    # set at create-time or via a follow-up PATCH.
    gate_gap = re.sub(r"[\r\n]+", " ", gate_gap or "").strip()
    # WB-238: same validation as `_update_locked` — the only difference is we
    # know the assignee for sure at create time, so the "backend only on dsh"
    # check is unambiguous.
    if backend not in BACKEND_VALUES:
        raise ValueError(
            f"backend muss einer von {BACKEND_VALUES} sein — 'local' oder "
            f"leer heißt lokales Modell, 'claude' route den Lauf über den "
            f"Claude-CLI und verbraucht Abo-Kontingent.")
    if backend and (assignee or "").strip().lower() != "dsh":
        raise ValueError(
            "backend gilt nur für Tickets mit „Zugewiesen an: dsh“. "
            "Für claude/opencode-Tickets bitte das Feld leer lassen.")
    with _locked(tickets_dir):
        return _create_locked(tickets_dir, title, description, assignee, project,
                              priority, type, nach, nicht_mit, fork, gate, epic,
                              interactive, gate_gap, backend)


def _read_highest_counter(tickets_dir) -> int:
    """tickets/.highest-id remembers the highest number EVER assigned, so a
    deleted ticket's number is never reissued (WB-101 — old logs and journal
    entries keep pointing at the number they meant). The file is committed on
    purpose: two checkouts allocating in parallel then produce a visible merge
    conflict instead of two tickets silently sharing an id."""
    try:
        return int((Path(tickets_dir) / ".highest-id").read_text().strip())
    except (OSError, ValueError):
        return 0


def _create_locked(tickets_dir, title, description, assignee, project, priority,
                   type, nach, nicht_mit, fork, gate="", epic="",
                   interactive="nein", gate_gap="", backend="") -> Ticket:
    nums = [_ticket_number(parse_ticket(p.read_text(encoding="utf-8")).id)
            for p in _paths(tickets_dir)]
    next_num = max(max(nums, default=0), _read_highest_counter(tickets_dir)) + 1
    today = date.today().isoformat()
    t = Ticket(
        id=f"WB-{next_num}",
        title=title.strip(),
        type=type,
        nach=nach,
        nicht_mit=nicht_mit,
        fork=fork,
        gate=gate,
        gate_gap=gate_gap,
        epic=epic,
        interactive=interactive,
        backend=backend,
        assignee=assignee.strip() or "claude",
        project=project,
        priority=priority,
        created=today,
        updated=today,
        body=f"## Beschreibung\n\n{description.strip()}\n\n## Ergebnis\n\n_(noch offen)_\n",
    )
    path = tickets_dir / f"{t.id}-{_slug(t.title)}.md"
    _write_ticket_file(path, serialize_ticket(t))
    (Path(tickets_dir) / ".highest-id").write_text(f"{next_num}\n", encoding="utf-8")
    return t


def create_bug_for(tickets_dir, ticket_id: str, description: str) -> Ticket:
    """Report a bug against an existing ticket (WB-71).

    The new ticket carries the original's title, description and result, so the
    agent that picks it up sees what was built and what was claimed about it —
    that context is the whole point of reporting the bug from the card."""
    description = (description or "").strip()
    if not description:
        raise ValueError("Bitte beschreiben, was nicht stimmt.")
    with _locked(tickets_dir):
        path = _find_path(tickets_dir, ticket_id)
        orig = parse_ticket(path.read_text(encoding="utf-8"))
        m = re.match(r"## Beschreibung\n+([\s\S]*?)\n*## Ergebnis\n+([\s\S]*)",
                     orig.body)
        orig_desc = (m.group(1).strip() if m else orig.body.strip())
        orig_result = (m.group(2).strip() if m else "")
        body = (
            f"**Was nicht stimmt:** {description}\n\n"
            f"**Betrifft:** {orig.id} — {orig.title}\n\n"
            f"**Ursprüngliche Aufgabe:**\n{orig_desc}\n\n"
            f"**Was der Agent damals berichtet hat:**\n{orig_result or '(kein Ergebnis)'}"
        )
        return _create_locked(tickets_dir, f"Bug zu {orig.id}: {orig.title}"[:70],
                              body, "claude", orig.project, "normal", "bug",
                              "", "", "nein")


def delete_ticket(tickets_dir, ticket_id: str) -> None:
    """Remove the ticket file (WB-30). Recoverable via git history — every
    ticket change is committed, so deletion never destroys the record."""
    with _locked(tickets_dir):
        _find_path(tickets_dir, ticket_id).unlink()


def set_result(tickets_dir, ticket_id: str, result: str) -> Ticket:
    """Replace the `## Ergebnis` section, keeping `## Beschreibung` intact.
    Read and write happen under the write lock, so a user edit saved in
    between is merged (their Beschreibung survives), never overwritten."""
    with _locked(tickets_dir):
        path = _find_path(tickets_dir, ticket_id)
        t = parse_ticket(path.read_text(encoding="utf-8"))
        m = re.match(r"(## Beschreibung\n[\s\S]*?)\n*## Ergebnis\n[\s\S]*", t.body)
        head = m.group(1).rstrip() if m else t.body.rstrip()
        body = f"{head}\n\n## Ergebnis\n\n{result.strip()}\n"
        return _update_locked(tickets_dir, ticket_id, {"body": body})


PLACEHOLDER_RESULT = "_(noch offen)_"


def append_result(tickets_dir, ticket_id: str, result: str,
                  heading: str = "") -> Ticket:
    """WB-231: add to the `## Ergebnis` section instead of replacing it.

    `set_result` replaces — correct for the board form, which shows the user
    what they are overwriting, and wrong for everyone else. Two sessions and a
    dispatched run now write the same board, and a replacing write is SILENT:
    no error, no warning, the other report is simply gone. Measured 2026-08-18:
    one write deleted a peer session's 49-line review, another a 73-line
    report. Both were only noticed through `git diff`.

    Read and append happen under the SAME lock, so two writers cannot both
    read the old text and each write their own on top — the read-then-write a
    caller does by hand has exactly that race, which is why this is a store
    function and not a rule in a skill.

    An empty result (or the `_(noch offen)_` placeholder the ticket template
    carries) is replaced outright: separating a report from nothing would only
    add noise."""
    with _locked(tickets_dir):
        path = _find_path(tickets_dir, ticket_id)
        t = parse_ticket(path.read_text(encoding="utf-8"))
        m = re.match(r"(## Beschreibung\n[\s\S]*?)\n*## Ergebnis\n([\s\S]*)", t.body)
        head = m.group(1).rstrip() if m else t.body.rstrip()
        previous = (m.group(2).strip() if m else "")
        if previous == PLACEHOLDER_RESULT:
            previous = ""
        block = result.strip()
        if heading:
            block = f"## {heading.strip()}\n\n{block}"
        combined = f"{previous}\n\n{block}".strip() if previous else block
        body = f"{head}\n\n## Ergebnis\n\n{combined}\n"
        return _update_locked(tickets_dir, ticket_id, {"body": body})


def effective_queue_pos(t) -> int:
    """WB-138: how the queue sees this ticket. Explicit `queue_pos` wins;
    otherwise fall back to the ticket number so tickets that never got
    moved keep their historical order."""
    try:
        return int(t.queue_pos)
    except (TypeError, ValueError):
        return _ticket_number(t.id)


def append_review_note(tickets_dir, ticket_id: str, note: str,
                       usage: dict = None) -> "Ticket":
    """WB-140: append an adversarial-reviewer report to the ticket's body.
    A `## Review-Bot (…)` heading is prepended so multiple reviews stack;
    the existing `## Beschreibung` and `## Ergebnis` sections stay in place.
    Under the store lock, so a note is atomic against user edits.

    WB-170: when the caller passes `usage` (a dict as produced by
    `opencode._parse_review_output`), we do two things atomically:
    append a `_💰 $X.XX · N in / M out / K cache_` footer to the section
    so a reader sees this run's cost, AND add `usage["cost_usd"]` into
    the ticket's cumulative `review_cost_usd` frontmatter field so the
    board can show the total across clicks."""
    from datetime import datetime
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = note.strip()
    changes = {}
    if isinstance(usage, dict):
        cost = usage.get("cost_usd")
        parts = []
        if isinstance(cost, (int, float)):
            parts.append(f"💰 ${cost:.4f}")
        tin = int(usage.get("tokens_in") or 0)
        tout = int(usage.get("tokens_out") or 0)
        tcache = int(usage.get("tokens_cache") or 0)
        if tin or tout or tcache:
            parts.append(f"{tin} in / {tout} out / {tcache} cache")
        if parts:
            body = f"{body}\n\n_{' · '.join(parts)}_"
    section = f"\n## Review-Bot ({stamp})\n\n{body}\n"
    with _locked(tickets_dir):
        path = _find_path(tickets_dir, ticket_id)
        t = parse_ticket(path.read_text(encoding="utf-8"))
        changes["body"] = t.body.rstrip() + "\n" + section
        if isinstance(usage, dict) and isinstance(usage.get("cost_usd"), (int, float)):
            prev = 0.0
            try:
                prev = float(t.review_cost_usd) if t.review_cost_usd else 0.0
            except (TypeError, ValueError):
                prev = 0.0
            changes["review_cost_usd"] = f"{prev + float(usage['cost_usd']):.4f}"
        return _update_locked(tickets_dir, ticket_id, changes)


def move_queued_up(tickets_dir, ticket_id: str) -> "Ticket":
    """Move a queued ticket one place forward in ITS lane. The peer is the
    directly-preceding ticket with the same status, project AND priority —
    priority stays the strongest sort key, the user only reorders within it.

    Effective positions of the two tickets are swapped; queue_pos becomes
    explicit on both, so a follow-up sort by (priority, effective_queue_pos)
    honours the change. Idempotent on the top ticket."""
    with _locked(tickets_dir):
        all_ts = load_tickets(tickets_dir)
        me = next((x for x in all_ts if x.id == ticket_id), None)
        if me is None:
            raise KeyError(f"no ticket {ticket_id}")
        if me.status != "zu_bearbeiten":
            raise ValueError("Nur Tickets in „Zu bearbeiten“ lassen sich verschieben.")
        peers = [x for x in all_ts if x.status == "zu_bearbeiten"
                 and x.project == me.project and x.priority == me.priority]
        peers.sort(key=lambda x: (effective_queue_pos(x), _ticket_number(x.id)))
        idx = next(i for i, x in enumerate(peers) if x.id == ticket_id)
        if idx == 0:
            return me                                # already at the top
        above = peers[idx - 1]
        my_pos = effective_queue_pos(me)
        above_pos = effective_queue_pos(above)
        # Two explicit writes; the flock we hold serialises them against any
        # other writer, so a reader in between can see the intermediate state
        # but never a lost update.
        _update_locked(tickets_dir, above.id, {"queue_pos": str(my_pos)})
        _update_locked(tickets_dir, ticket_id, {"queue_pos": str(above_pos)})
        return next(x for x in load_tickets(tickets_dir) if x.id == ticket_id)


def queue_peers(all_tickets, t) -> list:
    """WB-203: the tickets `t` shares a queue position with — same status,
    project and priority — in the order the dispatcher will take them.
    Priority is the strongest key and stays untouched by manual ordering,
    and each project has its own worker (WB-183), so a "lane" is exactly
    this triple."""
    peers = [x for x in all_tickets if x.status == "zu_bearbeiten"
             and x.project == t.project and x.priority == t.priority]
    peers.sort(key=lambda x: (effective_queue_pos(x), _ticket_number(x.id)))
    return peers


def move_queued_to(tickets_dir, ticket_id: str, index: int) -> "Ticket":
    """WB-203: put a queued ticket at `index` inside its lane — what a drag
    and drop means. `index` is clamped into the lane, so dropping above a
    higher-priority ticket lands at the top of the OWN priority instead of
    failing (the dispatcher sorts by priority first; the board says so).

    The lane's existing effective positions are reused and handed out in the
    new order, so the lane keeps its footprint relative to every other lane
    and no renumbering leaks across projects or priorities."""
    with _locked(tickets_dir):
        all_ts = load_tickets(tickets_dir)
        me = next((x for x in all_ts if x.id == ticket_id), None)
        if me is None:
            raise KeyError(f"no ticket {ticket_id}")
        if me.status != "zu_bearbeiten":
            raise ValueError("Nur Tickets in „Zu bearbeiten“ lassen sich verschieben.")
        peers = queue_peers(all_ts, me)
        slots = [effective_queue_pos(x) for x in peers]
        order = [x for x in peers if x.id != ticket_id]
        index = max(0, min(int(index), len(order)))
        order.insert(index, me)
        for slot, x in zip(slots, order):
            if effective_queue_pos(x) != slot or not x.queue_pos:
                _update_locked(tickets_dir, x.id, {"queue_pos": str(slot)})
        return next(x for x in load_tickets(tickets_dir) if x.id == ticket_id)


def update_ticket(tickets_dir, ticket_id: str, changes: dict,
                  expected_version=None) -> Ticket:
    """Apply a partial update. Allowed keys: title, status, assignee, project,
    priority, body (+links, fork). Bumps `updated` and the write counter
    `version`, and persists to the existing file. A stale base is rejected
    with ConflictError instead of overwriting (WB-9): pass the base version
    either as `expected_version` or as a `version` key inside `changes`."""
    with _locked(tickets_dir):
        return _update_locked(tickets_dir, ticket_id, dict(changes),
                              expected_version)


def _update_locked(tickets_dir, ticket_id: str, changes: dict,
                   expected_version=None) -> Ticket:
    path = _find_path(tickets_dir, ticket_id)
    t = parse_ticket(path.read_text(encoding="utf-8"))
    expected = changes.pop("version", None)
    if expected_version is not None:
        expected = expected_version
    if expected is not None and str(expected) != t.version:
        raise ConflictError(
            "Nicht gespeichert: Das Ticket wurde inzwischen geändert "
            "(z. B. vom Agenten). Bitte kurz prüfen und erneut speichern.")
    # `gate` holds the NAME of a check, never a command: the command behind
    # the name lives in config.json, which no request can touch. That is what
    # makes it settable from the board at all — a settable COMMAND on a
    # LAN-reachable board would be remote code execution.
    allowed = {"title", "type", "status", "assignee", "project", "priority",
               "nach", "nicht_mit", "fork", "gate", "gate_gap", "review",
               "session", "handover", "handover_at", "handover_expired",
               "limit_until", "pid", "answer", "tokens_in", "tokens_out",
               "tokens_cache", "cost_usd", "duration_s", "queue_pos", "epic",
               "interactive", "claimed_at", "orphaned", "backend",
               "review_cost_usd", "body"}
    unknown = set(changes) - allowed
    if unknown:
        # WB-178: name the likely cause in German — the historical raw
        # `cannot update keys: […]` bubbled up to the user verbatim and read
        # like a stack trace. Nine out of ten times this fires because the
        # running board is older than the browser tab that sent the request
        # (a field the client already knows about is not yet in this
        # server's `allowed` set); the other time it is a typo in a script.
        names = ", ".join(sorted(unknown))
        raise ValueError(
            f"Unbekannte Felder: {names}. Meist heißt das, das laufende "
            f"Board ist älter als das Ticket-Formular — starte das Board "
            f"neu, dann kennt es die neuen Felder auch serverseitig. Sonst "
            f"prüfe die Feldnamen auf Tippfehler.")
    if "status" in changes and changes["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    for key in ("nach", "nicht_mit"):
        if key in changes:
            changes[key] = normalize_links(changes[key])
    if "priority" in changes and changes["priority"] not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    if "type" in changes and changes["type"] not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    if "fork" in changes and changes["fork"] not in FORK_VALUES:
        raise ValueError(f"fork must be one of {FORK_VALUES}")
    if "interactive" in changes and changes["interactive"] not in INTERACTIVE_VALUES:
        raise ValueError(f"interactive must be one of {INTERACTIVE_VALUES}")
    if "backend" in changes:
        val = changes["backend"] or ""
        if val not in BACKEND_VALUES:
            raise ValueError(
                f"backend muss einer von {BACKEND_VALUES} sein — 'local' oder "
                f"leer heißt lokales Modell, 'claude' route den Lauf über den "
                f"Claude-CLI und verbraucht Abo-Kontingent.")
        # WB-238: the field only makes sense for the dsh runner. Reject
        # rather than silently ignore — the user set it deliberately, we
        # owe them a clear "wrong assignee" instead of a mystery no-op.
        if val:
            eff_assignee = (changes.get("assignee")
                            or getattr(t, "assignee", "")
                            or "").strip().lower()
            if eff_assignee != "dsh":
                raise ValueError(
                    "backend gilt nur für Tickets mit „Zugewiesen an: dsh“. "
                    "Für claude/opencode-Tickets bitte das Feld leer lassen.")
    if "gate_gap" in changes:
        # WB-226: frontmatter is one line per field — collapse newlines so a
        # multi-line paste from the user does not smuggle a bogus field.
        # Same shape the answer endpoint uses (see dispatch.answer_ticket).
        changes["gate_gap"] = re.sub(r"[\r\n]+", " ",
                                     changes["gate_gap"] or "").strip()
    if "review" in changes and changes["review"] not in ("", "ja", "nein"):
        raise ValueError("review must be '', 'ja' or 'nein'")
    if "gate" in changes and not is_gate_name(changes["gate"]):
        # Belt and braces: even though the name is only ever looked up in
        # config.json, a value that cannot be a shell fragment keeps it that
        # way if a future caller ever forgets the lookup.
        raise ValueError("gate must be a plain check name (letters, digits, "
                         "space, . _ -), max 40 characters")
    for k, v in changes.items():
        setattr(t, k, v)
    t.version = str(int(t.version or "1") + 1)
    t.updated = date.today().isoformat()
    # F4 (WB-35): the id comes from the FILE, so a poisoned id could steer the
    # rename below out of tickets/. Only real ticket ids may name a file.
    if not re.fullmatch(r"WB-\d+", t.id):
        raise ValueError(f"Unzulässige Ticket-Nummer in der Datei: {t.id!r}")
    # Board edits are authoritative: a new title means a new filename.
    new_path = path.parent / f"{t.id}-{_slug(t.title)}.md"
    if new_path != path:
        path.rename(new_path)
        path = new_path
    _write_ticket_file(path, serialize_ticket(t))
    return t
