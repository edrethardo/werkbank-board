---
title: ~/.claude cleanup landed — Werkbank verified unaffected
date: 2026-08-15
tags: [setup, investigation]
summary: Template session removed the six shared kit skills and the v1 machine-wide block with the owner's approval; verified Werkbank's eleven project-local skills intact and nothing here referenced the removed state.
outcome: done
---

# ~/.claude cleanup landed — Werkbank verified unaffected

## What was asked

Template session notified (informational): with the owner's approval, the six kit
skills were removed from ~/.claude/skills (the owner's own four remain) and the v1
developer-agent block — including the tool list naming Werkbank — was replaced by
a v5 block (one offer-once behaviour; explicit "never install skills into
~/.claude/skills/"). This closes the follow-up from
[2026-08-15-project-local-skills.md](2026-08-15-project-local-skills.md).

## What I did

Verified on disk: ~/.claude/skills holds exactly the owner's four personal skills;
~/.claude/CLAUDE.md carries the v5 block; Werkbank's .claude/skills/ still lists
all eleven project skills (six kit v3, syncing-the-kit, create-ticket,
start-board, work-tickets, _user-level). No Werkbank file references the removed
global state — the last such reference died with initialize-tool earlier today.

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- No reply sent to the peer ("nothing further needed") — cross-session chatter is
  kept to decisions, not acks.
- One correction to the peer's framing, recorded here rather than argued there:
  our project-local skills DO have a fallback on mistaken deletion — git history —
  unlike the unversioned ~/.claude copies ever had.

## Follow-ups

- Werkbank is no longer registered machine-wide (by design). If a future feature
  needs target-project sessions to discover the board without being told, that
  discovery must ship with werkbank-pull-ticket itself — the only sanctioned
  global artifact — never via ~/.claude/CLAUDE.md.
