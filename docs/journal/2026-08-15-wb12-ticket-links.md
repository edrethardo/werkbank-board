---
title: WB-12 — ticket links: ordering (nach) and mutual exclusion (nicht_mit)
date: 2026-08-15
tags: [feature]
summary: Tickets can now declare "must run after X" and "never at the same time as X"; server rejects blocked starts with a German reason, dispatcher rechecks order at dequeue, board shows badges/tooltips and dims blocked cards; 43 tests green, board restarted on the new code.
outcome: done
---

# WB-12 — ticket links: ordering (nach) and mutual exclusion (nicht_mit)

## What was asked

Ticket WB-12 (board-dispatched run): link tickets so dependencies are visible and
enforced — "muss nach X erledigt werden" and "darf nicht gleichzeitig mit X" —
as flat frontmatter fields, with board badges/tooltips, dimmed blocked cards,
start-time rejection with a plain message, queue-time enforcement, and soft
handling of unknown ids. The ticket said to sharpen semantics with the user;
this run was headless, so the choices below are recorded for review instead.

## What I did

- store.py: new frontmatter keys `nach` / `nicht_mit` (comma lists, normalized
  and validated via `normalize_links`, deduped; legacy files default to empty;
  serializer now rstrips lines so empty fields stay clean).
  `blocking_reasons(all, t, include_exclusion=True)` returns German reasons;
  unknown ids never block. Exclusion is symmetric (either side's declaration
  counts).
- server.py: a POST that would move a ticket offen/fehlgeschlagen → in_arbeit is
  answered with **409 "Nicht gestartet — …"** and NO state change when blocked;
  create accepts the new fields.
- dispatch.py `_run_one`: rechecks the `nach` order at dequeue (blocker may have
  landed in review instead of erledigt); if violated → ticket back to `offen`
  with the reason in `## Ergebnis`. Exclusion needs no dequeue recheck: runs are
  strictly serialized, and the drag check prevents conflicting queueing.
- board.html: link inputs in create + detail dialogs; ⛓/🚫 badges with per-id
  German tooltips including live status ("Wartet auf WB-8 (noch offen)",
  "unbekannt" for deleted ids); blocked cards in Offen/Fehlgeschlagen get
  `.blocked` (opacity .55). Both script blocks pass `node --check`.
- Tests: 43 green (`python3 -m unittest tests.test_store tests.test_dispatch`),
  including the three acceptance criteria: blocked ticket yields reasons /
  bounces from the queue without the runner being called; empty reasons once the
  blocker is erledigt; exclusion blocks in both directions while in_arbeit.
- docs/user section "Tickets verknüpfen", CHANGELOG entry.
- Restarted the board on the new code and verified `/api/tickets` now serves the
  `nach` field.

## What I tried that didn't work — and why

- First restart attempt via `pgrep | kill` reported exit 144 and left the OLD
  server running (verified: served tickets lacked `nach`). The pgrep-self-match
  trap again. Reliable route: `ss -tlnp | grep 8765` → kill that PID → start →
  verify the new field is served. The start-board skill should adopt this.

## Decisions made

- "Nach" requires the blocker to be **erledigt** (user-accepted), not merely
  review — the user owns acceptance, so order gates on their decision.
- Blocked drags are rejected outright rather than queued-in-order: with review
  as a mandatory human step, a queued dependent could wait forever holding the
  serialized queue. The dequeue recheck covers the remaining race.
- Field names German (`nach`, `nicht_mit`) — ticket files are user-facing.

## Follow-ups

- start-board skill: replace pgrep-based stop with the port-PID route (ss/fuser).
- User review may still sharpen semantics (e.g. should "nach" already unblock at
  review?) — one-line change in `blocking_reasons` if so.
