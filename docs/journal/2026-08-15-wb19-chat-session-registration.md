---
title: WB-19 fixed — chat ticket work registers as last ticket session
date: 2026-08-15
tags: [bugfix, skill]
summary: state.json entries can now be marked interactive; chat flows register via $CLAUDE_CODE_SESSION_ID (proven safe empirically), and interactive lineages are always resumed as a fork; 69 tests green, skills staged, restart pending.
outcome: done
---

# WB-19 fixed — chat ticket work registers as last ticket session

## What was asked

Bug ticket WB-19: chat sessions that work tickets (work-tickets / pull flows)
never register in state.json, so the next board drag resumed the old board-run
lineage instead of the session that actually worked the last ticket.

## How the bug was reproduced / the cause proven

- `grep -rn save_last_session` over src/ and skills: the only caller is
  `dispatch.run_claude` (board runs). Neither work-tickets nor the pull skill
  mention state.json at all — chat work could not register anywhere. That IS
  the reported behavior; no guessing needed.
- Regression tests written first, red before the fix (7 errors:
  `register_ticket_session` / `load_last_entry` did not exist, `build_command`
  had no force_fork), green after.

## The session-id mechanism (criterion: clarify, don't guess)

`$CLAUDE_CODE_SESSION_ID` is the safe source, proven empirically: this run's
own env var value was byte-identical to the id the dispatcher had written to
state.json from the CLI's `--output-format json` `session_id` field. (Note:
`$CLAUDE_SESSION_ID` — without CODE — is empty; the skills name the exact
variable.) Rule enforced in code: `register_ticket_session` raises on an empty
id, so callers skip registration instead of guessing.

## What I did

- dispatch.py: state entries are now either legacy plain strings
  (= non-interactive, stays valid) or `{"id":…, "interactive": true}`.
  `load_last_entry` normalizes both; `load_last_session` keeps its old
  signature for existing callers/tests. New `register_ticket_session(project,
  id)` marks interactive. `build_command(..., force_fork=)`: an interactive
  lineage is ALWAYS resumed with `--fork-session`, whatever the ⑂ checkbox
  says (two-writers problem — an open conversation must never be written
  into); board lineages keep checkbox behavior. run_claude passes the flag and
  keeps saving board results as non-interactive (a fork of a chat lineage
  becomes a normal board lineage).
- Skills (staged in staged-skills/, .claude/ is write-protected for dispatched
  runs): work-tickets v3→v4 and werkbank-pull-ticket v2→v3 gain an "After the
  batch"/"Report back" registration step with the exact shell line and the
  never-guess rule.
- Tests: 8 new (registration roundtrip, empty-id rejection, legacy-string
  normalization, board-save stays non-interactive, forced-fork command,
  checkbox-decides command, end-to-end fake-claude run proving
  `--resume <chat-id> --fork-session` and non-interactive re-remembering).
  Suite: 69 green.
- CHANGELOG + docs/user paragraph updated.
- Deliberately did NOT register THIS run: it is a board-dispatched
  (non-interactive) run; the dispatcher records it itself.

## What I tried that didn't work — and why

Nothing failed. (First Write to staged-skills/work-tickets/SKILL.md was
rejected read-before-write — a WB-13 staged v3 already existed there; edited
that one instead, which also carried the bug-discipline additions forward.)

## Decisions made

- Non-interactive entries keep the legacy plain-string form in state.json —
  maximum backward compatibility, only interactive entries use the dict form.
- Forced fork implemented in build_command (not in the skills): the safety
  property must hold even if a skill forgets it.

## Follow-ups

- Board restart needed (dispatch.py changed) — requested in the Ergebnis.
- Werkbank session: install staged skills per staged-skills/README.md
  (work-tickets v4 → .claude/skills/, werkbank-pull-ticket v3 → _user-level +
  ~/.claude/skills/).
