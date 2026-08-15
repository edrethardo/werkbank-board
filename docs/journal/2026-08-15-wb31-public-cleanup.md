---
title: WB-31 — repo cleaned for public release, adversarial review run
date: 2026-08-15
tags: [investigation, docs, decision]
summary: Werkbank README written, owner name/e-mail redacted from current files, tickets/.lock untracked; adversarial subagent found zero secrets in full history but three owner-decisions that WB-33 must present before flipping visibility.
outcome: done
---

# WB-31 — repo cleaned for public release, adversarial review run

## What was asked

Ticket WB-31 (chat handover): clean the repo for the public release, including
an adversarial review; create the make-public ticket immediately as a separate
ticket (done: WB-33, nach WB-31).

## What I did

- README.md rewritten for Werkbank (was still the starter-kit template — the
  adversarial reviewer graded the committed state a release blocker). Claims in
  it were verified by the reviewer against the code (91 tests OK, stdlib-only,
  port, statuses).
- Redactions in CURRENT files: owner e-mail in 2026-08-14-init.md; the owner's
  first name in three journals + INDEX (now "the owner"). Old blobs keep both —
  documented as unfixable-history below.
- tickets/.lock untracked and gitignored (store recreates it on demand).
- Adversarial review by an independent subagent (read-only mandate: find
  reasons NOT to publish). Verified clean: zero secrets/tokens/keys in the FULL
  history, zero LAN IPs, no third-party data, no TODO shame, deleted blobs
  harmless, LICENSE consistent (MIT, pseudonym). Findings fixed: template
  README (blocker), e-mail in file content (blocker), name mentions,
  tracked lock file.

## Owner decisions that remain (for WB-33 — none removable by cleanup)

1. **Commit metadata:** the owner's private e-mail address (deliberately not
   repeated in plaintext here) is the author/committer of every one of the
   ~58 commits. Removable ONLY by rewriting history (all SHAs change,
   against this repo's no-rewrite rule). Publish as-is, or don't.
2. **Paths reveal the first name:** `~/...` appears functionally in
   skills/tickets/config and throughout history — the pseudonym "Ed Rethardo"
   is thereby linkable to a first name.
3. **Public repo = public LIVE board:** publishing this repo publishes the
   actual ticket board — all current AND FUTURE tickets, including other
   projects' tickets (e.g. WB-27 for the another project and its path in
   config.json). Alternative worth offering: publish a clean tool-only copy and
   keep the live board private.
4. Redactions apply to current files only; the pre-redaction blobs remain
   readable in history.

## What I tried that didn't work — and why

Nothing failed. (The reviewer's finding that none of the cleanup was committed
was a snapshot mid-work — commit happens with this entry.)

## Decisions made

- No history rewrite, no unilateral scrubbing of functional ~ paths —
  both would break rules or the tool itself; surfaced as owner decisions
  instead.
- Session UUIDs and quoted chat snippets in tickets/journals stay: local-only
  identifiers, part of the honest build history the README now advertises.

## Follow-ups

- WB-33 (nach WB-31): present decisions 1–3, get explicit confirmation, then
  flip visibility and verify anonymous access.
