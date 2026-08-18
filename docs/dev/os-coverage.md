---
title: OS coverage status
date: 2026-08-14
tags: [meta, setup]
summary: Which operating systems the init flow has actually been verified on.
---

# OS Coverage

The template assumes nothing about the target OS, but verification so far is:

| OS | Status |
|---|---|
| Linux | Init rehearsed end-to-end mechanically (see journal 2026-08-14) |
| Windows | Unit suite green in CI since 2026-08-17. The counts below are from that run and are NOT re-measured per release — the suite has grown since (566 in the 1.1.0 export). Some tests skip themselves with a printed reason. The init path itself is still paper-reviewed — VERIFY ON FIRST REAL DEPLOYMENT |
| macOS | Unit suite green in CI since 2026-08-17, same caveat about counts. Two real macOS bugs were found and fixed on the way (truncating `ps`, zombie detection). **Nobody has RUN the board there** — VERIFY ON FIRST REAL DEPLOYMENT |

First deployment on an unverified OS: treat init as suspect, journal every deviation,
and fix the `initialize-tool` skill (bump its version) so the next machine benefits.
