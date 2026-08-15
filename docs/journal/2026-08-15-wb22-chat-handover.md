---
title: WB-22 — dragged tickets are handed to the live chat session visibly
date: 2026-08-15
tags: [feature, decision, investigation]
summary: Messenger-run relay proved infeasible (SendMessage tool absent in headless runs), so handover works via a marker field plus a session-side watcher; unclaimed handovers fall back to a forked background run; 79 tests green.
outcome: done
---

# WB-22 — dragged tickets are handed to the live chat session visibly

## What was asked

Ticket WB-22 (user, after rejecting a docs-only fix): when the remembered ticket
lineage is an OPEN chat conversation, a dragged ticket must be worked VISIBLY in
that chat, not by a silent forked copy.

## Feasibility first — the ticket's proposed mechanism died honestly

The ticket sketched a messenger run delivering the ticket via the
session-to-session channel. Measured: a `claude -p` run reports the SendMessage
tool is NOT available (neither directly nor via tool search) — the channel
exists only for interactive sessions, and no MCP server provides it that could
be handed to a headless run (no `mcpServers` anywhere in the configs). Raw
socket speaking was already ruled out in the drag-dispatch journal. So: relay
dead, documented here; the ticket's outcome criteria are met by a different
mechanism, which its text explicitly allowed ("beim Umsetzen verifizieren").

## The mechanism that works — marker + watcher

- New frontmatter field `handover`: the dispatcher, seeing an interactive
  lineage (state.json) and fork ≠ ja, writes `handover: <session-id>` instead
  of spawning a run, and arms a fallback timer (`chat_handover_minutes`,
  default 5).
- The chat session keeps a cheap background watcher loop (work-tickets skill
  v5) greping tickets/ for its own id; the harness wakes it, it CLAIMS
  (`handover: ""`, `session: <own id>`), announces the ticket to the user, and
  works it visibly.
- Unclaimed deadline → marker cleared, ticket re-enqueued as a normal forked
  background run (`_handover_failed` prevents a handover loop). Nothing can
  hang: startup re-arms timers for surviving markers, and sweep_orphaned now
  spares pending handovers and tickets claimed by a session that state.json
  marks interactive (everything else still sweeps to fehlgeschlagen).
- Board: in_arbeit cards show "an Chat-Session übergeben … (wartet auf
  Übernahme)" / "wird sichtbar in Chat-Session … bearbeitet" honestly.

## What I tried that didn't work — and why

- Messenger relay (above): `FAILED:SendMessage-Tool ist in dieser Session nicht
  verfügbar` from the probe run — the pivot, not a workaround, is the design.
- First skill edit was based on the stale v2: WB-19's dispatched run had staged
  its v4 (registration section) in staged-skills/ because .claude/ is protected
  for headless runs — merged staged v4 + handover section into v5 and removed
  the staging copy. Lesson: check staged-skills/ before editing a skill the
  board's agents also maintain.

## Decisions made

- Marker in the ticket file rather than any push channel: works with existing
  primitives, survives restarts, visible to the board for free.
- Fallback to a FORKED background run (never in-place) — the interactive
  session might wake up later and continue its own conversation.
- staged-skills werkbank-pull-ticket / werkbank-report-bug (user-level targets)
  remain uninstalled — needs the user's go for ~/.claude writes.

## Follow-ups

- Watcher started in this chat session; test ticket WB-23 (nach: WB-22) is on
  the board for the live drag test after WB-22's review.
- The pull skill's staged copy should gain the watcher section when installed.
