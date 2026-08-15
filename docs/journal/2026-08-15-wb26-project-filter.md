---
title: WB-26 — project filter on the board
date: 2026-08-15
tags: [feature]
summary: Header dropdown filters the board to one project (persisted in localStorage); blocking logic still sees all tickets; board.html only, no restart needed.
outcome: done
---

# WB-26 — project filter on the board

## What was asked

Ticket WB-26 (chat handover): "erlaube filtern nach projekt" — empty
description; read as a board filter now that multiple projects exist. Clear
enough to pass the clarity gate without questions.

## What I did

- board.html only: header select ("Alle Projekte" + named projects + any
  legacy ticket paths, labeled by their last segment), filters the columns;
  choice persisted in localStorage (werkbank-projekt-filter) and restored,
  falling back to "all" if the stored path vanishes. Options rebuild only when
  their signature changes, so the WB-10 no-flicker behavior is preserved;
  filter changes re-render directly without a data fetch.
- Deliberate: `blocking_reasons`/link badges keep operating on ALL tickets —
  a filter must not change scheduling semantics, only visibility (noted in the
  user manual).
- node --check green on both script blocks; 88 tests unchanged green (no
  Python touched). No server restart needed (board.html is re-read per GET).

## What I tried that didn't work — and why

Nothing failed.

## Decisions made

- Client-side filtering (tickets are already all in the payload) — a server
  filter would add API surface for zero gain at this scale.

## Follow-ups

- None.
