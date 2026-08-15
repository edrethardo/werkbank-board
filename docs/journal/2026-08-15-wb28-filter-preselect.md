---
title: WB-28 — filter preselects project in create dialog; per-project lineage proven
date: 2026-08-15
tags: [feature]
summary: Create dialog preselects the filtered project; the per-project session lineage concern from the ticket title turned out already correct and is now pinned by a regression test (89 green).
outcome: done
---

# WB-28 — filter preselects project in create dialog; per-project lineage proven

## What was asked

Ticket WB-28 (chat handover): title asked to ensure the "next agent that
pulls" memory is per project; description asked that an active project filter
preselects the project for new tickets.

## What I did

- Lineage check FIRST: wrote `PerProjectLineageTest` (two projects, two
  registered sessions, handover for a project-B ticket must carry B's session).
  It passed immediately — state.json has been keyed by project path since
  WB-14, so this half of the ticket was already correct; the test stays as a
  regression pin. Honestly reported as "verified, not fixed".
- board.html: `openCreate` preselects `projectFilter || default_project` —
  one line. node --check green, suite 89 green.
- CHANGELOG entry; no restart needed (board-only), page reload suffices.

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- The detail dialog keeps showing the ticket's OWN project regardless of
  filter — editing must never silently re-home a ticket.

## Follow-ups

- WB-27 ("add shutter speed") is the first real foreign-project ticket in
  Offen — when it runs, the user-level pull/report skills (still awaiting
  install approval) become relevant for that project's sessions.
