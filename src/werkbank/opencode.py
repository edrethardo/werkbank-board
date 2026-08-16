"""Work a ticket with the local model, and let a GATE decide whether it worked.

A local model reports success over failing tests — measured repeatedly on this
machine. So its own summary can never be the acceptance criterion. Every
opencode ticket carries a gate: a shell command that must pass. No gate, no
dispatch.

Flow: opencode implements -> gate runs -> green means review, red means one free
retry with the failing output fed back, and a second red escalates to Claude.
"""

from __future__ import annotations

import os
import pathlib
import shutil
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
                 cwd=None):
    """subprocess.run stand-in that owns the WHOLE process group (WB-94).

    `subprocess.run(timeout=…)` kills only the direct child; the WB-92 incident
    left `opencode-task`'s children alive and editing the repo after the abort
    (and the surviving grandchild held the stdout pipe open, so the "kill" also
    blocked). Each run gets its own session; a timeout signals the group:
    TERM, short grace, KILL — then the TimeoutExpired is re-raised. On
    platforms without process groups (Windows) it degrades to killing the
    direct child, which is what subprocess.run did anyway."""
    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text,
        start_new_session=hasattr(os, "killpg"))
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise


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


def run_task(t, task_text: str, run=None, timeout=None):
    """Hand the task to the local model. Returns (returncode, final text).

    The task goes on STDIN. It must NOT be argv: `opencode-task` reads the task
    with `$(cat)` and treats its second argument as the MODEL ID, so passing the
    text positionally sends an empty task and silently mislabels the model.
    """
    run = run or _run_grouped
    proc = run([OPENCODE_TASK, getattr(t, "project", "")], input=task_text,
               capture_output=True, text=True,
               timeout=timeout if timeout is not None else TASK_TIMEOUT)
    return proc.returncode, (getattr(proc, "stdout", "") or "").strip()


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
    return [CLAUDE_BIN, "-p", "--model", REVIEW_MODEL,
            "--disallowedTools", REVIEW_TOOLS_OFF,
            "--append-system-prompt",
            "Du hast KEINE Tools. Antworte in einem Zug allein aus dem Text."]


def review_diff(criteria: str, diff: str, run=subprocess.run):
    """One bounded, tool-less turn in an empty directory. (verdict, truncated)."""
    clipped, truncated = clip_diff(diff)
    with tempfile.TemporaryDirectory(prefix="werkbank-review-") as sandbox:
        proc = run(review_command(), cwd=sandbox,
                   input=review_prompt(criteria, clipped),
                   capture_output=True, text=True, timeout=REVIEW_TIMEOUT)
    return (getattr(proc, "stdout", "") or "").strip(), truncated


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


def work_ticket(t, cfg: dict, run=None, on_progress=None) -> Outcome:
    run = run or _run_grouped
    name, gate = resolve_gate(t, cfg)
    if not gate:
        return Outcome(result=no_gate_message(t, cfg), status="fehlgeschlagen")
    try:
        return _work_ticket_inner(t, cfg, run, on_progress, name, gate)
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


def _work_ticket_inner(t, cfg: dict, run, on_progress, name, gate) -> Outcome:

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

    def left():
        return max(1.0, deadline - time.monotonic())

    for attempt in (1, 2):
        attempts = attempt
        say(f"opencode, Versuch {attempt}")
        try:
            rc, text = run_task(t, task, run=run, timeout=left())
        except (FileNotFoundError, NotADirectoryError):
            # WB-52: the wrapper is a local install, not part of this project.
            # Without this, a fresh checkout answers "interner Fehler der
            # Werkbank" — which blames the board for a missing prerequisite.
            return Outcome(
                result=(f"Nicht gestartet — das Hilfsprogramm `{OPENCODE_TASK}` "
                        "wurde auf diesem Rechner nicht gefunden. Der Bearbeiter "
                        "`opencode` braucht ein lokales Modell und dieses "
                        "Startprogramm; ohne das kann nur `claude` arbeiten. "
                        "Ticket auf `claude` umstellen oder das Programm "
                        "einrichten."),
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
            verdict, truncated = review_diff(getattr(t, "body", ""), diff, run=run)
            if verdict:
                note = ("\n\n_Hinweis: Der Diff war zu gross und wurde gekuerzt — "
                        "das Review deckt nur den Anfang ab._" if truncated else "")
                transcript.append(f"### Review (Claude, nur Diff)\n{verdict}{note}")
        else:
            transcript.append("### Review\nUebersprungen — der Lauf hat weder "
                              "bestehende Dateien geaendert noch neue angelegt.")
    return Outcome(result="\n\n".join(transcript), status="review", attempts=attempts)
