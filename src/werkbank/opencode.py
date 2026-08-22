"""Work a ticket with the local model, and let a GATE decide whether it worked.

A local model reports success over failing tests — measured repeatedly on this
machine. So its own summary can never be the acceptance criterion. Every
opencode ticket carries a gate: a shell command that must pass. No gate, no
dispatch.

Flow: opencode implements -> gate runs -> green means review, red means one free
retry with the failing output fed back, and a second red escalates to Claude.
"""

from __future__ import annotations

import inspect
import json
import os
import pathlib
import shutil
import signal
import subprocess
import time
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# The verified wrapper: stdin carries the task, stdout is the final text only,
# exit 4 means the endpoint is unreachable (infrastructure, not ticket failure).
# WB-52: paths are resolved at call time via _resolve_bin so a publish never
# ships a private $HOME path hardcoded (WB-47 was the same mistake in skills).


def _resolve_bin(name: str, which=shutil.which, candidates=None):
    """Find an executable. PATH first; then the usual install locations;
    otherwise the plain name so subprocess still tries the PATH lookup at
    call time (and fails with a clean error, not with 'a private $HOME path')."""
    found = which(name)
    if found:
        return found
    if candidates is None:
        candidates = [Path.home() / ".local" / "bin" / name,
                      Path(f"/usr/local/bin/{name}"),
                      Path(f"/usr/bin/{name}")]
    for path in candidates:
        if Path(path).exists():
            return str(path)
    return name


# WB-52: hardcoded 'a private $HOME path' strings would leak into every published
# copy. Resolved once at import via PATH + the usual install locations.
OPENCODE_TASK = _resolve_bin("opencode-task")
# WB-219: the DeepSeek harness is the second runner of the LOCAL lane. Its
# wrapper was written to the SAME contract as opencode-task on purpose (task
# on stdin, the final text and nothing else on stdout, exit 0/3/4/5), so the
# board keeps ONE code path and ONE exit-code mapping instead of two. What
# differs lives inside the wrapper — dsh reads its task only from argv, so
# dsh-task spools stdin to a file and passes the reference; the board never
# sees that.
DSH_TASK = _resolve_bin("dsh-task")
CLAUDE_BIN = _resolve_bin("claude")
ENDPOINT_DOWN = 4
BAD_DIRECTORY = 3
NO_FINAL_TEXT = 5
# Anything else non-zero means the WRAPPER or opencode itself did not complete —
# not that the model did poor work. Measured 2026-08-16: a task too large for
# argv exited 126, which this code used to read as "ran, produced nothing" and
# then escalated as "twice red", i.e. it blamed the model for a tooling limit.
# The gate still decides SUCCESS; the exit code decides who to blame for FAILURE.

# Running the reviewer inside the project loads CLAUDE.md, skills and plugins:
# measured 93k tokens and 5 turns versus 14.7k and 1 turn from an empty
# directory. The diff arrives on stdin, so the project directory buys nothing.
REVIEW_TOOLS_OFF = "Bash Edit Write Read Glob Grep Task WebFetch WebSearch"
REVIEW_MODEL = "sonnet"
GATE_TIMEOUT = 1800
TASK_TIMEOUT = 3600
REVIEW_TIMEOUT = 300
MAX_GATE_OUTPUT = 4000
MAX_DIFF = 60000          # well under MAX_ARG_STRLEN, and bounds the review bill


@dataclass
class Outcome:
    result: str
    status: str
    changes: dict = field(default_factory=dict)
    attempts: int = 0


def project_gates(project: str, cfg: dict) -> dict:
    """{name: command} configured for this project. Never ticket-supplied."""
    gates = (cfg.get("gates") or {}).get(project)
    if isinstance(gates, dict):
        return {k: v for k, v in gates.items() if isinstance(v, str) and v.strip()}
    # A bare string is the old per-project default: keep it working under a name.
    if isinstance(gates, str) and gates.strip():
        return {"standard": gates.strip()}
    return {}


def resolve_gate(t, cfg: dict):
    """(name, command) or (None, None).

    The ticket NAMES a gate; the command behind the name lives in config.json.
    Nothing that crossed the network is ever executed: the set of runnable
    commands is exactly what the owner wrote into config.json. An unknown name
    resolves to nothing on purpose — quietly running a different gate would
    defeat the mechanism it exists to be."""
    project = getattr(t, "project", "") or cfg.get("default_project", "")
    gates = project_gates(project, cfg)
    wanted = (getattr(t, "gate", "") or "").strip() or "standard"
    command = gates.get(wanted)
    if not command:
        return None, None
    return wanted, command.strip()


