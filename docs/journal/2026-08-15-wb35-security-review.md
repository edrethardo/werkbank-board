---
title: WB-35 — pre-public security review and hardening
date: 2026-08-15
tags: [investigation, bugfix, decision]
summary: Adversarial review found a CSRF→RCE chain, DNS rebinding, frontmatter injection and stored XSS; all four fixed with regression tests, plus log/lock/picker hardening, skill injection fixes and a README security section; 122 tests green.
outcome: done
---

# WB-35 — pre-public security review and hardening

## What was asked

Ticket WB-35 (chat handover, second attempt — the first died on an API session
limit): final review before the repo goes public.

## How it was reviewed

- One independent adversarial subagent on the CODE (mandate: find reasons not
  to publish; read-only). Quota was at 82 % of the seven-day limit, so the
  other two angles were done mechanically by me instead of by agents: secret/
  PII grep over tracked files, README/manual claims against the code, index
  completeness, test run.
- My own scan found what the earlier WB-31 cleanup had missed: **my own
  cleanup notes had re-introduced the owner's e-mail as plaintext**, and the
  README still described five columns and 91 tests. Both fixed before the
  security work.

## Findings and what happened to them

| # | Finding | Verdict |
|---|---------|---------|
| F1 | **CSRF → RCE**: `_json_body` ignored Content-Type, no Origin check. Any web page could POST a ticket (with `fork: ja`) and drive it to `in_arbeit` → `claude -p` with attacker text, Bash pre-approved | FIXED — `guard.check_write`: JSON content type required, same-origin Origin when present |
| F2 | **DNS rebinding**: no `Host` validation, so an attacker domain resolving to 127.0.0.1 would be same-origin | FIXED — `guard.check_read/write` require a localhost Host |
| F4 | **Frontmatter injection**: no field rejected newlines; later duplicate keys override earlier ones, so `handover` could rewrite `id`/`status`/`project` → arbitrary `.md` write outside `tickets/`, `.log` append, id confusion | FIXED — newline rejection in `serialize_ticket`, duplicate-key rejection in `parse_ticket`, `WB-\d+` assertion before the rename |
| F5 | **Stored XSS → RCE**: `cardEl` built the card with `innerHTML` including `t.id`/`t.priority`; injected script runs same-origin and can start runs | FIXED — card built with `createElement`/`textContent`, priority whitelisted; CSP + `X-Frame-Options: DENY` + `nosniff` added |
| F3 | `/api/browse` browsed the whole filesystem, error echoed the path (existence oracle) | FIXED — confined to home + registered projects, generic refusal text |
| F6 | Agent logs in world-readable `/tmp` with predictable names (symlink pre-planting) | FIXED — `~/.local/state/werkbank/logs`, `0700`, `O_NOFOLLOW`, `0600` |
| F7 | Lock files and the atomic-write sibling followed symlinks | FIXED — `O_NOFOLLOW` locks, `tempfile.mkstemp` sibling |
| F8 | Symlinks inside `tickets/` were read through | FIXED — `_paths` skips symlinks |
| F9 | Unbounded request body (memory DoS) | FIXED — 1 MiB cap |
| Skills | `create-ticket` / `werkbank-report-bug` substituted user text into a Python heredoc (quote → code execution); `$PWD` interpolated into a Python literal | FIXED — values now travel through the environment; skills bumped (create-ticket v3, report-bug v2) |
| F10 | No security section anywhere; `acceptEdits` + Bash defaults undocumented for third parties | FIXED — README security section stating plainly that a ticket is an executable prompt |
| — | Prompt injection through ticket text | ACCEPTED by design (tickets *are* instructions); documented instead |

Reviewer's clean list (verified, no action): subprocess list-form with
`shell=False`, all other DOM sinks use `textContent`, anchored route regexes,
`nach`/`nicht_mit` validation, the flock/RLock layering, `os.replace`
atomicity, no secrets in `src/`, `.gitignore` coverage.

## Verification

122 tests green (`python3 -m unittest discover -s tests`), including a new
`tests/test_security.py` with 14 regression tests: cross-origin POST, text/plain
POST, rebound Host, board request passes, curl passes, read guard, newline in
title, newline in an updatable field, duplicate keys, poisoned id rename,
symlinked ticket file, and three folder-picker containment cases. The three
older WB-25 picker tests encoded the pre-containment contract and were pulled
onto the new one.

## What I tried that didn't work — and why

Nothing failed. Worth recording: the earlier WB-31 review had declared the
board "XSS-safe rendering" — that claim was wrong (or the `innerHTML` template
was added later), which is exactly why a second, independent adversarial pass
before publishing was worth the tokens.

## Decisions made

- Guards live in their own module (`guard.py`) with the reasoning in the
  docstring, so the next reader sees WHY a JSON content type is a security
  control and not a formality.
- Non-browser clients without `Origin` stay allowed: the skills and curl must
  keep working, and a hostile web page cannot omit `Origin`.
- Prompt injection is documented rather than "fixed" — no delimiter makes
  instructions-from-data safe when the data IS the instruction.

## Follow-ups

- WB-33 (make public) can proceed once the owner decides the three privacy
  questions; the security half is now covered by the README section.
