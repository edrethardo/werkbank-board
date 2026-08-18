---
title: opencode dispatch — the named check is the acceptance criterion
date: 2026-08-16
tags: [decision]
summary: Tickets with assignee opencode run on the local model and are accepted only when a configured check passes. The ticket names the check; the command lives in config.json, so nothing that crosses the network is ever executed.
outcome: done
---

# opencode dispatch — the named check is the acceptance criterion

## The problem this exists for

A local model reports success over failing tests. Measured repeatedly on this
machine, and it is not a quirk to be prompted away: a model that cannot run the
verification cannot know, and one that can run it still summarises optimistically.
If its own summary decided whether a ticket is done, the board would fill with
confident green garbage — and the whole value of the board is that its columns
mean something.

So: **no check, no dispatch.** An opencode ticket without a resolvable check is
refused before anything is executed. This is a correctness property, not a policy
dial, and it must survive every future change to the flow.

## Why the ticket carries a NAME and not a command

The obvious design puts the command in the ticket: `gate: npm test`. It was the
first implementation, and it is wrong here for two independent reasons.

1. **Security.** A ticket is an executable prompt already; anything that can
   create or start one has shell access on this machine. The board is reachable
   from the LAN behind a password. A ticket field that carried a command would
   make every ticket update a remote-code-execution primitive — one stolen
   session cookie away from `gate: curl … | sh`. The first implementation closed
   this by keeping `gate` out of the API's `allowed` set entirely, so a gate
   could only come from the file on disk.
2. **That fix made the feature unusable.** The owner of this tool does not use a
   terminal — a standing constraint of the project, not a preference. "The gate
   may only be written by editing the file by hand" plus "no gate, no dispatch"
   means he can never create a working opencode ticket. A feature that is secure
   and undeliverable is not finished.

Naming solves both at once:

    ticket:       gate: Tests laufen durch
    config.json:  "gates": {"/pfad/zur/werkbank":
                             {"Tests laufen durch": "python3 -m pytest tests/ -q"}}

The set of runnable commands is exactly what the owner wrote into `config.json`,
which no request can touch. Names cross the network; commands never do. `gate` is
therefore back in `allowed` — safely, because it is validated against
`GATE_NAME_RE` (letters, digits, space, `. _ -`, max 40) and can never be, or
become, a shell fragment. An unknown name resolves to nothing and the ticket is
refused: silently running a *different* check would defeat the mechanism.

Defence in depth, in order: the name cannot be a command (regex), the name is
only ever looked up (never executed), the lookup table is not reachable over
HTTP, and the board is served only the NAMES — `public_config` strips both the
commands and the password hash before the page ever sees the config.

## The flow

    opencode-task <project> < task     local GPU, free
    -> named check runs in <project>   zero Claude tokens
       GREEN -> bounded diff review    ~$0.056, one turn, tool-less, empty cwd
             -> status: review
       RED   -> one free retry, failing output fed back
             -> still red: assignee: claude, status: offen, both attempts kept

Endpoint unreachable (`opencode-task` exit 4) is infrastructure, not a ticket
failure: the ticket returns to `offen` with nothing recorded against it.

One wall-clock budget from `opencode_timeout_minutes` (default 60, twice the
claude default — WB-94: inheriting `agent_timeout_minutes` was a bug, local runs
are slower) covers both attempts and both
checks, so an opencode ticket cannot hold its lane open indefinitely. The
lanes run in PARALLEL (WB-92): a local run and a claude run of different
projects do not wait for each other.

## The review, and why it runs in an empty directory

Measured by the coding_agent session: the same tool-less review costs 5 turns and
$0.117 when run inside the project, because CLAUDE.md, skills and plugin sync
load ~93k tokens before any work — and 1 turn and $0.056 from a context-free
directory, still catching a planted `a - b` against "liefert die Summe". The diff
arrives on stdin, so the project directory buys nothing. `--bare` is deliberately
NOT used: it forces `ANTHROPIC_API_KEY` auth and never reads OAuth, which would
break or re-bill a subscription account.

The diff is capped (60k chars, marked when truncated) for two reasons: an
uncapped diff is an uncapped bill, and a single argv element dies at ~128 KB
(measured: 100 KB ok, 130 KB `E2BIG`) — which is also why the prompt goes on
stdin rather than argv.

`review: nein` on a ticket skips the paid review. When the check genuinely runs
the behaviour, the marginal value of a diff review is small; when it only
type-checks, the review is the only thing between us and confident green.

## What this is not

A check is only as good as the ticket: a weak check produces confident green. A
tool-less reviewer sees the diff, not runtime behaviour — it catches
wrong-looking code, not wrong-behaving code. This is a filter, not a guarantee.
Both limits belong in the user's head, so they are in the manual too.

## Traps found by building it

* **The task went to argv, not stdin.** `opencode-task` reads the task from
  stdin and treats `$2` as the model id, so the whole German ticket body arrived
  as a model id and the local model worked on an EMPTY task — then failed its
  check twice and escalated, for entirely the wrong reason. Sixteen tests with an
  injected `run` sailed past it. **Dependency injection tests your logic and
  hides your interfaces.** There is now at least one real-subprocess test per
  external command.
* Under a stdin that is not `/dev/null`, that same bug HANGS instead of failing
  (`TASK=$(cat)` waits for EOF). The board's unit is `StandardInput=null`, which
  is the only reason it failed fast rather than blocking for the full timeout.
