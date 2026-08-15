---
name: work-tickets
description: Use when the user asks to work the tickets ("arbeite die Tickets ab", "erledige Ticket WB-3", "was steht an?"), and when a handover marker wakes the watcher — dispatch or visibly work tickets and report results.
version: 5
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

## Dispatch ("arbeite die Tickets ab" / "erledige WB-n")

For each targeted ticket with status `offen` (all of them, or the named one), ordered
`hoch` before `normal` before `niedrig`:

1. Set `status: in_arbeit` and `updated: <today>` in the ticket file.
2. Spawn a subagent (Agent tool, general-purpose; run in background so tickets run in
   parallel; if the assignee is `opencode`, follow the `coding-with-opencode` skill
   instead). Prompt must contain:
   - the full ticket body and title,
   - the target: work INSIDE the ticket's `project` directory,
   - that project's rules apply (its CLAUDE.md, commit discipline),
   - report back: what was done, what was verified, what failed — as raw facts,
   - for `type: bug` additionally, debugging discipline: FIRST reproduce the bug
     (prove the cause, no guessing), THEN fix the cause (not the symptom), THEN add
     a regression test that fails without the fix and passes with it. The evidence
     (how it was reproduced, which test) must be part of the report — it goes into
     `## Ergebnis`.
3. When the agent returns: write its outcome into the ticket's `## Ergebnis` section
   (short, German, honest — including failures), set `status: review`,
   `updated: <today>`.
4. If the agent failed or was blocked: still `review`, with the blocker spelled out
   in `## Ergebnis` — a failed ticket silently sitting in `in_arbeit` is a bug.

Working a ticket YOURSELF in this chat ("zieh dir WB-n", handover arrivals) follows
the same steps minus the subagent: claim, work visibly, honest Ergebnis, review.

## Handover watcher (WB-22) — keep running while this session is the ticket lineage

After registering (below), start the watcher so dragged tickets reach this chat
visibly (Bash, run_in_background):

    end=$((SECONDS+7200)); while [ $SECONDS -lt $end ]; do
      f=$(grep -l "^handover: $CLAUDE_CODE_SESSION_ID" tickets/*.md 2>/dev/null | head -1)
      [ -n "$f" ] && { echo "HANDOVER:$f"; exit 0; }; sleep 5
    done; echo TIMEOUT; exit 1

When it exits with HANDOVER:<file> (the harness wakes you): CLAIM IMMEDIATELY —
update the ticket with `{"handover": "", "session": "$CLAUDE_CODE_SESSION_ID"}` —
the claim deadline is ~5 min (config `chat_handover_minutes`); afterwards a forked
background run takes over. Then tell the user the ticket arrived, work it VISIBLY
here, finalize (review), re-register, and START THE WATCHER AGAIN. On TIMEOUT
restart it when convenient; a dead watcher only means handovers fall back to
background runs.

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
