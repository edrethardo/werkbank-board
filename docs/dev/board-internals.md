---
title: Board internals — the details the README stops mentioning
date: 2026-08-16
tags: [decision, docs]
summary: WB-51 trimmed the README's feature list from nine dense bullets to four; this doc keeps the details that got pulled out, grouped so a reader who wants to know "and how does that actually work?" has one place to go.
---

# Board internals

The README lists four things the board does; this doc keeps the details
that used to hang off those bullets and the small design decisions behind
them. Nothing here is required reading to use the tool — that's the whole
point of moving it out of the README.

## Kanban UI details

- Single vanilla-JS HTML page (`src/werkbank/board.html`), no build step,
  no bundler.
- Dark/light theme toggle, persisted per browser via `localStorage`.
- Project filter in the header (`All / <name>`); the selection sticks
  across reloads. Setting the filter to a specific project also
  pre-selects it for "+ Neues Ticket".
- Delete button in the ticket detail dialog (with a confirmation). Deleted
  tickets stay recoverable through git — the file was in `tickets/` and
  every ticket file lives its whole life under version control if you
  commit the folder.
- All ticket-editing dialogs are resizable from the bottom-right corner.
- On phones/tablets the seven columns collapse to one list plus status
  chips at the top; swipe horizontally to change columns
  (`SWIPE_MIN = 40`, `SWIPE_RATIO = 1.5` after WB-68 round two).

## Queue mechanics

- The self-driving queue is a strict FIFO ordered by priority (`hoch >
  normal > niedrig`), then by ticket number. Exactly one `claude -p`
  process runs at any moment across the whole board (agent runs must be
  serialized — parallel `claude -p` corrupts `~/.claude/claude.json`, see
  Anthropic issues #29051 / #28813).
- **Per-project switch**: `nonblocking_review` in `config.json` (per
  project name) lets that project's queue keep advancing while a ticket
  waits in Review. Default: OFF (a pending review blocks the project's
  queue), because a stale un-reviewed ticket usually means the last
  attempt was wrong.
- Other projects never block your queue — the "one run at a time" rule is
  enforced by the dispatcher itself, not by the per-project reason list.
- A ticker sweeps the queue every 15 s: without it, chat-side finalizes
  (which write straight through the store, never touching the HTTP API)
  would leave the queue holding an eligible ticket forever (WB-59).

## Review + failure loop

- Agents stop at Review with an honest result — including failure text
  and log path. Only the human moves tickets to Erledigt (this is
  enforced everywhere in the code; the "erledigt" column is the user's).
- Reject with a reason: the ticket returns to Offen with your reason
  appended to the description.
- Technical failures land in a separate *Fehlgeschlagen* column with a
  red header so they never look like something to accept by mistake.
  Each card has a **Erneut versuchen** button that re-dispatches.
- **Bug tickets** carry a debugging discipline into the dispatch prompt
  (reproduce → fix cause → regression test); a bug ticket that stays
  silently in `in_arbeit` after a run is itself a bug.

## Robustness / concurrency

- **Lost-update protection** — every ticket carries a `version` counter;
  the board rejects a save based on a stale version instead of
  overwriting silently.
- **Cross-process file locks** — `flock` on Unix, `msvcrt.locking` on
  Windows, wrapping every read-modify-write cycle. Both the server and
  chat sessions write through the same `store.py`, so the lock actually
  matters.
- **Atomic ticket writes** — `tempfile.mkstemp` + `os.replace`, so a
  reader never sees a half-written file (WB-32; earlier `write_text`
  truncated first and the reader briefly flagged the ticket as broken).
- **Orphan sweep** at startup — a ticket left in `in_arbeit` after a
  board restart is surfaced as `fehlgeschlagen` (WB-17); if the
  ex-worker's `claude` process is still alive and its command line
  matches the ticket, it is killed as part of the sweep (WB-75).
- **Broken ticket quarantine** — one unreadable file no longer takes the
  whole board down; readable tickets show normally and the broken file
  is called out at the top with its parser error.
- **Chat handover** — if the last worker for a project is a live chat
  session, a dispatched ticket is handed over there and worked visibly
  for you; the deadline for the chat to claim (default 5 min) lives in
  the ticket file so it survives board restarts (WB-66). The watcher
  itself is a `bash` loop and is therefore Unix-only; on Windows the
  fallback background run kicks in without a claim attempt.

## Multi-project + skills

- Named project list (`projects` in `config.json`, name → absolute path)
  shown in the dialogs. A folder picker lets you register more from the
  UI.
- Each ticket may name its own `project`. Without one, the board falls
  back to `default_project`.
- **Other projects' chat sessions** pull their own tickets via the
  user-level skill `werkbank-pull-ticket` (installed manually per the
  README, or interactively by typing `init` in the Werkbank chat).
- Bug reports from other projects use the sibling skill
  `werkbank-report-bug`.

## Ticket links

- **`nach: WB-1, WB-2`** — must-wait-for ordering. Checked both at
  drag time and when the queue reaches this ticket.
- **`nicht_mit: WB-4`** — mutual exclusion. Symmetric: if A lists B, B
  cannot run while A does either.
- References to unknown ticket ids never block (so a deleted ticket
  doesn't strand its followers).
