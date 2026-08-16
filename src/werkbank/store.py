"""Ticket storage: one markdown file per ticket with flat key: value frontmatter.

The files are the source of truth — the board and the agents both read and write
through this module, but a hand-edited ticket file is equally valid.
"""

import os
import re
import tempfile
import threading
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
STATUSES = ["offen", "zu_bearbeiten", "in_arbeit", "review", "fehlgeschlagen",
            "erledigt"]
PRIORITIES = ["hoch", "normal", "niedrig"]
TYPES = ["aufgabe", "bug"]

# Frontmatter keys, in the order they are written to disk. Older ticket files
# may lack `type`, `nach` or `nicht_mit`; parsing falls back to the dataclass
# defaults. `nach`/`nicht_mit` are comma lists of ticket ids (WB-12).
KEYS = ["id", "title", "type", "status", "assignee", "project", "priority",
        "nach", "nicht_mit", "fork", "gate", "review", "version", "session",
        "handover", "handover_at", "handover_expired", "limit_until", "pid",
        "created", "updated"]
FORK_VALUES = ["ja", "nein"]
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
    review: str = ""      # "nein" skips the paid diff review after a green gate
    version: str = "1"    # write counter for lost-update detection (WB-9)
    session: str = ""     # session id of the run that worked this ticket (WB-20)
    handover: str = ""    # session id a dragged ticket was handed to (WB-22)
    handover_at: str = "" # unix time the handover was set — survives restarts (WB-66)
    handover_expired: str = ""  # "ja" once a handover went unclaimed (WB-68)
    limit_until: str = ""       # unix time the run pauses on quota (WB-69)
    pid: str = ""               # OS pid of the live claude process — kills orphans (WB-75)
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
    "review": "in Review, noch nicht abgenommen",
    "fehlgeschlagen": "fehlgeschlagen", "erledigt": "erledigt",
}


def blocking_reasons(all_tickets, t, include_exclusion: bool = True) -> list:
    """German reasons why `t` may not start now. References to unknown ids never
    block. Exclusion can be skipped: the dispatcher runs strictly one at a time,
    so at run time only the `nach` order can still be violated."""
    by_id = {x.id: x for x in all_tickets}
    reasons = []
    for lid in link_ids(t.nach):
        other = by_id.get(lid)
        if other and other.status != "erledigt":
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


def load_tickets_with_errors(tickets_dir):
    """A broken file must only affect itself (WB-8): readable tickets are
    returned normally, unreadable files land in the error list."""
    tickets, errors = [], []
    for p in _paths(tickets_dir):
        try:
            tickets.append(parse_ticket(p.read_text(encoding="utf-8")))
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
                  fork: str = "nein", gate: str = "") -> Ticket:
    tickets_dir = Path(tickets_dir)
    tickets_dir.mkdir(parents=True, exist_ok=True)
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    if type not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    nach, nicht_mit = normalize_links(nach), normalize_links(nicht_mit)
    if fork not in FORK_VALUES:
        raise ValueError(f"fork must be one of {FORK_VALUES}")
    if not is_gate_name(gate):
        raise ValueError("gate must be a plain check name")
    with _locked(tickets_dir):
        return _create_locked(tickets_dir, title, description, assignee, project,
                              priority, type, nach, nicht_mit, fork, gate)


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
                   type, nach, nicht_mit, fork, gate="") -> Ticket:
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
               "nach", "nicht_mit", "fork", "gate", "review", "session",
               "handover", "handover_at", "handover_expired", "limit_until",
               "pid", "body"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"cannot update keys: {sorted(unknown)}")
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
