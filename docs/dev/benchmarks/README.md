---
title: Benchmark — opencode lane vs a forked Claude run
date: 2026-08-16
tags: [investigation, decision]
summary: Measured comparison of the local model (opencode lane) against a Claude run in a separate worktree on three real tickets, including the head-to-head on WB-108.
---

# Benchmark: local model vs Claude (2026-08-16)

Requested by the user ("einmal du mit opencode vs eine geforkte session von dir
alleine … miss es genau"). Raw data next to this file as JSON.

## Setup

Both sides got the SAME task text — `title + body`, exactly what
`opencode.work_ticket` sends — and the same acceptance check ("Tests laufen
durch" = `python3 -m pytest tests/ -q`). The Claude side ran in its own frozen
`git worktree` (`claude -p --permission-mode acceptEdits --allowedTools Bash
--output-format json`), the opencode side through the board in the live
checkout. Runs were sequential, so neither competed for the GPU inside its own
measurement.

## Results

| Ticket | Side | Wall clock | Cost | Check | Outcome |
|---|---|---|---|---|---|
| WB-106 (bugfix `guard.py`) | opencode | 40.0 min | ~$0.105 | green (323) | done, accepted |
| WB-107 (docs + 2 tests) | opencode | 23.7 min | ~$0.105 | green (325) | done, accepted |
| **WB-108** (new `tests/test_server.py`) | **Claude Opus** | **7.6 min** | **$2.48** | green (327) | done |
| **WB-108** (same ticket) | **opencode** | **58 min** | **$0** | RED | **failed — 60-min budget exhausted** |

Cost note: the local run itself costs no quota, but the flow's Claude diff
review does. It is not logged by the Werkbank, so it was re-measured with the
same call on WB-106's committed diff: **$0.105** (32 s, sonnet, one tool-less
turn). Same order of magnitude, not bit-identical to the original.

## The head-to-head (WB-108)

- **Claude:** 48 turns, 7.6 min, $2.48, 8 new tests, suite green (327).
- **opencode:** two attempts, 58 min, budget exhausted, ticket → failed. Its
  leftover `tests/test_server.py` (7 tests) **passes in isolation but breaks the
  full suite**: importing `server.py` starts a real Dispatcher whose ticker
  outlives the module, so `test_dispatch.tearDownModule` fails
  ("queue ticker(s) outlived the tests"). Archived here as
  `wb108-opencode-artefakt-test_server.py.txt` and removed from `tests/` so the
  suite is green again (325).
- Both sides hit the SAME trap — importing `server.py` has side effects. Claude
  spotted it and mitigated it (`server.DISPATCHER.stop()` right after the import,
  with a comment explaining why); the local model did not.
- **Caveat, stated plainly:** the opencode side was disturbed. Between 19:27 and
  19:31 a second, ownerless opencode process worked the same ticket and the same
  file (filed as WB-142). The lost time is not separable from the measurement,
  so "58 min, failed" is an upper bound on quality, not a clean duel.

## What this says (and does not)

- **Cost:** the local lane is ~96 % cheaper per completed ticket ($0.105 vs
  $2.48 on comparable work).
- **Speed:** 3–8× slower on tickets it can do at all (23.7 and 40.0 min vs 7.6).
- **Ceiling:** on the hardest of the three tickets it did not finish within an
  hour, and what it left behind would have broken the suite for everyone else.
- **Quality where it succeeded:** genuinely good — WB-106 reproduced the bug
  before fixing it, added four regression tests, wrote a journal entry and named
  honestly what it could not verify. Claims were spot-checked and held.
- Sample size is three tickets on one project. Treat it as a direction, not a law.

## Practical rule derived from this

Give the local lane small, bounded work with a check, when nobody is waiting.
Anything touching concurrency, process handling or cross-module wiring goes to
Claude — that is exactly where the hour ran out. (This matches the assignee
guidance in the `create-ticket` skill, now backed by measurement.)
