---
name: work-tickets
description: Use when the user asks to work the tickets ("arbeite die Tickets ab", "erledige Ticket WB-3", "was steht an?"), and when a handover marker wakes the watcher — dispatch or visibly work tickets and report results.
version: 7
---

> Note: dragging a ticket offen → in_arbeit in the BOARD dispatches it
> automatically (see `src/werkbank/dispatch.py`): normally a claude run resuming
> the remembered ticket session (forked per checkbox/safety rules; bug tickets get
> the debugging-discipline block injected). If the remembered lineage is a LIVE
> chat session, the ticket is HANDED OVER to it instead (WB-22, see watcher
> below). The flow below is the CHAT path; every path must end in `review` with an
> honest `## Ergebnis`.

# Work Tickets

Tickets are markdown files in `tickets/` (format: see `src/werkbank/store.py` — flat
frontmatter, body has `## Beschreibung` and `## Ergebnis`). The files are the source
of truth; edit them with file tools. Statuses: `offen → in_arbeit → review →
erledigt`. Agents NEVER set `erledigt` — that column belongs to the user.
Tickets have a `type`: `aufgabe` (default) or `bug`; older files without the
field count as `aufgabe`.

## "Was steht an?"

Read `tickets/*.md`, summarize per status in plain German (id, title, assignee,
priority). No dispatch.

## Dispatch ("arbeite die Tickets ab" / "erledige WB-n") — one source of truth

For each targeted ticket with status `offen`, ordered `hoch` > `normal` > `niedrig`:

1. Set `status: in_arbeit` and `updated: <today>` in the ticket file.
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

## Handover watcher — START IT WHEN YOU CLAIM, NOT WHEN YOU FINISH

**Only in an INTERACTIVE chat session. A dispatched background run must never
start it** — and must leave no background job of any kind behind. Measured
2026-08-16 (WB-77): a background run resumed a chat session's transcript,
copied this rule, and left the watcher looping. The run had already delivered
its result, but the watcher inherited its output pipe, so the board could not
tell it was finished: 19 minutes of "in Arbeit", the whole queue stalled behind
it, and at the 30-minute watchdog the SUCCESSFUL run would have been recorded
as failed. Waiting for a handover is meaningless in a run that IS the handover.

**Rule (learned the hard way, three times):** the FIRST action after claiming a
ticket is restarting the watcher. Doing it "at the end" fails every time
something interrupts the end — the next handover then sits unnoticed until the
board's fallback picks it up minutes later, and the user has to ask why nothing
is happening.

Start it (Bash, run_in_background) — immediately after the claim:

    end=$((SECONDS+7200)); while [ $SECONDS -lt $end ]; do
      f=$(grep -l "^handover: $CLAUDE_CODE_SESSION_ID" tickets/*.md 2>/dev/null | head -1)
      [ -n "$f" ] && { echo "HANDOVER:$f"; exit 0; }; sleep 5
    done; echo TIMEOUT; exit 1

When it exits with HANDOVER:<file> (the harness wakes you):

1. **Claim** — `{"handover": "", "handover_at": "", "session": "$CLAUDE_CODE_SESSION_ID"}`.
   The deadline is `chat_handover_minutes` (default 5) counted from the marker's
   own timestamp, so it survives board restarts (WB-66); afterwards a forked
   background run takes over.
2. **Restart the watcher right away** (see the rule above).
3. Tell the user the ticket arrived, work it VISIBLY here, finalize (review),
   re-register.

On TIMEOUT just start it again. A dead watcher is not data loss — handovers
fall back to background runs — but it makes the board look stuck.

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

- Then start (or restart) the handover watcher above.
- If the project worked on was THIS repo: normal git discipline applies (commit the
  ticket-file changes together with the work).
- Otherwise: commit the ticket-file changes here with subject
  `Work tickets: <ids>` and leave the target project's commits to its own rules.
- Report to the user per ticket: one line, what happened, where to look.
