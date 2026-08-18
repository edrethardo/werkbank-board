---
name: werkbank-pull-ticket
description: Use when the user asks this session to pull or work its Werkbank ticket — "zieh dir ein Ticket", "hol dir dein Ticket", "hast du ein Ticket?", "arbeite dein Ticket ab" — find the open ticket for THIS project on the Werkbank board and work it.
version: 9
---

# Werkbank: Pull Your Ticket

## Path to the Werkbank — the ONLY line you adapt

    WERKBANK=/pfad/zur/werkbank

Every command below starts with that assignment, so the path exists exactly
once. Never write the path into a Python string: `~` is expanded by the SHELL,
never by Python (that mistake shipped once — WB-47).

Verify it before working: `ls "$WERKBANK/tickets" >/dev/null` — if that fails,
say so and stop instead of guessing.

Tickets are markdown files in `$WERKBANK/tickets/*.md`: flat `key: value`
frontmatter (`id`, `title`, `type`, `status`, `assignee`, `project`,
`priority`, …) plus body sections `## Beschreibung` and `## Ergebnis`.
Statuses: `offen → zu_bearbeiten → in_arbeit → review → fehlgeschlagen →
erledigt`. `type` is `aufgabe` or `bug`.

## 1. Find your ticket

- Candidates: `status: offen` AND `project` equal to this session's working
  directory (compare resolved real paths).
- Skip tickets whose `assignee` is not `claude` — name them, but leave them alone.
  `assignee: opencode` means a local model owns it; the board
  dispatches those. You only ever see one if it was **escalated** back to you.
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

    WERKBANK=/pfad/zur/werkbank WB=WB-42 python3 - <<'EOF'
    import os, sys
    sys.path.insert(0, os.path.join(os.environ["WERKBANK"], "src"))
    from werkbank import store
    store.claim_ticket(os.path.join(os.environ["WERKBANK"], "tickets"),
                       os.environ["WB"], os.environ["CLAUDE_CODE_SESSION_ID"])
    EOF

**Use `claim_ticket`, not a bare status write.** It sets status, your session id
AND `claimed_at` in one go, and clears a handover marker. That timestamp is what
stops the board from deciding your ticket is stranded: without it the board hands
the ticket away and, five minutes later, drops it back into „Offen" while you are
still working on it — the user sees a ticket that never moved (WB-181, measured:
back in Offen after 200 s).

**Claim when you START, not when you notice.** The claim is a promise to the
user: the card turns "In Arbeit" and the board stops treating the ticket as
available. If you claim and then finish something else first, the board shows
work that is not happening — measured 2026-08-17 on WB-203, where a claimed
ticket sat untouched for minutes while the session completed another one. The
user saw it and did not believe the board, which is the correct reaction and a
bug in how we behave, not in the board. If you must hold a handover before you
can start (the marker expires), say so in the chat in one line, and start
immediately after. Since WB-204 the card counts the minutes since `claimed_at`
and calls out a claim that stands too long, so this now shows.

- Werkbank-Ticket (project = the Werkbank checkout)? Follow the project-local
  skill `werkbank-work-ticket` (in `$WERKBANK/.claude/skills/`) — it is the
  single source of truth for the workflow (clarity gate, work, verify,
  journal/doc duty, honest Ergebnis, bug discipline). Everything below in
  this skill only applies when the Werkbank skill is NOT reachable, e.g. for
  tickets targeting OTHER projects.
- Other project → work inside THIS project under its own rules
  (CLAUDE.md, commit discipline).
- `type: bug` (any project): FIRST reproduce the bug (prove the cause, no
  guessing), THEN fix the cause (not the symptom), THEN add a regression test
  that fails without the fix and passes with it. Evidence goes into `## Ergebnis`.
- Never set `erledigt` — that column belongs to the user.

## 3b. Checked tickets — `gate:` names a check, it is NOT a command

A ticket may name a check that decides whether the work counts:

    gate: Tests laufen durch

**The name is only a name.** The command behind it lives in the board's
`config.json` under `gates` -> `<project path>` -> `<name>`. Look it up there and
run THAT; never invent a command because the name sounds like one. If the name is
not in `config.json`, the board would refuse to dispatch it — say so instead of
guessing.

    "gates": { "/pfad/zur/werkbank": { "Tests laufen durch": "python3 -m pytest tests/ -q" } }

Why the indirection: the board is reachable from the LAN, and a ticket is an
executable prompt already. If a ticket field could carry a command, any request
that edits a ticket would be remote code execution. Names cross the network,
commands never do.

**If a check is named, it decides whether you are finished — not your judgement.**
Run it in the ticket's `project` directory and paste its REAL output into
`## Ergebnis`. Never write "Tests laufen" without the output that proves it.