def _tail(text: str, limit: int = MAX_GATE_OUTPUT) -> str:
    text = text or ""
    return text if len(text) <= limit else "…\n" + text[-limit:]


def _run_grouped(cmd, input=None, capture_output=True, text=True, timeout=None,
                 cwd=None, env=None, on_pid=None):
    """subprocess.run stand-in that owns the WHOLE process group (WB-94).

    `subprocess.run(timeout=…)` kills only the direct child; the WB-92 incident
    left `opencode-task`'s children alive and editing the repo after the abort
    (and the surviving grandchild held the stdout pipe open, so the "kill" also
    blocked). Each run gets its own session; a timeout signals the group:
    TERM, short grace, KILL — then the TimeoutExpired is re-raised. On
    platforms without process groups (Windows) it degrades to killing the
    direct child, which is what subprocess.run did anyway.

    WB-135: on the SUCCESSFUL path too — after `communicate()` returned, the
    direct child is dead, but any grandchild spawned in this session can
    survive it. In production this kept an opencode-task descendant alive for
    minutes after its ticket was marked review, holding the GPU and the lane's
    worker. Signalling the group unconditionally reaps them; on an already-empty
    group `killpg` raises ProcessLookupError and is caught. The tiny PID-reuse
    window (a fresh process happens to have adopted this pgid in the microseconds
    between communicate returning and the killpg call) is preferable to a
    lingering grandchild that keeps the lane blocked."""
    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text,
        env=env,
        start_new_session=hasattr(os, "killpg"))
    pgid = proc.pid   # start_new_session=True makes the child its own pgid
    if on_pid is not None:
        try:
            on_pid(proc.pid)
        except Exception:
            pass
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise
    finally:
        if hasattr(os, "killpg"):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass


