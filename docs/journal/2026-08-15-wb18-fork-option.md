---
title: WB-18 — fork on dispatch is now a per-ticket checkbox, default off
date: 2026-08-15
tags: [feature]
summary: New fork field (default nein) — the remembered ticket session now grows in place by default; a board checkbox (⑂) opts into forking; without a remembered session the dispatcher always forks; 52 tests green, restart pending.
outcome: done
---

# WB-18 — fork on dispatch is now a per-ticket checkbox, default off

## What was asked

Ticket WB-18 (board-dispatched run): make forking optional per ticket, default
NOT forking so the ticket session grows as one continuous conversation; board
checkbox with ⑂ + tooltip; safety rule: no remembered ticket session → always
fork; tests for all command combinations.

## What I did

- store.py: new frontmatter key `fork` (`ja`/`nein`, default `nein`, validated
  on create and update; legacy files without the field count as nein). SAMPLE
  fixture and legacy-parse test updated.
- dispatch.py `build_command`: resume mode appends `--fork-session` only when
  the ticket says `fork: ja`; continue mode ALWAYS forks (safety rule confirmed
  as intended — without a remembered ticket session, --continue would resume an
  arbitrary latest conversation such as the user's live chat and, unforked,
  permanently grow it); fresh mode unchanged. Module docstring updated.
- server.py: create passes `fork` through.
- board.html: "⑂ Auf Abzweigung arbeiten" checkbox in create and detail dialogs
  with a plain-German tooltip covering both states and the safety rule;
  checkbox maps to ja/nein explicitly on submit (checkboxes are absent from
  FormData when unchecked). CSS keeps the checkbox from inheriting width:100%.
- Tests first (red: 2 failures/4 errors → green): fork roundtrip/default/
  validation and legacy default in test_store; command combos in test_dispatch —
  resume+nein (no fork flag), resume+ja (fork flag), continue×{ja,nein} (always
  forks), fresh unchanged. Suite: 52 tests green; both script blocks pass
  `node --check`.

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- German `ja`/`nein` values (not true/false): ticket files are user-facing, and
  flat strings keep the frontmatter format dumb and greppable.
- save_last_session stays unconditional on success: for unforked resumes the CLI
  returns the same session id, so remembering it is a no-op; for forked runs it
  keeps remembering the newest fork (existing WB-14 behavior).

## Follow-ups

- Board restart needed to activate (server.py/dispatch.py changed) — requested
  in the Ergebnis; a dispatched run must not restart the board (WB-17 rule).
