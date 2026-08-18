# Examples

## `opencode-task` — the local-model launcher

Werkbank can hand a ticket to a model on your own machine instead of Claude
(`assignee: opencode`). It does that by running one command:

    opencode-task <project-dir> [model-id]   < task-text-on-stdin

**That command is not part of the board, on purpose.** Which local agent you
run, how you wake it and how you address it is your setup, not ours. What the
board depends on is only the shape of the conversation:

| | |
|---|---|
| `argv[1]` | absolute path of the project to work in |
| `argv[2]` | model id, optional and advisory |
| stdin | the task text, verbatim UTF-8, possibly tens of KB of markdown |
| stdout | the agent's **final text and nothing else** — copied into the ticket |
| stderr | diagnostics |
| exit 0 | success |
| exit 3 | project directory missing |
| exit 4 | model endpoint unreachable — the board returns the ticket to *Offen* and records no failure |
| exit 5 | ran, but produced no final text |
| exit 127 | the agent binary itself is not installed (the message names the variable to set) |

`opencode-task` in this folder is a **working reference implementation** you can
run as-is or rewrite. Standard library only.

### Use it

    cp examples/opencode-task ~/.local/bin/     # anywhere on the board's PATH
    chmod +x ~/.local/bin/opencode-task

    OPENCODE_BIN=opencode \
    OPENCODE_ENDPOINT=http://my-gpu-box:8000/v1 \
    OPENCODE_PROVIDER=my-provider \
      opencode-task /path/to/project <<< "Fix the failing test in foo.py"

| variable | meaning |
|---|---|
| `OPENCODE_BIN` | the agent to run (default `opencode`) |
| `OPENCODE_ENDPOINT` | OpenAI-compatible base URL; if set, the launcher asks it which model it actually serves and uses that, so a stale id in a ticket cannot break dispatch |
| `OPENCODE_PROVIDER` | provider prefix for the model id, if your agent wants one |
| `OPENCODE_TIMEOUT` | seconds for the whole run (default 3600) |
| `OPENCODE_PROBE_TIMEOUT` | seconds to wait for the endpoint (default 60) |

The board still needs a **check** for the ticket — see
[the walkthrough](../docs/user/opencode-beispiel.md). No check, no dispatch:
a local model reports success over failing tests, so its own summary can never
be the acceptance criterion.

### Three things that are not obvious

They are in the reference implementation because leaving them out cost this
project real downtime:

1. **The task goes on stdin, never as an argument.** A single argv element dies
   at ~128 KB. A truncated or empty task looks exactly like a stupid model —
   that misdiagnosis happened three times before the cause was found.
2. **The agent's output must not use your own stdout/stderr.** If a background
   child of the agent inherits the pipe, the board waits for an end-of-output
   that never arrives: measured once as 19 minutes of a stalled queue behind a
   run that had finished. Write to files, then print.
3. **Nothing may outlive the launcher.** It puts the agent in its own process
   group and ends the group before exiting.

`tests/test_examples.py` runs this launcher against a stand-in agent and pins
all of it — including that its exit codes still match what
`src/werkbank/opencode.py` expects. If you rewrite the launcher, run those
tests against yours.

## `dsh-task` — the same contract, a second harness

`assignee: dsh` runs the **DeepSeek Harness** (`dsh`) against your local model.

> **You have to obtain that harness yourself.** This project does not ship it,
> does not install it and does not endorse a particular source — the launcher
> only wraps whatever `dsh` binary is on your `PATH` (or the one you name in
> `DSH_BIN`). If you do not have it, use `opencode` instead; the board treats
> the two identically. The board treats it **exactly like `opencode`**: one
code path, one exit-code mapping, one set of expectations. So `dsh-task` in
this folder answers the very same contract as the table above — only the
innards differ.

    cp examples/dsh-task ~/.local/bin/
    chmod +x ~/.local/bin/dsh-task

    DSH_BIN=dsh \
    DSH_ENDPOINT=http://my-gpu-box:8000/v1 \
      dsh-task /path/to/project <<< "Fix the failing test in foo.py"

| variable | meaning |
|---|---|
| `DSH_BIN` | the harness to run (default `dsh`) |
| `DSH_PROFILE` | profile to use (default `headless`) |
| `DSH_NODE_BIN` | a Node executable to put first on PATH (see below) |
| `DSH_ENDPOINT` | OpenAI-compatible base URL; if set, the launcher asks it which model it actually serves |
| `DSH_TIMEOUT` | seconds for the whole run (default 3600) |
| `DSH_PROBE_TIMEOUT` | seconds to wait for the endpoint (default 60) |

Both local workers share ONE lane in the board, because they share one GPU:
a `dsh` ticket waits while an `opencode` run is going, and the other way
round. And both need a **check** on the ticket — no check, no dispatch.

### Four things this launcher does that yours will have to do as well

Every one of them was found by a run that failed in a way naming something
else entirely:

1. **The harness takes its task only as a command-line argument** and does not
   read stdin — while the board's contract forbids argv, because a single
   element dies at ~128 KB. The launcher therefore writes the task into a file
   inside the project (`.dsh-task-<pid>.md`, removed again afterwards) and
   passes only a reference to it. The board never sees this.
2. **The sandbox derives its workspace root from the current directory.** The
   same call worked from one directory and failed from another. The launcher
   chdirs into the project itself — do not wrap it in your own `cd` trick.
3. **Without `HOME` the harness cannot find its profiles** and reports a
   loader error that reads like a broken installation. It is a missing
   variable.
4. **It needs a current Node.** A minimal environment can put an old `node`
   first on PATH, and the error message names neither Node nor the version.
   `DSH_NODE_BIN` puts the right one in front.

`tests/test_dsh_example.py` runs this launcher against a stand-in and pins all
of it — including that the task never appears in the command line, that the
spool file is cleaned up even when the agent fails, and that the exit codes
still match the board's.