def _kill_group(proc, grace=2.0):
    """TERM the group, give it a moment, then KILL. Never raises."""
    def signal_group(sig):
        try:
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(proc.pid), sig)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
    import signal as _signal
    signal_group(_signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        signal_group(_signal.SIGKILL)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def run_gate(gate: str, project: str, run=None, timeout=None):
    """(passed, combined output). Uses a shell because gates are written as
    shell one-liners ('npm test && npm run lint')."""
    run = run or _run_grouped
    proc = run(["/bin/sh", "-c", gate], cwd=project, capture_output=True,
               text=True, timeout=timeout if timeout is not None else GATE_TIMEOUT)
    out = (getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")
    return proc.returncode == 0, out


def run_task(t, task_text: str, run=None, timeout=None, on_pid=None,
             owner=None):
    """Hand the task to the local model. Returns (returncode, final text).

    The task goes on STDIN. It must NOT be argv: `opencode-task` reads the task
    with `$(cat)` and treats its second argument as the MODEL ID, so passing the
    text positionally sends an empty task and silently mislabels the model.

    WB-142: `WERKBANK_TICKET_ID` is set in the run's environment so a stray
    opencode process on the system can be traced back to the ticket that owns
    it — `cat /proc/<pid>/environ | tr '\\0' '\\n' | grep WERKBANK` is enough,
    no cmdline parsing (opencode-task and opencode share their argv shape).
    `owner` (the dispatching board's tickets dir) is stamped alongside as
    WERKBANK_TICKETS_DIR: ticket ids repeat across boards, so the id alone
    must never be enough for a reaper to claim — let alone kill — a run."""
    run = run or _run_grouped
    kwargs = _run_kwargs(run, timeout, on_pid, t, owner)
    proc = run([runner_for(getattr(t, "assignee", "")),
                getattr(t, "project", "")], input=task_text,
               capture_output=True, text=True, **kwargs)
    return proc.returncode, (getattr(proc, "stdout", "") or "").strip()


def _short_path(path: str) -> str:
    """A tilde-shortened home path instead of the absolute one. WB-263 put the resolved
    binary on the card so nobody has to guess which copy ran — and the first
    screenshot taken for the README showed the owner's full home path on a
    ticket. It is on his own machine either way, but it goes on every
    screenshot and every demo projector, so shorten what adds nothing."""
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home + "/") else path


def runner_for(assignee) -> str:
    """WB-219: which wrapper runs this ticket. Unknown names fall back to
    opencode-task — the caller has already passed `known_assignee`, and a
    silently wrong runner is worse than the known one.

    Reads the module attributes at CALL time, not through a dict built at
    import: tests point the lane at a stand-in by assigning
    `opencode.OPENCODE_TASK`, and a frozen mapping would ignore that — the
    first version of this function did, and the lane-self-heal test failed
    at once (which is exactly what that test is for)."""
    if (assignee or "").strip().lower() == "dsh":
        return DSH_TASK
    return OPENCODE_TASK


def _run_kwargs(run, timeout, on_pid, t, owner=None):
    """Only send `env`/`on_pid` to runners that accept them; fake runners in
    tests (subprocess.run, hand-crafted mocks) do not, and we still want the
    real runs to carry the identifying env even when a test injects its own
    `run=`. Signature-sniffed so injected fakes stay simple."""
    kwargs = {"timeout": timeout if timeout is not None else TASK_TIMEOUT}
    try:
        params = inspect.signature(run).parameters
    except (TypeError, ValueError):
        params = {}
    if "env" in params or _accepts_var_keywords(params):
        env = dict(os.environ)
        env["WERKBANK_TICKET_ID"] = getattr(t, "id", "") or ""
        if owner:
            env["WERKBANK_TICKETS_DIR"] = str(Path(owner).resolve())
        # WB-238: per-ticket dsh backend override. Only meaningful for the
        # dsh runner (opencode-task ignores the var; the store already
        # rejects a `backend:` set on non-dsh tickets, so we should never
        # reach here with backend=claude on an opencode ticket — the check
        # here is belt & braces). The wrapper reads DSH_TASK_BACKEND at
        # startup; empty / "local" means the wrapper's own default (the
        # local model), "claude" routes through the local claude CLI
        # and thus the subscription quota.
        backend = (getattr(t, "backend", "") or "").strip().lower()
        assignee = (getattr(t, "assignee", "") or "").strip().lower()
        if backend in ("local", "claude") and assignee == "dsh":
            env["DSH_TASK_BACKEND"] = backend
        kwargs["env"] = env
    if on_pid is not None and ("on_pid" in params or _accepts_var_keywords(params)):
        kwargs["on_pid"] = on_pid
    return kwargs


def _accepts_var_keywords(params) -> bool:
    return any(getattr(p, "kind", None) == inspect.Parameter.VAR_KEYWORD
               for p in params.values())


def clip_diff(diff: str):
    """(text, truncated). A diff over the cap is cut with a visible marker.

    Two reasons, both hard: a single argv element dies at ~128 KB
    (MAX_ARG_STRLEN) with OSError, and an uncapped diff is an unbounded bill —
    the whole cost case rests on bounded input.
    """
    if len(diff) <= MAX_DIFF:
        return diff, False
    return (diff[:MAX_DIFF]
            + "\n\n[... Diff nach {} Zeichen abgeschnitten ...]".format(MAX_DIFF)), True


def review_prompt(criteria: str, diff: str) -> str:
    return (
        "Pruefe diesen Diff gegen die Akzeptanzkriterien. "
        "Antworte in einem Zug: 'VERDICT: OK' oder 'VERDICT: PROBLEM' plus eine "
        "kurze Begruendung auf Deutsch.\n\n"
        "Akzeptanzkriterien:\n" + criteria + "\n\nDiff:\n" + diff
    )


def review_command() -> list:
    # No --bare: it forces ANTHROPIC_API_KEY auth and never reads OAuth, which
    # would break or re-bill a subscription account.
    # The prompt is NOT in argv — see clip_diff for why.
    # WB-170: `--output-format json` so the review reports its own bill —
    # the CLI's result event carries `total_cost_usd` and `usage`. Without
    # this the review is the one code path this project never measured.
    return [CLAUDE_BIN, "-p", "--model", REVIEW_MODEL,
            "--disallowedTools", REVIEW_TOOLS_OFF,
            "--output-format", "json",
            "--append-system-prompt",
            "Du hast KEINE Tools. Antworte in einem Zug allein aus dem Text."]


def _parse_review_output(stdout: str):
    """WB-170: split (text, usage-dict) from `claude -p --output-format json`.

    The CLI emits a single JSON object with `.result` (the model's answer),
    `.usage` (input/output/cache tokens) and `.total_cost_usd`. A parse
    failure treats the whole stdout as the text and returns `None` for
    usage — the review report still lands on the ticket, only the cost
    footer is missing that one time."""
    stdout = (stdout or "").strip()
    try:
        obj = json.loads(stdout)
    except (ValueError, TypeError):
        return stdout, None
    if not isinstance(obj, dict):
        return stdout, None
    text = str(obj.get("result", "") or "").strip() or stdout
    usage_raw = obj.get("usage") or {}
    if not isinstance(usage_raw, dict):
        usage_raw = {}
    cost = obj.get("total_cost_usd")
    usage = {
        "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
        "tokens_in": int(usage_raw.get("input_tokens") or 0),
        "tokens_out": int(usage_raw.get("output_tokens") or 0),
        "tokens_cache": int((usage_raw.get("cache_creation_input_tokens") or 0)
                            + (usage_raw.get("cache_read_input_tokens") or 0)),
    }
    return text, usage


def review_diff(criteria: str, diff: str, run=None):
    """One bounded, tool-less turn in an empty directory.
    Returns (verdict, truncated, usage) — usage is a dict per
    `_parse_review_output`, or None if the CLI produced non-JSON output.
    WB-171: default is `_run_grouped`, so a claude-CLI grandchild that
    outlives the direct child gets reaped with the group instead of
    holding stdout open past the REVIEW_TIMEOUT."""
    run = run or _run_grouped
    clipped, truncated = clip_diff(diff)
    with tempfile.TemporaryDirectory(prefix="werkbank-review-") as sandbox:
        proc = run(review_command(), cwd=sandbox,
                   input=review_prompt(criteria, clipped),
                   capture_output=True, text=True, timeout=REVIEW_TIMEOUT)
    text, usage = _parse_review_output(getattr(proc, "stdout", "") or "")
    return text, truncated, usage


def adversarial_review_prompt(ticket_body: str, diff: str) -> str:
    """WB-140: the on-demand button asks a HARDER reviewer than the automatic
    opencode gate — actively hunt for bugs, silent gaps, security issues,
    and dishonest reporting. One tool-less turn (see review_command).
    WB-172: hard 200-word ceiling on the output — 26 measured tickets
    averaged 21.6k output tokens because the prompt named neither format
    nor limit. The findings-with-file:line format is where the value is;
    the prose around them was pure cost."""
    return (
        "Du bist ein adversarialer Code-Reviewer. Du sollst NICHT bestätigen — "
        "such nach dem, was fehlt: übersehene Fälle, stille Zusagen im "
        "Ergebnistext ohne Codebeleg, Sicherheits- oder Nebenläufigkeits-"
        "Fallen, Tests die das Verhalten nur pinnen ohne es zu prüfen, "
        "Copy-Paste-Fehler.\n\n"
        "Antworte in ≤200 Wörtern. Jedes Finding als eine Zeile "
        "`- <file>:<line> — <konkretes Fehlerszenario in einem Satz>`. "
        "Keine Präambel, keine Schluss-Zusammenfassung, keine Wiederholung "
        "der Ticket-Beschreibung. Wenn nichts zu meckern ist: ein Satz. "
        "Antwort auf Deutsch.\n\n"
        "Ticket:\n" + ticket_body + "\n\nDiff:\n" + diff
    )


def adversarial_review(ticket_body: str, diff: str, run=None):
    """Fresh claude instance, no tools, adversarial system prompt. Returns
    (report_text, truncated, usage) — WB-170: usage is the parsed cost/token
    dict from `_parse_review_output`, or None on non-JSON output. Uses the
    same review_command as the automatic gate — the model choice and safety
    flags stay consistent. WB-171: default is `_run_grouped`, so a hanging
    grandchild past REVIEW_TIMEOUT does not keep `_REVIEWS_RUNNING`'s
    per-ticket lock held until the next server restart."""
    run = run or _run_grouped
    clipped, truncated = clip_diff(diff)
    with tempfile.TemporaryDirectory(prefix="werkbank-adv-review-") as sandbox:
        proc = run(review_command(), cwd=sandbox,
                   input=adversarial_review_prompt(ticket_body, clipped),
                   capture_output=True, text=True, timeout=REVIEW_TIMEOUT)
    text, usage = _parse_review_output(getattr(proc, "stdout", "") or "")
    return text, truncated, usage


def head_sha(project: str, run=subprocess.run):
    try:
        proc = run(["git", "-C", project, "rev-parse", "HEAD"],
                   capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    return (proc.stdout or "").strip() or None if proc.returncode == 0 else None


# Per new file, so one generated blob cannot crowd out everything else. The
# whole prompt is capped again by _clip_diff.
MAX_NEW_FILE = 20000


def untracked_files(project: str, run=subprocess.run) -> list:
    """New files, honouring .gitignore. `git diff` cannot see these, and a
    brand-new module is the most common outcome of a 'build X' ticket."""
    try:
        proc = run(["git", "-C", project, "ls-files", "--others",
                    "--exclude-standard"], capture_output=True, text=True,
                   timeout=60)
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return [line for line in (proc.stdout or "").splitlines() if line.strip()]


def _new_file_section(project: str, name: str) -> str:
    """A new file rendered so the reviewer sees it as an addition. We do NOT use
    `git add -N` for this: it writes to the index of a repository the user and
    other agents also work in, and a later `git commit -a` would then sweep up
    files nobody staged."""
    path = pathlib.Path(project) / name
    try:
        if not path.is_file() or path.is_symlink():
            return ""
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"\n--- NEUE DATEI: {name} (nicht lesbar oder binär) ---\n"
    if len(text) > MAX_NEW_FILE:
        text = text[:MAX_NEW_FILE] + "\n… (gekürzt)"
    body = "\n".join("+" + line for line in text.splitlines())
    return f"\n--- NEUE DATEI: {name} ---\n{body}\n"


def diff_since(project: str, sha, run=subprocess.run) -> str:
    """Everything that changed: tracked edits AND new files.

    Measured 2026-08-16 on the first live ticket: the run created a new module,
    `git diff <sha>` showed nothing, and the ticket recorded "kein Diff" — so
    the review, the one thing standing between us and confident-green garbage,
    silently did not run in exactly the case where the model wrote something
    entirely new. A quiet mechanism producing evidence that reads like a finding
    is the failure shape this project keeps meeting."""
    parts = []
    if sha:
        try:
            proc = run(["git", "-C", project, "diff", sha], capture_output=True,
                       text=True, timeout=60)
            if proc.returncode == 0 and (proc.stdout or "").strip():
                parts.append(proc.stdout)
        except Exception:
            pass
    for name in untracked_files(project, run=run):
        section = _new_file_section(project, name)
        if section:
            parts.append(section)
    return "\n".join(parts)


def budget_seconds(cfg: dict) -> float:
    """The opencode lane's OWN ceiling for everything a ticket does — both
    attempts and both gates together. `opencode_timeout_minutes`, default 60.

    WB-94: this used to inherit agent_timeout_minutes (30) from the Claude
    lane, which killed WB-92's run nine minutes before the (measurably slower)
    local model was done. Since WB-92 the lanes run independently, so the
    Claude limit has no business here."""
    try:
        minutes = float(cfg.get("opencode_timeout_minutes") or 60)
    except (TypeError, ValueError):
        minutes = 60.0
    return max(60.0, minutes * 60.0)


def no_gate_message(t, cfg: dict) -> str:
    """Refusal a non-technical owner can act on: name the choices he has, or
    tell him how to get one — never just 'no gate configured'."""
    project = getattr(t, "project", "") or cfg.get("default_project", "")
    known = sorted(project_gates(project, cfg))
    wanted = (getattr(t, "gate", "") or "").strip()
    if known and wanted:
        return (f"Nicht gestartet — die Pruefung \u201e{wanted}\u201c gibt es fuer dieses "
                f"Projekt nicht. Zur Auswahl stehen: {', '.join(known)}.")
    if known:
        return ("Nicht gestartet — dieses Ticket nennt keine Pruefung. Waehle im "
                f"Ticket eine aus: {', '.join(known)}.")
    return ("Nicht gestartet — fuer dieses Projekt ist keine Pruefung hinterlegt. "
            "Ohne Pruefung waere die Selbstauskunft des lokalen Modells das "
            "Abnahmekriterium, und die ist nachweislich nicht verlaesslich. Sag "
            "mir im Chat, woran man sieht, dass es in diesem Projekt funktioniert "
            "(z. B. \u201edie Tests laufen durch\u201c) — ich trage es ein.")


def _asks_a_question(text: str) -> bool:
    """WB-220: the model's raw text starts with the RÜCKFRAGE marker.
    Delegates to `dispatch.is_query_result` so both lanes share one
    definition of "the agent is asking, not answering". Lazy import:
    `dispatch` imports `opencode`, so an eager import would cycle."""
    from werkbank.dispatch import is_query_result
    return is_query_result(text)


def _rueckfrage_refusal(worker: str, text: str) -> str:
    """WB-220: the message that lands on a fehlgeschlagen local-lane ticket
    whose model asked a question. Names WHY (one-shot wrapper, no session to
    resume), WHAT the user's option is (`claude` is the only assignee that
    can pause), and preserves the model's own text so the question is not
    lost — the whole point of the ticket."""
    return (
        f"Nicht bearbeitet — der Bearbeiter `{worker}` kann keine Rückfragen "
        "stellen. Der Lauf hat mit einer Frage geantwortet statt zu arbeiten, "
        "und sein Wrapper startet jede Aufgabe frisch (keine Sitzung, die eine "
        "Antwort fortsetzen könnte). Deine Antwort ginge ins Nichts.\n\n"
        "Nur `claude` kann pausieren und nachfragen. Umstellen — oder die "
        "Frage vorab klären und das Ticket präziser fassen.\n\n"
        "Text des Laufs:\n" + text)


def work_ticket(t, cfg: dict, run=None, on_progress=None, on_pid=None,
                owner=None) -> Outcome:
    run = run or _run_grouped
    name, gate = resolve_gate(t, cfg)
    if not gate:
        return Outcome(result=no_gate_message(t, cfg), status="fehlgeschlagen")
    try:
        return _work_ticket_inner(t, cfg, run, on_progress, name, gate, on_pid,
                                  owner)
    except subprocess.TimeoutExpired:
        # WB-94: an exceeded budget is an honest, explained failure — not an
        # "interner Fehler der Werkbank" bubbling out of the worker.
        ceiling = int(budget_seconds(cfg) // 60)
        return Outcome(
            result=(f"Zeitlimit überschritten — das Ticket hat sein Zeitbudget "
                    f"von {ceiling} Minuten (opencode_timeout_minutes) "
                    "aufgebraucht; der Lauf und alle seine Prozesse wurden "
                    "beendet. Erneut versuchen startet frisch; wenn das "
                    "Zielprojekt groß ist, das Budget in config.json erhöhen."),
            status="fehlgeschlagen")


def _work_ticket_inner(t, cfg: dict, run, on_progress, name, gate,
                       on_pid=None, owner=None) -> Outcome:

    project = getattr(t, "project", "") or cfg.get("default_project", "")
    before = head_sha(project, run=run)
    task = f"{getattr(t, 'title', '')}\n\n{getattr(t, 'body', '')}".strip()
    transcript, attempts, gate_out = [], 0, ""
    deadline = time.monotonic() + budget_seconds(cfg)
    ceiling = int(budget_seconds(cfg) // 60)

    def say(step):
        # F4: an hour of silence on an in_arbeit card is the complaint this
        # tool already has. Report which step is running.
        if on_progress:
            try:
                on_progress(step)
            except Exception:
                pass

    # WB-219: which local worker this is — the progress line and the
    # "wrapper missing" message must not tell a dsh user to fix opencode.
    worker = (getattr(t, "assignee", "") or "opencode").strip().lower()

    def left():
        return max(1.0, deadline - time.monotonic())

    for attempt in (1, 2):
        attempts = attempt
        # WB-263: name the BINARY, not just the worker. `_resolve_bin` falls
        # back to the usual install locations when nothing is on PATH, so a
        # stale copy in ~/.local/bin can shadow the one the user thinks they
        # installed — and nothing in the ticket said which file had run.
        say(f"{worker} ({_short_path(runner_for(worker))}), "
            f"Versuch {attempt}")
        try:
            rc, text = run_task(t, task, run=run, timeout=left(), on_pid=on_pid,
                                owner=owner)
        except (FileNotFoundError, NotADirectoryError):
            # WB-52: the wrapper is a local install, not part of this project.
            # Without this, a fresh checkout answers "interner Fehler der
            # Werkbank" — which blames the board for a missing prerequisite.
            return Outcome(
                result=(f"Nicht gestartet — das Hilfsprogramm "
                        f"`{runner_for(worker)}` wurde auf diesem Rechner nicht "
                        f"gefunden. Der Bearbeiter `{worker}` braucht ein lokales "
                        "Modell und dieses Startprogramm; ohne das kann nur "
                        "`claude` arbeiten. Ticket auf `claude` umstellen oder "
                        "das Programm einrichten."),
                status="fehlgeschlagen", attempts=attempts)
        if rc == ENDPOINT_DOWN:
            # Infrastructure, not the ticket's fault: no failure is recorded.
            return Outcome(
                result=("Nicht gestartet — der lokale Modell-Endpunkt war nicht "
                        "erreichbar. Das Ticket bleibt offen und kann erneut "
                        "gestartet werden."),
                status="offen", attempts=attempts)
        if rc == BAD_DIRECTORY:
            return Outcome(
                result=(f"Nicht gestartet — das Projektverzeichnis `{project}` "
                        "gibt es nicht. Bitte im Ticket das richtige Projekt "
                        "auswählen."),
                status="fehlgeschlagen", attempts=attempts)
        if _asks_a_question(text):
            # WB-220: the local wrapper is one-shot — `dsh --profile headless`
            # and opencode-task each spawn a fresh conversation, so nothing
            # would carry the user's answer back to the model that asked. A
            # rueckfrage from here would vanish into a review-column result;
            # refuse the run instead, name the reason, and preserve the text.
            return Outcome(
                result=_rueckfrage_refusal(worker, text),
                status="fehlgeschlagen", attempts=attempts)
        tool_failed = rc not in (0, NO_FINAL_TEXT)
        transcript.append(f"### Versuch {attempt} (opencode)\n{text or '(keine Ausgabe)'}")
        say(f"Pruefung \u201e{name}\u201c laeuft (Versuch {attempt})")
        passed, gate_out = run_gate(gate, project, run=run, timeout=left())
        transcript.append(f"### Pruefung „{name}“ — {'gruen' if passed else 'rot'}\n"
                          f"```\n{_tail(gate_out)}\n```")
        if passed:
            break        # the check is green: the exit code does not get a vote
        if tool_failed:
            # Do not spend a second attempt, and do not call this "the model
            # failed" — it never got to try.
            transcript.append(
                f"**Der Lauf selbst ist gescheitert (Rückgabecode {rc}).** Das ist "
                "ein Problem des Werkzeugs oder des Auftrags, keine Aussage über "
                "das lokale Modell. Prüfung wurde trotzdem ausgeführt und blieb rot.")
            return Outcome(result="\n\n".join(transcript),
                           status="fehlgeschlagen", attempts=attempts)
        if attempt == 1:
            # Rung 3 of the escalation ladder: hand it the failing symptom
            # before concluding it cannot do the job. This retry is free.
            task = (f"{task}\n\n---\nDein vorheriger Versuch hat die Pruefung "
                    f"\u201e{name}\u201c NICHT bestanden. Ausgabe:\n{_tail(gate_out)}\n\n"
                    "Behebe die Ursache und sorge dafuer, dass sie gruen wird.")
    else:
        transcript.append(
            "**Zweimal rot — an Claude eskaliert.** Beide Versuche und die "
            "Pruefausgabe stehen oben; sie ist meist die Ursache. "
            f"(Zeitbudget fuer das ganze Ticket: {ceiling} Minuten.)")
        return Outcome(result="\n\n".join(transcript), status="offen",
                       changes={"assignee": "claude"}, attempts=attempts)

    if (getattr(t, "review", "") or "").strip().lower() != "nein":
        diff = diff_since(project, before, run=run)
        if diff.strip():
            say("Review (Claude, nur Diff)")
            verdict, truncated, _usage = review_diff(
                getattr(t, "body", ""), diff, run=run)
            if verdict:
                note = ("\n\n_Hinweis: Der Diff war zu gross und wurde gekuerzt — "
                        "das Review deckt nur den Anfang ab._" if truncated else "")
                transcript.append(f"### Review (Claude, nur Diff)\n{verdict}{note}")
        else:
            transcript.append("### Review\nUebersprungen — der Lauf hat weder "
                              "bestehende Dateien geaendert noch neue angelegt.")
    return Outcome(result="\n\n".join(transcript), status="review", attempts=attempts)
