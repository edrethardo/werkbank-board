---
title: Windows release plan (WB-160)
date: 2026-08-16
tags: [decision, docs]
summary: The current 1.0 release claims Windows support and CI coverage; Windows CI has been red on every push (52+ ERRORs then a subprocess timeout kills the run), and the release pipeline does not check either job before READY. This plan enumerates the categories of breakage, proposes two honest paths (drop the claim OR earn it), and names a decision point.
---

# Windows release plan (WB-160)

> **Resolved on 2026-08-17 (WB-182): option 2 — the claim was earned.** Windows
> CI has been green since, and macOS was added the same day (WB-184). Everything
> below describes the situation as it WAS; it is kept because the categories of
> breakage and the reasoning are still the map. Do not read the present tense
> below as current state.

## Where we actually are

- **Every `.github/workflows/tests.yml` run on `main` is red.** Sample: run
  31970986858 (2026-08-16, HEAD then). Ubuntu: `Ran 373 tests in 23.5s,
  OK (skipped=1)`. Windows: 473 ok, **52 ERROR, 3 FAIL**, then the whole
  Python process died with `KeyboardInterrupt` in `subprocess._wait` at
  `tests/test_dispatch.py:2577` — the CI job's own timeout killed a
  hanging test.
- **The README says the opposite.** `README.md:88-92` and `:124` claim the
  suite "runs on both ubuntu-latest and windows-latest" and that things
  are "exercised by CI on Linux and Windows". Both are technically true
  ("it runs") and materially false ("it passes"). A reader takes the
  latter meaning.
- **`docs/dev/os-coverage.md:15` contradicts the README** — "Windows: Init
  path reviewed on paper only — VERIFY ON FIRST REAL DEPLOYMENT."
- **The release pipeline does not gate on Windows CI.**
  `scripts/publish-clean-copy.py` runs the tests INSIDE the export, but
  only on the maintainer's Linux box. `README` describes `publish
  → sync → tag`; neither step checks the workflow's Windows job.

## Categories of Windows breakage

### A. Test suite (~52 ERRORs, 3 FAILs, then hang)

Root causes, in order of prevalence in the grep audit:

1. **Unix-only stub binaries.** ~15 tests write `#!/bin/sh\n…` stand-ins
   and spawn them (`test_dispatch.py:285,320,788,1116,1298,1789,2851,
   2962,2994`, `test_opencode.py:175,499,556,592`). Windows can't run
   shebangs; three classes are `@skipIf(os.name == "nt", "Attrappen sind
   sh-Skripte")` already, most aren't.
2. **Bare Unix commands via subprocess.** `subprocess.Popen(["sleep",
   "30"], …)` at `test_dispatch.py:2589,2823`, `subprocess.run(
   ["/bin/echo", "x" * 200000], …)` at `test_opencode.py:215`. No
   `sleep.exe` on Windows → FileNotFoundError, or worse: PowerShell
   `sleep` alias with different semantics.
3. **POSIX process handling.** `SIGTERM`/`SIGKILL`, process groups, and
   `/proc/<pid>/environ` lookups. `dispatch.py:498,805` and
   `opencode.py:123` acknowledge the gap; tests don't.