Choosing the check matters more than running it: `npm run compile` type-checks
WITHOUT running tests, so code can pass it while every test fails. If the
configured check does not actually exercise the Akzeptanzkriterien, say so in the
result rather than leaning on a green that means little.

## 3c. Escalated tickets — read the two attempts BEFORE you start

A ticket whose `assignee` changed from `opencode` to `claude` was escalated: the local
model tried twice and the gate stayed red. Its `## Ergebnis` already contains BOTH
attempts and the failing gate output.

Read that first. It tells you what was already tried, and the gate output is usually the
actual root cause. Starting from scratch wastes the work the GPU already did — the point
of escalation is that the expensive model gets a head start, not a blank page.

Do not hand an escalated ticket back to `opencode`. It failed there twice; a third
attempt is not new information.

## 4. Report back

Write a short, honest German summary — what was done, what was verified, what
failed or stayed open — and move the ticket to review.

**Use `append_result`, never `set_result`, on a ticket that may already carry
somebody else's text (WB-231).** `set_result` REPLACES the whole `## Ergebnis`
section — silently: no merge, no version check, no warning. Measured 2026-08-18:
one write deleted a peer session's 49-line review, another a 73-line report;
both were found only through `git diff`. `append_result` reads and writes under
the SAME store lock, so two sessions finishing at once cannot lose each other's
report — a hand-rolled read-then-append has exactly that race. An empty result
(or the `_(noch offen)_` placeholder) is replaced, not decorated.

    WERKBANK=/pfad/zur/werkbank WB=WB-42 \
    ERGEBNIS="Kurz, ehrlich, auf Deutsch." python3 - <<'EOF'
    import os, sys
    sys.path.insert(0, os.path.join(os.environ["WERKBANK"], "src"))
    from werkbank import dispatch, store
    tickets = os.path.join(os.environ["WERKBANK"], "tickets")
    store.append_result(tickets, os.environ["WB"], os.environ["ERGEBNIS"])
    store.update_ticket(tickets, os.environ["WB"], {"status": "review"})
    # Register THIS session so the next board drag reaches this chat (WB-19/22).
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        dispatch.register_ticket_session(os.environ["PWD"], sid)
    EOF

If `$CLAUDE_CODE_SESSION_ID` is empty, skip the registration and say so — never
guess an id. Then commit the ticket file in the Werkbank repo (do not push):

    git -C "$WERKBANK" add tickets/
    git -C "$WERKBANK" commit -m "Work tickets: <id> (pulled by <project name>)"

## 5. Handover watcher — START IT WHEN YOU CLAIM, NOT WHEN YOU FINISH

**Only in an INTERACTIVE chat session — never in a dispatched background run,
which must leave no background job behind at all.** Measured 2026-08-16
(WB-77): a background run copied this rule from a resumed chat transcript and
left the watcher looping. It had already delivered its result, but the watcher
inherited the run's output pipe, so the board could not see the run had
finished: 19 minutes of "in Arbeit", the queue stalled behind it, and the
30-minute watchdog would have filed the SUCCESSFUL run as failed.

**Rule:** restarting the watcher is the FIRST action after claiming a ticket.
Doing it "at the end" fails whenever the end is interrupted — the next handover
then sits unnoticed until the board's fallback picks it up, and the user has to
ask why nothing happens (this happened three times in this project).

Start it (Bash, run_in_background):

    WERKBANK=/pfad/zur/werkbank
    end=$((SECONDS+7200)); while [ $SECONDS -lt $end ]; do
      f=$(grep -l "^handover: $CLAUDE_CODE_SESSION_ID" "$WERKBANK"/tickets/*.md 2>/dev/null | head -1)
      [ -n "$f" ] && { echo "HANDOVER:$f"; exit 0; }; sleep 5
    done; echo TIMEOUT; exit 1

When it exits with HANDOVER:<file>, CLAIM IMMEDIATELY (deadline ~5 min, then a
background run takes over):

    WERKBANK=/pfad/zur/werkbank WB=WB-42 python3 - <<'EOF'
    import os, sys
    sys.path.insert(0, os.path.join(os.environ["WERKBANK"], "src"))
    from werkbank import store
    store.update_ticket(os.path.join(os.environ["WERKBANK"], "tickets"),
                        os.environ["WB"],
                        {"handover": "", "handover_at": "",
                         "session": os.environ["CLAUDE_CODE_SESSION_ID"]})
    EOF

Claim within `chat_handover_minutes` (default 5, counted from the marker's own
timestamp — it survives board restarts), then RESTART THE WATCHER, then tell the
user the ticket arrived and work it visibly per steps 2–4.
