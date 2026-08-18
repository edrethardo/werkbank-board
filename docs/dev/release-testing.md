---
title: Release testing — the fresh-machine check before every public release
date: 2026-08-17
tags: [setup, decision]
summary: What to run before publishing, in what order, and why each step exists. The centrepiece is scripts/release-smoke.py, which follows the README literally in a sandbox and exercises the whole dispatch path without spending quota.
outcome: done
---

# Release testing

Reading the README does not tell you whether a stranger can clone this and use
it. Running it does. This is the whole pre-release procedure; it takes about
four minutes and needs no network beyond GitHub.

## The three commands

```bash
# 1. Build the public copy from a FROZEN commit, never the live checkout
git worktree add --detach /tmp/relbuild HEAD
cd /tmp/relbuild && python3 scripts/publish-clean-copy.py --out /tmp/relbuild-dist

# 2. Fresh-machine test against that copy
cd /path/to/werkbank && python3 scripts/release-smoke.py --copy /tmp/relbuild-dist

# 3. Both CI jobs green on the commit you are about to publish
gh run list --workflow tests.yml --limit 1
```

All three must pass. Then mirror and publish per
[`release-sync.md`](release-sync.md) — which is the owner's call, not the
assistant's.

## Why a frozen worktree (step 1)

Several sessions share this checkout. The export copies the WORKING TREE, so a
build started while somebody else is mid-edit publishes their half-finished
work. It nearly happened on 2026-08-17; the publisher now refuses a dirty tree,
and building from `git worktree add --detach HEAD` removes the race entirely.

## What the fresh-machine test actually does (step 2)

Sandbox: a throwaway directory, a throwaway `HOME` (so nothing reads or writes
the real `~/.claude`, `~/.config` or log dir), a free port, and a STAND-IN for
the `claude` binary — the dispatch path is proven end to end without spending
a cent of quota or touching a real project.

Eighteen checks, in the order a new user meets them:

1. the copy carries no `config.json` and no `tickets/` — as a stranger gets it;
2. `config.example.json` exists (README step 1);
3. the board starts and serves the page (README step 2);
4. the ticket list is empty and error-free, and the password hash does not
   reach the page;
5. creating a ticket through the API writes a file in `tickets/`;
6. dragging it to *In Arbeit* really starts an agent, the run reaches *Review*,
   and its result lands in the ticket;
7. the documented CLI: `--help`, `--set-password` (stores only a hash),
   `--lan-on` refuses without a password, and the board REFUSES TO START when
   the config would expose it unprotected;
8. the shipped `examples/opencode-task` reports a missing directory cleanly;
9. the whole test suite runs green inside the copy.

On failure the sandbox is kept and the failing step prints what it saw.

## What it has already caught

* **A fresh install could never start an agent.** `tickets/` does not exist yet
  on a new install; opening the board constructs the dispatcher, the lock file
  cannot be created inside a missing directory, and the dispatcher marks itself
  "not this board" for the life of the process — every dispatch then refused
  SILENTLY. The unit suite could not see it because its fixtures always create
  the directory first.
* **A refused start swept tickets first.** The board decided it would not start
  (LAN without a password) only AFTER the startup sweep had marked a healthy
  in_arbeit ticket as failed.

Both were invisible to 440 unit tests and obvious within one fresh-machine run.

## The three-OS proof (step 3)

`gh run list --workflow tests.yml --limit 1` gives the aggregate; with
`fail-fast: false` a run is only green when EVERY job is. For the release note
you want the jobs individually:

    gh api repos/<owner>/<repo>/actions/runs/<id>/attempts/1/jobs \
      --jq '.jobs[] | "\(.conclusion)  \(.name)"'

(The plain `…/jobs` endpoint 404s intermittently; the attempt-scoped one has
been reliable.)

## Windows (step 3)

CI runs the suite on `ubuntu-latest` and `windows-latest` on every push and has
been green on both since 2026-08-17 (427+ tests, ~22 skipped on Windows with
printed reasons — process groups, signals, `/proc`, symlinks).

**Green CI is not "it works on Windows".** Nobody has run the board on a Windows
machine. The chat-handover watcher (`bash`) and the opencode check (`/bin/sh -c`)
are shell-based and untested there, and the init path has only ever been read.
The README says exactly this; keep it that way until someone actually does it.

## What this procedure still does not cover

A real agent run (costs quota, needs credentials), a real local model, the
phone/LAN path over an actual network, and any browser behaviour — the board's
JavaScript is only pinned by string-shape tests. If you change `board.html`,
open it and look.
