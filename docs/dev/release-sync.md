---
title: Releasing the public copy — the two rules that are easy to get wrong
date: 2026-08-16
tags: [decision, setup]
summary: How dist/ is built and mirrored into the public repo, why the journal is excluded from the export but preserved in the public tree, and why the publisher itself never ships.
outcome: done
---

# Releasing the public copy

`scripts/publish-clean-copy.py` builds `dist/werkbank-board/` and refuses to
report READY unless every gate passes. That part is automated and needs no
judgement. Two things around it do, and both were got wrong once.

## Rule 1 — the export drops the journal, the SYNC must not delete it

Decision 2026-08-16: **new journal entries are not published.** The journal is
the internal work record; it names sessions, incidents and reasoning that only
concerns this machine.

But 44 entries from the run-up to 1.0 are ALREADY in the public repo, and the
owner decided to keep them there deliberately — as a sample of what the
journalling habit produces. They were checked and contain no private data.

So the export has no `docs/journal/`, and the mirror step must leave the
directory alone instead of deleting what it does not have:

    rsync -a --delete --exclude=.git \
          --exclude='docs/journal' --exclude='docs/journal/**' \
          dist/werkbank-board/ "$clone/"

Without those two excludes, `--delete` removes all 44 entries — a silent
deletion nobody asked for, and the README points at them.

The README describes them as a sample that stops at 1.0. Keep that true: do not
start adding new entries there by hand.

## Rule 2 — the publisher never ships

`scripts/` is excluded from the export. The publish script has to NAME what it
scrubs (the address forms, the path shapes), so it is the one file that cannot
be scrubbed by itself. It shipped once with the owner's real LAN address and
his name quoted in its own comments, because the comments documented the leaks
they had just fixed.

Same class, same round: the final gate runs the test suite INSIDE the export,
which regenerates `__pycache__` after every check has passed. Each `.pyc`
embeds the absolute path of its source — the real username — and no text gate
can see it, because it is not text. The script now purges bytecode after the
test run and then proves it is gone.

**The pattern to watch for: a check that exempts itself.** Every leak found in
the 1.0 review was one — the redaction script skipping its own file, the IP gate
skipping `scripts/`, the test phase running after the gates.

## Release notes from the CHANGELOG

    sed -n '/^## \[1.0.0\]/,$p' CHANGELOG.md

Not `… /,/^## \[/p | head -n -1`: that assumes a section FOLLOWS 1.0.0. It is
the last one, so the range runs to EOF and `head -n -1` eats a content line
instead of a heading.

## Order of operations

1. `python3 scripts/publish-clean-copy.py` — must print READY.
2. Clone the public repo fresh, mirror with the rsync above.
3. Read the diff. Check deletions especially: a deletion is either intended or
   a bug in the exclude list.
4. Commit, push, tag, GitHub release. These are the owner's call — visible,
   irreversible actions in a public repo.
