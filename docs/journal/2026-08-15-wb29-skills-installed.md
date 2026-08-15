---
title: WB-29 — user-level Werkbank skills installed after explicit approval
date: 2026-08-15
tags: [skill, setup]
summary: werkbank-pull-ticket (upgraded to v4 with the WB-22 handover watcher) and werkbank-report-bug v1 installed to ~/.claude/skills/ with the user's go; delivery copies synced into _user-level, staging emptied.
outcome: done
---

# WB-29 — user-level Werkbank skills installed after explicit approval

## What was asked

Ticket WB-29 (chat handover): install the Werkbank skills for the user — "ask
for permission first but tell it's more handy like that".

## What I did

- Upgraded the staged pull skill to v4 BEFORE installing: registration note now
  reflects WB-22 (handover to the live chat, fallback fork) and a new section 5
  adds the handover watcher adapted for foreign projects (absolute tickets
  path, store-based claim command). Report-bug went in unchanged (v1).
- Asked with the recommended-install framing per the ticket; user chose "beide
  installieren". Installed to `~/.claude/skills/werkbank-pull-ticket/` and
  `~/.claude/skills/werkbank-report-bug/`.
- Synced identical delivery copies into `.claude/skills/_user-level/` (the
  creating-skills both-copies rule), emptied `staged-skills/` down to its
  README, verified with `diff -q` (identical) — closing the gap open since
  WB-5/WB-13.
- User docs updated (skills now described as installed), CHANGELOG entry.

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- Pull skill v4 upgrade first, then install — installing the stale v3 would
  have shipped a skill whose registration promise ("always fork") no longer
  matches the WB-22 behavior, and without the watcher other projects' sessions
  could never receive chat handovers.

## Follow-ups

- WB-27 ("add shutter speed") can now be pulled by its own project's session
  ("zieh dir dein Ticket" there) — first real cross-project exercise.
