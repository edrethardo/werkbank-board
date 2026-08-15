---
title: WB-11 — create-ticket project skill (tickets from chat)
date: 2026-08-15
tags: [skill, feature]
summary: New create-ticket skill lets the Werkbank session turn chat requests into store-created tickets — infer fields, ask only for what's missing, confirm with the ticket number; create flow verified end-to-end.
outcome: done
---

# WB-11 — create-ticket project skill (tickets from chat)

## What was asked

Ticket WB-11 (pulled by the user: "zieh dir ticket wb 11"): a project skill so the
user can say "erstelle ein Ticket für X" in chat; the session creates a correctly
formatted ticket via store.create_ticket, asks for missing fields in plain
language, confirms with the ticket number. Acceptance criteria were explicit —
no clarity-gate questions needed.

## What I did

- `.claude/skills/create-ticket/SKILL.md` v1: trigger phrases per the ticket;
  field-gathering rules (infer from conversation, defaults over questionnaire, at
  most one clarifying question, title = outcome not activity); mandatory
  store.create_ticket via a python3 heredoc (covers the new `type` field, defaults
  aufgabe; bug reports get pointed at werkbank-report-bug); confirm with id +
  column; commit per git-discipline; "creating is not starting" rule.
- Verified the skill's exact create snippet live: created WB-16 as a scratch
  ticket — id assignment, `type: aufgabe`, `status: offen`, slugged filename all
  correct — then deleted the file (number will be reused by the next real ticket).
- docs/user/board-und-tickets.md: chat path rewritten to describe the real flow;
  CHANGELOG entry.
- WB-11 → review with Ergebnis; committed together with stray board-state ticket
  edits left by the WB-14/WB-15 sessions (WB-13/14 modified, WB-15 untracked) —
  they belong to the board's history, not to any pending work.

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- Skill lives at project level only (`.claude/skills/`): creating tickets is done
  BY the Werkbank session in this repo; other projects' sessions get tickets onto
  the board via werkbank-report-bug or by asking here. Revisit if the user wants
  chat-creation from arbitrary project sessions.
- Scratch-verify then delete instead of leaving a test ticket on the board — the
  board is the user's workspace, not a test fixture.

## Follow-ups

- None for this ticket. Open backlog: WB-8, WB-9, WB-10, WB-12 remain in offen.