4. **Hardcoded POSIX paths.** `/tmp/…`, `/proc/…`, `/bin/…`,
   `/usr/…` sprinkled through both `src/` and `tests/`. `board.html:919,
  (Historisch: der Dialog nannte damals einen /tmp-Pfad. Seit 2026-08-22
  liefert der Server das echte Protokoll-Verzeichnis mit — WB-263.)
   the runtime actually writes to `%LOCALAPPDATA%\werkbank\logs\` per
   `dispatch.py:498`.
5. **Test cleanup hangs.** `_reap` (`test_dispatch.py:2577`) waits on a
   stub that was never actually spawned; the wait then blocks the whole
   suite until the job-level timeout kills Python.

### B. Runtime feature gaps (documented, not fixed)

- **Chat-handover watcher is `bash`** (README:127-128). On Windows the
  handover silently falls back to a background run — no interactive
  path, no error, no signal that something was skipped.
- **opencode check is `/bin/sh -c`** (`opencode.py:187`, README:130-131).
  opencode tickets cannot be gated on Windows at all.
- **No Windows start command beyond `py -3 …`.** No shortcut, no service
  wrapper, no auto-restart. Windows autostart uses the Startup folder,
  described in README:235 in one sentence.

### C. Release-pipeline gaps

- **`publish-clean-copy.py` never touches Windows.** The `run_tests(out)`
  gate at `scripts/publish-clean-copy.py:354` is `python3 -m unittest`
  on the maintainer's machine. A tag can go out with `windows-latest`
  bright red for weeks; the publish script wouldn't notice.
- **The workflow's `fail-fast: false`** keeps Linux visibly green even
  when Windows fails — good for diagnosis, bad for anyone glancing at
  the badge (there is no badge yet, but if one is added it will point
  at the overall red state and hide the Linux truth).
- **No commit-level gate** ties HEAD to a passing Windows run before the
  publish script is even allowed to build.

### D. Documentation truth

- README §Portability implies passing Windows CI; that must either
  become true or the claim must go.
- `docs/dev/os-coverage.md` is right and stale — it hasn't tracked the
  README's escalation.

## The plan — two honest options; the user picks

### Option 1 — drop the Windows claim (recommended default)

Cheapest and most honest given "the maintainer has no Windows machine"
(README:91). One release cycle:

1. **README §Portability rewrite.** State "Linux and macOS. Windows is
   best-effort; the current CI job is red and the maintainer has no
   Windows machine to reproduce on. See `docs/dev/os-coverage.md`."
2. **Workflow trim.** Remove `windows-latest` from the matrix so `main`
   is honestly green.
3. **User-doc trim.** Drop the `py -3 …` line from step 2 and the
   Windows Startup instructions from §6 (or move them into a labelled
   "unverified" appendix that says the same thing as the README).
4. **`os-coverage.md` update.** Windows: "Not covered — see
   docs/dev/windows-release-plan.md for the path back."
5. **Publish gate.** `publish-clean-copy.py` reads the workflow's latest
   status via `gh run list --branch=main --workflow=tests.yml
   --limit=1` and refuses READY when it isn't `success`.

Effort: half a day. Blast radius: strangers who wanted Windows get a
truthful "no", not a broken "yes". Reversible: put Windows back into
the matrix the moment Option 2 lands.

### Option 2 — actually earn the Windows claim

Only worth doing when a real Windows user is on the line. Multi-day
work, needs a real Windows machine to verify (a CI green run is not
enough — several of the runtime gaps in section B can't be seen by the
suite).

Phased so each phase can be shipped on its own:

- **2a — Test hygiene (1–2 days, no runtime change).**
  - Replace every `#!/bin/sh` stand-in with a `.py` stand-in invoked as
    `[sys.executable, str(stub_py)]`. Cross-platform by construction.
  - Replace `["sleep", …]` with `[sys.executable, "-c", "import time;
    time.sleep(30)"]`. Same for `/bin/echo` etc.
  - Replace `/tmp/` and `/proc/` literals with
    `Path(tempfile.gettempdir())` and a helper that returns "" on
    Windows for tests that grep an environ dump.
  - Fix `_reap` to notice `poll() is not None` before waiting — the
    hang is the reason the CI job is timed-out, not just red.
  - Keep `@skipIf(os.name == "nt", …)` on the handful of tests that
    genuinely exercise POSIX signals / process groups, with a comment
    that names the runtime gap they cover.
- **2b — Runtime parity (2–3 days, needs a real Windows box).**
  - Rewrite the handover watcher in Python (one file under
    `.claude/skills/_user-level/werkbank-pull-ticket/`), invoked the
    same way on both platforms. Retires the `bash` loop.
  - Serve the log path from `/api/tickets` config payload instead of
    hardcoding `/tmp/…` in `board.html:919,1361`.
  - Add a documented Windows start: either a `werkbank.bat` in the
    repo root that calls `py -3 src\werkbank\server.py`, or a
    `python -m werkbank` entry point with a `console_scripts` shim.
  - Autostart runbook: verified Startup-folder recipe with screenshots
    (`docs/user/…`), not a one-liner.
- **2c — Release gate (0.5 day).**
  - `publish-clean-copy.py` refuses READY unless the last workflow
    run for HEAD on both `ubuntu-latest` AND `windows-latest` is
    `success`. Named as a self-check right next to the existing
    binary/pyc gates.
- **2d — CHANGELOG / README truth.** Only after 2a–2c are actually
  green on a real Windows machine (not "green on CI"):
  README §Portability keeps its current claim; `os-coverage.md` gets
  its first honest "Windows: verified".

## Decision point

Both paths need the same first action: **stop the README from lying**.
That is one PR, worth doing before anything else regardless of which
path we take.

The choice between Option 1 and Option 2 is a use-case question, not a
technical one:

- No known Windows user right now → **Option 1**. Cheap, honest,
  reversible.
- A real Windows user is asking for it → **Option 2**. Budget for a
  week of work AND a Windows machine to test on.

## Explicitly out of scope

- Rewriting the tests to run under WSL. That is macOS-with-extra-steps;
  it does not earn the Windows claim.
- Cross-platform installers (msi/pkg). One-file `.bat` is enough; every
  step beyond it is package-manager cargo cult.
- Fixing individual Windows-only user bug reports. There aren't any;
  this plan is about preventing the first one.
