---
title: WB-259 — proposed patch for the werkbank-pull-ticket skill (watcher loop uses wall-clock deadline)
date: 2026-08-20
tags: [decision, docs]
summary: The Step 5 handover-watcher block in `werkbank-pull-ticket` uses `end=$((SECONDS+7200))`, which fires immediately in the way the background bash is wrapped — the loop exits within seconds looking successful, and every claimed handover during the "watching" window silently misses the chat. Companion failure the same day: the loop was previously extracted into `/tmp`, which was cleared, so the restart aborted with `exit 127`. This doc carries the exact Step 5 replacement plus a verify-after-a-few-seconds note; the write to `.claude/skills/_user-level/werkbank-pull-ticket/SKILL.md` is blocked in dispatched runs, so an interactive session has to apply it.
---

# WB-259 — proposed patch for `werkbank-pull-ticket`, Step 5

> **OBSOLETE — DO NOT APPLY (2026-08-21).** WB-258 removed the handover watcher
> entirely: the board delivers into the chat session over its messaging socket,
> and the shipped skill is v11, whose Step 5 says „Do not start a polling
> watcher". Applying the patch below would rebuild the very loop that skill now
> forbids. It is kept as the record of a measured failure — the `$SECONDS`
> deadline that fired immediately — not as an instruction.

## Why the patch, not the patch itself, ships here

Same reason as WB-166: dispatched runs cannot write to `.claude/skills/**`
by design (an agent running from a dispatched context should not be able
to rewrite the skills that shape how future agents work). The write is
one of these:

- **Interactive session:** open
  `.claude/skills/_user-level/werkbank-pull-ticket/SKILL.md` and replace
  the Step 5 block plus the surrounding paragraph as shown below. Bump
  the version in the frontmatter from `9` to `10`.
- **Command line:** with the block below saved somewhere durable (NOT
  `/tmp` — that is what bit the second time on 2026-08-20), open the
  file in an editor and paste.

Only after the file is in place should the installed copy at
`~/.claude/skills/werkbank-pull-ticket/SKILL.md` be refreshed (the init
skill's copy step, or a manual `cp` — with the `WERKBANK=` line
re-checked, per the CLAUDE.md warning about the five placeholder sites).

## What is wrong

Step 5 currently ends with (lines ~181–187 in v9):

    Start it (Bash, run_in_background):

        WERKBANK=/pfad/zur/werkbank
        end=$((SECONDS+7200)); while [ $SECONDS -lt $end ]; do
          f=$(grep -l "^handover: $CLAUDE_CODE_SESSION_ID" "$WERKBANK"/tickets/*.md 2>/dev/null | head -1)
          [ -n "$f" ] && { echo "HANDOVER:$f"; exit 0; }; sleep 5
        done; echo TIMEOUT; exit 1

Two failure modes, both measured on 2026-08-20:

1. **`$SECONDS` is per-shell and evaluated too early in the wrapping.**
   The way the background bash is invoked, `$SECONDS` is 0 when
   `end=$((SECONDS+7200))` runs AND when `[ $SECONDS -lt $end ]` is
   compared — so the condition is false on the very first iteration.
   Concrete observation: the watcher started after WB-257 exited with
   `TIMEOUT` seconds later, not two hours later. Everything in the
   interval was silently lost to the background lane instead of reaching
   the chat.
2. **Scratchpad dependency.** A previous attempt to work around (1) put
   the loop into `/tmp/watcher.sh` and started it from there. `/tmp` was
   cleared during a session restart; the next invocation aborted with
   `exit 127` (no such file). Inlining removes the dependency and makes
   the failure impossible.

Two smaller papercuts:

- `exit 1` on regular timeout surfaces as a red "failed" bubble in the
  chat, even though a 2-hour deadline passing without a handover is
  the expected happy path.
- The skill nowhere tells the caller to VERIFY the watcher actually
  runs, so both failure modes above stay invisible until a real handover
  arrives and does not reach the chat.

## Replacement — Step 5 tail (drop-in for the section starting with "Start it")

Everything above "Start it (Bash, run_in_background):" stays as it is in
v9. Replace from that line through `exit 1` with:

    Start it (Bash, run_in_background) — inline, NEVER from a scratchpad
    script (`/tmp` gets cleared and the next restart fails with `exit 127`,
    WB-259):

        WERKBANK=/pfad/zur/werkbank
        end=$(( $(date +%s) + 7200 )); while [ "$(date +%s)" -lt "$end" ]; do
          f=$(grep -l "^handover: $CLAUDE_CODE_SESSION_ID" "$WERKBANK"/tickets/*.md 2>/dev/null | head -1)
          [ -n "$f" ] && { echo "HANDOVER:$f"; exit 0; }; sleep 5
        done; echo TIMEOUT; exit 0

    **Verify the watcher actually runs.** `$SECONDS` is per-shell and was
    evaluated to 0 too late in the wrapping, so the old deadline
    `end=$((SECONDS+7200))` fired on the first iteration — the loop
    exited within seconds and looked started (WB-259). Wall-clock
    (`date +%s`) closes that. Check after a few seconds with
    BashOutput: empty output = the loop is running, `TIMEOUT` = the loop
    already finished, which against a 2-hour deadline means the deadline
    itself is wrong again. A regular 2-hour timeout exits 0; it is not a
    failure and must not surface as one.

## Frontmatter

    version: 10

(from `9`)

## Verification the user can do after applying

1. In an interactive Claude Code session in any project, ask the session
   to pull its ticket. It will run the Step 5 background bash.
2. Wait ~10 seconds, then check the bash output. Empty = fixed. `TIMEOUT`
   in seconds = the deadline is still wrong; do not ship.
3. Optional: drop a fake handover marker into a test ticket's frontmatter
   with the session's id and confirm the loop emits `HANDOVER:<file>`
   within `sleep 5`.

## Long-term note

This is a stopgap. The neighbouring ticket WB-258 (direct delivery via
the messaging socket) would remove the whole watcher-loop path. Until
that lands, the loop is what stands between a claimed handover and
silence.
