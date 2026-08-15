---
title: WB-6 — board dialogs made resizable
date: 2026-08-15
tags: [feature]
summary: Dispatched run added resize:both with min/max bounds to the board dialogs; verified in the file, visual check left to the user's review.
outcome: done
---

# WB-6 — board dialogs made resizable

## What was asked

Ticket WB-6: "mache ticketfenster größenverstellbar" — empty description. Read as:
the board's dialogs (create/detail/reject) should be user-resizable. Title deemed
unambiguous enough to proceed without the clarity-gate stop.

## What I did

(Board-dispatched headless run, forked from the Werkbank session.)

- `src/werkbank/board.html`: the shared `dialog` CSS rule gains `resize: both;
  overflow: auto; min-width: 20rem; min-height: 8rem; max-width: 95vw;
  max-height: 88vh`. Applies to all three dialogs; CSS-only, no JS change.
- CHANGELOG entry and one line in docs/user/board-und-tickets.md.

## What I tried that didn't work — and why

- `curl` against the running board to confirm the served page was blocked by this
  run's permissions ("requires approval"). Fell back to grepping the file;
  board.html is re-read on every GET (start-board skill), so served = file.

## Decisions made

- No browser-visual verification possible from a headless run — stated honestly in
  the Ergebnis; the review step is the designated catch.
- Textareas keep their own vertical resize; no flex re-layout of dialog innards —
  smallest change that satisfies the title.

## Follow-ups

- If the user meant something else by "Ticketfenster" (e.g. the board columns),
  the reject-with-reason loop will say so.
