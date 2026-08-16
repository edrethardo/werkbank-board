---
title: Release-Plan 1.0 (WB-46)
date: 2026-08-15
tags: [decision, docs]
summary: Findings of the WB-46 review and the ticket chain that has to close before the public copy is refreshed and tagged 1.0.
---

# Release-Plan 1.0

Two reviews fed this plan: my own inventory of the private repo, and an
independent "stranger" review that only looked at the PUBLIC repository and
actually ran it (clean clone, suite, server, guard probes).

## Where we stand

- 142 tests green, board runs from a clean clone, no secrets or personal paths
  in the public copy, MIT licensed, 49 journal entries of honest history.
- The public copy is **three commits old**: password login, LAN mode and the
  whole phone view are missing there.

## Findings that must close before 1.0

| # | Finding | Ticket |
|---|---------|--------|
| 1 | The two shipped user-level skills are **broken as published**: my WB-33 scrub replaced absolute paths with `~/…` *inside Python string literals*, which Python never expands → `ModuleNotFoundError`, and one path would create a literal `./~/code/...` folder. The README also says "edit the path at the top" while there are seven occurrences. | WB-47 |
| 2 | Without `config.json`, `default_project` silently becomes **the Werkbank checkout itself** — a Bash-enabled agent aimed at the board's own repo. | WB-48 |
| 3 | (WB-51) The public README is **structurally unsafe**: the "a ticket is an executable prompt" warning sits at line ~190 of 223, after the reader has already started their first agent. It also claims "every change is committed to git" (the server never calls git), does not say the whole UI is German, references a CHANGELOG that is not published, and documents `host` as a free choice when changing it only breaks the board. | WB-49 |
| 4 | Publishing the LAN/password work without rewriting the Security section would make the public README **actively false** about the security model. Publishing is manual today, which is how the copy fell behind in the first place. | WB-50 |
| 5 | First-run failures are raw tracebacks (port in use, `claude` missing), and the systemd unit block-buffers stdout, so `journalctl` shows nothing. | WB-51 |
| 6 | A stranger **cannot set a password** at all — the LAN mode is documented nowhere they can find and has no setup command. | WB-52 |
| 7 | No SECURITY.md, no published CHANGELOG, no version tag, no topics — a tool that advertises a CSRF→RCE finding gives no disclosure channel. | WB-53 |
| 8 | Zero images for a visual Kanban tool; contradictory setup routes (`init` vs README) and doc statements that only hold after step 5. | WB-54, WB-55 |
| 9 | Windows is claimed but there is no Windows *start* command, and the handover watcher is a bash loop (Unix-only) — not stated. CI on both platforms would turn the honest caveat into real coverage. | WB-56 |

## Order (real ticket numbers)

| Ticket | Topic |
|---|---|
| WB-47 | broken shipped skills (tilde in Python strings) |
| WB-48 | default project points at Werkbank itself |
| WB-49 | friendly first-run errors + unbuffered logs |
| WB-50 | password command + LAN mode documented |
| WB-51 | README restructure (waits for WB-50) |
| WB-52 | **release gate**: automated publish + tag 1.0.0 (waits for 47–51, 53) |
| WB-53 | SECURITY.md, published CHANGELOG, topics |
| WB-54 | screenshots / demo |
| WB-55 | doc contradictions for strangers |
| WB-56 | CI on Linux + Windows |

WB-47 … WB-50 are independent and run first; WB-51 needs WB-50's truth about
LAN mode; WB-52 gates the release and waits for everything above plus WB-53.
WB-54/55/56 are polish and stay in Offen until the owner wants them. The table
above is authoritative — the numbers in the findings table were drafted before
the tickets existed.

## Explicitly not doing

CONTRIBUTING, issue templates, code of conduct, badges without CI — ceremony
for a single-maintainer personal tool (stranger review agreed).
