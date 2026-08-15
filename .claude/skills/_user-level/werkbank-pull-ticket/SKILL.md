---
name: werkbank-pull-ticket
description: Use when the user asks this session to pull or work its Werkbank ticket — "zieh dir ein Ticket", "hol dir dein Ticket", "hast du ein Ticket?", "arbeite dein Ticket ab" — find the open ticket for THIS project on the Werkbank board and work it.
version: 4
---

# Werkbank: Pull Your Ticket

Werkbank tickets live in `~/code/werkbank/tickets/*.md` (verify the
path exists first; the repo may have moved). One file per ticket: flat `key: value`
frontmatter (`id`, `title`, `type`, `status`, `assignee`, `project`, `priority`,
`created`, `updated`) plus body sections `## Beschreibung` and `## Ergebnis`.
Statuses: `offen → in_arbeit → review → erledigt`. `type` is `aufgabe` or `bug`;
older files without the field count as `aufgabe`. Edit tickets with plain file
tools.

## 1. Find your ticket

- Candidates: `status: offen` AND `project` equal to this session's working
  directory (compare resolved real paths).
- Skip tickets whose `assignee` is not `claude` — name them, but leave them alone.
- Order: priority `hoch` > `normal` > `niedrig`, then lowest WB number. Take the
  first; mention any others.
- No match → tell the user plainly ("kein offenes Ticket für dieses Projekt") and stop.

## 2. The clarity gate — ask BEFORE you claim

Read the chosen ticket fully. Ask the user first if ANY of these hold:

- the description is empty, vague, or allows more than one reading
- it conflicts with this project's rules, or looks destructive / hard to reverse
- you cannot say what "fertig" concretely looks like

Interactive session: ask directly, one question per message, in the user's language.
Non-interactive run (nobody can answer): do NOT guess — write your questions into
`## Ergebnis`, set `status: review`, and stop. The board's "Ablehnen mit Grund"
returns the ticket to `offen` with the user's answer recorded in the description.

## 3. Claim and work

- Set `status: in_arbeit` and `updated: <today>` in the ticket file, then start.
- Work inside THIS project under its own rules (CLAUDE.md, commit discipline).
- `type: bug` → debugging discipline is mandatory: FIRST reproduce the bug (prove
  the cause, no guessing), THEN fix the cause (not the symptom), THEN add a
  regression test that fails without the fix and passes with it. The evidence
  (how reproduced, which test) belongs in `## Ergebnis`.
- Never set `erledigt` — that column belongs to the user.

## 4. Report back

- Write a short, honest German summary into `## Ergebnis`: what was done, what was
  verified, what failed or stayed open. Failures go here too — a failed ticket
  sitting silently in `in_arbeit` is a bug. Set `status: review`, bump `updated`.
- Register THIS session as the project's last ticket session (WB-19): the next
  board drag then HANDS the ticket to this chat visibly (WB-22); if this session
  doesn't claim in time, a forked background run takes over — an open
  conversation is never written into directly. The id comes ONLY from the
  environment; if the variable is empty, skip registration and say so — never
  guess an id:

      sid="$CLAUDE_CODE_SESSION_ID"
      [ -n "$sid" ] && python3 -c "import os, sys; sys.path.insert(0, '~/code/werkbank/src'); from werkbank import dispatch; dispatch.register_ticket_session(os.environ['PWD'], os.environ['CLAUDE_CODE_SESSION_ID'])"

- Commit the ticket-file change in the Werkbank repo (do not push; the Werkbank's
  own session pushes):

      git -C ~/code/werkbank add tickets/
      git -C ~/code/werkbank commit -m "Work tickets: <id> (pulled by <project name>)"

## 5. Handover watcher (WB-22) — keep running after registering

Start this watcher (Bash, run_in_background) so dragged tickets reach this chat
visibly; restart it after each handled handover or timeout:

    end=$((SECONDS+7200)); while [ $SECONDS -lt $end ]; do
      f=$(grep -l "^handover: $CLAUDE_CODE_SESSION_ID" ~/code/werkbank/tickets/*.md 2>/dev/null | head -1)
      [ -n "$f" ] && { echo "HANDOVER:$f"; exit 0; }; sleep 5
    done; echo TIMEOUT; exit 1

When it exits with HANDOVER:<file>: CLAIM IMMEDIATELY (deadline ~5 min, then a
background run takes over) by updating the ticket via the store:

    python3 -c "import sys; sys.path.insert(0, '~/code/werkbank/src'); from werkbank import store; import os; store.update_ticket('~/code/werkbank/tickets', '<WB-n>', {'handover': '', 'session': os.environ['CLAUDE_CODE_SESSION_ID']})"

Then tell the user the ticket arrived and work it visibly per steps 2–4.
