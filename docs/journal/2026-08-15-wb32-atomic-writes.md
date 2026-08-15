---
title: WB-32 — false "kaputte Ticket-Datei" flashes fixed by atomic writes
date: 2026-08-15
tags: [bugfix]
summary: Readers could catch a ticket file mid-write (write_text truncates first) — hammer repro produced 324 parse errors; fixed with tmp-sibling + os.replace, regression test now reads clean under write pressure; 92 tests green.
outcome: done
---

# WB-32 — false "kaputte Ticket-Datei" flashes fixed by atomic writes

## What was asked

Bug report WB-32 (chat handover): "jedes ticket das ich in arbeit ziehe ist
angeblich kaputt — find raus was da los ist."

## How the bug was reproduced (bug discipline)

- Board state showed NO stuck errors and a scripted drag reproduced nothing —
  so not a persistent corruption but a transient one.
- Hypothesis: `Path.write_text` truncates before writing; the board's reader
  (`load_tickets_with_errors`, taken WITHOUT the write lock, by design cheap)
  can read the file in that window → parse error → red "Kaputte Ticket-Datei"
  banner for one poll cycle. A drag triggers several writes in quick
  succession (status, handover marker, claim) plus an immediate client
  refresh — maximizing the chance to hit the window.
- Proof: hammer script (reader thread vs 300 updates on a 60 KB ticket) —
  **324 parse errors** ("Kein sauberer Frontmatter-Block").

## The fix

`store._write_ticket_file`: write a hidden sibling (`.<name>.tmp`) then
`os.replace` — atomic on POSIX, readers see old or new content, never partial.
Wired into both write sites (create + update). The regression hammer test
(150 writes under a concurrent reader) now records zero errors; suite 92 green.
Repro scratch ticket WB-35 deleted via the new delete feature.

## What I tried that didn't work — and why

The first repro attempt (single scripted drag + error poll) showed nothing —
the window is microseconds wide; only sustained write pressure with a large
body made it visible. Lesson: transient races need hammers, not single shots.

## Decisions made

- Readers stay lock-free (polling every 5s through the flock would serialize
  the UI behind agent writes); atomicity at the write site is the cheaper and
  complete fix.
- Temp sibling is hidden and non-`.md`, so `_paths` globbing and the picker
  never see it.

## Follow-ups

- None. Board restart required (store.py) — done in this session.
