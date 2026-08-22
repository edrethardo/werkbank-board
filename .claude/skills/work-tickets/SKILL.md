---
name: work-tickets
description: Use when the user asks to work the tickets ("arbeite die Tickets ab", "erledige Ticket WB-3", "was steht an?"), and when the board hands a ticket to this chat — dispatch or visibly work tickets and report results.
version: 7
---

> Note: dragging a ticket offen → in_arbeit in the BOARD dispatches it
> automatically (see `src/werkbank/dispatch.py`): normally a claude run resuming
> the remembered ticket session (forked per checkbox/safety rules; bug tickets get
> the debugging-discipline block injected). If the remembered lineage is a LIVE
> chat session, the ticket is HANDED OVER to it — delivered straight into
> that conversation since WB-258; see the last section. The flow below is the CHAT path; every path must end in `review` with an
> honest `## Ergebnis`.

# Work Tickets

Tickets are markdown files in `tickets/` (format: see `src/werkbank/store.py` — flat
frontmatter, body has `## Beschreibung` and `## Ergebnis`). The files are the source
of truth; edit them with file tools. Statuses: `offen → in_arbeit → review →
erledigt`. Agents NEVER set `erledigt` — that column belongs to the user.
Tickets have a `type`: `aufgabe` (default), `bug` or `epic` (WB-161); older files without the
field count as `aufgabe`.

## "Was steht an?"

Read `tickets/*.md`, summarize per status in plain German (id, title, assignee,
priority). No dispatch.

## Dispatch ("arbeite die Tickets ab" / "erledige WB-n") — one source of truth

For each targeted ticket with status `offen`, ordered `hoch` > `normal` > `niedrig`:

1. Claim it with `store.claim_ticket(tickets_dir, id, session_id)` — status,
   session and `claimed_at` in one call. A bare status write makes the board
   treat the ticket as stranded and take it back mid-work (WB-181).
   Claim when you START. A claim is what turns the card "In Arbeit"; claiming
   and then finishing something else first makes the board show work that is
   not happening (WB-203, 2026-08-17 — the user noticed and stopped believing
   the board). Since WB-204 the card counts the minutes and says so.
2. Spawn a subagent (Agent tool, general-purpose; run in background for
   parallelism). A ticket with `assignee: opencode` is NOT yours to spawn —
   the board dispatches it to the local model itself.
   Its prompt: the full ticket body and title, the target project, and one
   instruction — follow `werkbank-work-ticket` (this repo's `.claude/skills/`).
   That skill IS the workflow (clarity gate, work, verify, journal/doc duty,
   honest Ergebnis, bug discipline). Do NOT re-list its rules here — WB-70
   caught the drift that killed us three times.
3. When the agent returns, take its final text as the Ergebnis, set
   `status: review`. Failures land in `review` too, with the blocker in the
   text; a failure silently sitting in `in_arbeit` is a bug.

Working a ticket YOURSELF in this chat (chat request, handover arrivals):
follow `werkbank-work-ticket` — the same workflow, without spawning.

## Handovers arrive by themselves — no watcher

Since WB-258 the board delivers a dragged ticket straight into the registered
chat session over its messaging socket. There is nothing to poll.

**Do not start a watcher loop.** The old one cost this project twice: a
background run copied it out of a resumed transcript and left it holding the
run's output pipe (19 minutes of a stalled queue behind a run that had already
finished, WB-77), and a later version looked started while it had already
exited, because its deadline came from a per-shell counter that was still zero
(WB-259). Both disappear with the loop.

What makes a chat reachable is the REGISTRATION: `state.json` must hold this
session's id with `"interactive": true` for the ticket's project. The
`werkbank-register-session` skill does that deliberately; finishing a ticket
does it as a side effect. If a handover does not arrive, check that — do not
reintroduce a poller.

## Traps this project has actually hit

- **Scripted edits that silently do not match.** A `str.replace` in a patch
  script fails quietly and you believe the change landed. ALWAYS assert the
  pattern was found (`assert old in s`) or verify the result afterwards —
  three separate incidents (WB-43, WB-48, WB-57).
- **Never restart the board from a dispatched run** (WB-17): the dispatcher
  dies with it and the ticket is left stranded.
- **Chat-side writes do not touch the API**, so nothing pumps the queue by
  itself except the 15 s ticker (WB-59). If you finalize by hand, do not assume
  the next ticket starts instantly.
- **Verify a "fresh install" claim against a truly fresh copy** — a port the
  running board already owns will answer for it (WB-46).

## After the batch

- Register THIS session as the project's last ticket session (WB-19), so the next
  board drag reaches this chat (handover; unclaimed handovers fall back to a
  forked background run — an open conversation is never written into directly).
  The id comes ONLY from the environment; if the variable is empty, skip
  registration and say so (never guess an id):

      sid="$CLAUDE_CODE_SESSION_ID"
      [ -n "$sid" ] && python3 -c "import os, sys; sys.path.insert(0, 'src'); from werkbank import dispatch; dispatch.register_ticket_session('<ticket project path>', '$sid')"

- Nothing else to start: the board delivers the next handover into this
  conversation by itself (WB-258).
- If the project worked on was THIS repo: normal git discipline applies (commit the
  ticket-file changes together with the work).
- Otherwise: commit the ticket-file changes here with subject
  `Work tickets: <ids>` and leave the target project's commits to its own rules.
- Report to the user per ticket: one line, what happened, where to look.
