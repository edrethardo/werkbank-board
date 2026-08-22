---
name: create-ticket
description: Use when the user asks to create a ticket in chat — "erstelle ein Ticket", "leg ein Ticket an", "schreib das als Ticket auf", "mach daraus ein Ticket", "das sollten wir als Ticket festhalten".
version: 6
---

# Create a Ticket from Chat

Turn what the user just described into a correctly formatted ticket on the board.
ALWAYS go through `store.create_ticket` — never write a ticket file by hand; the
store owns id numbering, filename slugs, and the file format.

NEVER create a ticket from an isolated copy of this repo (git worktree, review
clone, scratch checkout): its `tickets/` is frozen at fork time, so it assigns a
number the live board may already have given away — that is exactly how two
tickets ended up sharing WB-93 (WB-101). File tickets in the LIVE board repo, or
hand the request to the session that owns it.

## 1. Gather the fields — ask only for what's missing

From the conversation, fill as much as possible; ask for the rest in plain German,
one short question at a time (AskUserQuestion or plain text):

- **title** — required. Draft it yourself from the user's words; a good title says
  the outcome, not the activity.
- **description** — required in practice: what should exist afterwards, and any
  acceptance criteria the user states. If the user gave only a title-sized wish,
  ask ONE question ("Woran erkennst du, dass es fertig ist?") rather than padding.
- **type** — `aufgabe` (default), `bug`, or `epic` (WB-161). If the user is
  reporting broken behavior, prefer the `werkbank-report-bug` flow (it asks
  repro questions); a package big enough that it will have to be broken up
  into several tickets → `epic` (it is planned in a chat session, and its
  children carry `epic: WB-<parent>`); otherwise `aufgabe`.
- **priority** — `hoch` | `normal` (default) | `niedrig`. Infer from urgency
  words; only ask when the user signals urgency but you cannot rank it.
- **project** — absolute path. Default: `default_project` from `config.json`.
  The named project list lives in `config.json` under `projects` (name → path,
  WB-24): when the user says a project NAME, resolve it there; unknown name →
  ask instead of guessing. A raw path is fine too — verify it exists before
  creating. New projects can be registered via
  `werkbank.projects.add_project(config_path, name, path)`.
- **assignee** — estimate who SHOULD work it (user request, 2026-08-16) and set
  it; the user overrides by naming one. Recommend `opencode` only when ALL of
  these hold, otherwise `claude`:
  1. Small and isolated — new files or one well-bounded change, no cross-module
     refactor, no security-sensitive or concurrency-sensitive code.
  2. A named check exists for the project in config.json `gates` (an opencode
     ticket without a gate refuses to start — check BEFORE recommending, and
     set the ticket's `gate` field to the check's name).
  3. Nobody is waiting: priority is `niedrig`, or the user signalled "irgendwann/
     nebenbei". The local lane is 5–10× slower (measured: WB-102, a ~20-line
     script, ≈20 min) but costs no quota and runs beside the Claude lane.
  Borderline → `claude`; a failed opencode attempt that escalates costs more
  than starting with Claude (measured: WB-92).

Do NOT interrogate the user through all six fields — sensible defaults beat a
questionnaire. One confirmation question maximum when everything was inferable.

## 1a. Gate for opencode — resolve it BEFORE creating (WB-166)

If (and only if) you are about to recommend `assignee=opencode`, resolve the
gate now. The ticket must carry a valid `gate:` name before it lands on the
board — otherwise the first dispatch fails with the German refusal from
`opencode.no_gate_message`. Read the project's gates from config.json:

```bash
WB_PROJECT="<absolute project path>" python3 - << 'EOF'
import json, os, sys; sys.path.insert(0, "src")
from werkbank import opencode
cfg = json.load(open("config.json"))
print("\n".join(sorted(opencode.project_gates(os.environ["WB_PROJECT"], cfg)))
      or "(keine)")
EOF
```

Then decide:

- **Exactly one gate configured** → use its name; mention it in the confirmation
  ("empfohlen: opencode — geprüft über „<name>"").
- **Several gates configured** → ask the user which one belongs to this ticket;
  do NOT guess (a wrong gate greens on unrelated evidence).
- **No gate configured for this project** → do NOT create an opencode ticket
  yet. Offer BOTH honest paths to the user in one sentence:
  1. **Fall back to `claude`** and create the ticket now (fastest path).
  2. **Add a gate first**: ask the user for a name (like „Tests laufen durch")
     and the shell command that proves this class of work is done in this
     project, then persist it before creating the ticket:

     ```bash
     WB_PROJECT="<abs path>" WB_GATE_NAME="Tests laufen durch" \
     WB_GATE_CMD="python3 -m pytest tests/ -q" python3 - << 'EOF'
     import json, os
     cfg = json.load(open("config.json"))
     cfg.setdefault("gates", {}).setdefault(os.environ["WB_PROJECT"], {})[
         os.environ["WB_GATE_NAME"]] = os.environ["WB_GATE_CMD"]
     open("config.json", "w").write(json.dumps(cfg, indent=2) + "\n")
     EOF
     ```

     Then set `gate=<the new name>` on the ticket. Do this only when the
     board is not actively working an opencode ticket for this project — a
     concurrent gate write can otherwise race the reader.

The claude lane does NOT require a gate; leave the ticket's `gate` field empty
for `assignee=claude` unless the user explicitly asks for one.

## 1b. opencode-Modus — Pflicht, sobald `assignee: opencode`

opencode ist ein **kleines lokales Modell**. Auf eine Lücke im Ticket reagiert es
nicht mit einer Rückfrage, sondern mit einer Vermutung — und eine Vermutung, die
zweimal am Gate scheitert, eskaliert an Claude. Das kostet mehr, als das Ticket
gleich ordentlich zu schreiben (gemessen: WB-92).

Ein opencode-Ticket MUSS deshalb so aussehen:

1. **Nummerierte Schritte.** Alle Design-Entscheidungen vorher treffen und
   hinschreiben. Keine offenen Fragen im Ticket.
2. **Exakte Namen und Pfade** — Datei, Funktion, Flag, Typ. Auf ein vorhandenes
   Muster zeigen, das man kopieren kann: „GENAU wie `src/bar.py` als Vorlage".
3. **Abschnitt `## Tests / Abnahme`** mit **kopierbaren Kommandos UND erwartetem
   Ergebnis** — nicht „Tests schreiben", sondern
   `python3 -m pytest tests/test_foo.py -q   # erwartet: 3 passed, exit 0`.
4. **Abschnitt `## Fertig, wenn`** als Haken-Liste.
5. **Ein Ticket, eine Verantwortung.** Zu groß → aufteilen.
6. **`gate:` setzen** — der Name der Prüfung aus `config.json` → `gates` →
   `<Projektpfad>`. Hat das Projekt keine, ins Ticket schreiben, dass eine
   angelegt werden muss (ohne Gate startet das Board ein opencode-Ticket nicht).
7. **Die Tests müssen in der Umgebung grün sein, in der opencode wirklich
   läuft.** Braucht der Test eine GPU oder eine optionale Abhängigkeit, die dort
   fehlt, ist das Gate unerreichbar und das Ticket eskaliert endlos — dann
   entweder einen CPU-Weg testen oder das Ticket an `claude` geben.

Vor dem Anlegen prüfen (fängt die strukturellen Auslassungen, nicht die Qualität):

    WB_GATE="Tests laufen durch" python3 - <<'EOF'
    import os, sys; sys.path.insert(0, "src")
    from werkbank import store
    draft = open("/tmp/entwurf.md", encoding="utf-8").read()
    # gate= ist die Prüfung, die du in §1a ausgewählt hast — der Entwurf ist
    # nur Text und trägt sie noch nicht.
    print(store.opencode_ticket_gaps(draft, gate=os.environ["WB_GATE"])
          or "vollständig")
    EOF

Kurzbeispiel für die Beschreibung:

    1. Lege `src/luna/cuda_blur.py` an, GENAU wie `src/luna/blur.py` als Vorlage.
    2. Ergänze `blur_gpu(frame: np.ndarray, radius: int) -> np.ndarray`.
    3. Trage in `config.json` nichts ein — die Umschaltung kommt in WB-193.

    ## Tests / Abnahme

        python3 -m pytest tests/test_cuda_blur.py -q    # erwartet: 4 passed, exit 0
        python3 -m luna.blurbench --cpu                 # läuft ohne GPU durch, exit 0

    ## Fertig, wenn

    [ ] `blur_gpu` existiert und fällt ohne CUDA auf den CPU-Weg zurück
    [ ] beide Kommandos oben laufen grün

Vorlagen aus der Praxis: WB-193, WB-194, WB-195 im Luna-Projekt.

## 2. Create it

```bash
# Values go through the ENVIRONMENT, never substituted into the snippet —
# a quote or triple-quote in user text would otherwise end the literal and
# execute the rest as Python (WB-35 review).
WB_TITLE="<title>" WB_DESC="<description>" WB_PROJECT="<absolute project path>" \
WB_PRIO="normal" WB_TYPE="aufgabe" WB_ASSIGNEE="claude" WB_GATE="" \
python3 - << 'EOF'
import os, sys; sys.path.insert(0, "src")
from werkbank import store
t = store.create_ticket("tickets", title=os.environ["WB_TITLE"],
                        description=os.environ["WB_DESC"],
                        project=os.environ["WB_PROJECT"],
                        priority=os.environ["WB_PRIO"], type=os.environ["WB_TYPE"],
                        assignee=os.environ["WB_ASSIGNEE"],
                        gate=os.environ["WB_GATE"])
print(t.id)
EOF
```

(When another Werkbank checkout is the cwd, adjust paths accordingly — the
tickets dir and `src/` sit in the Werkbank repo root.)

## 3. Confirm and commit

- Tell the user the ticket number and title back in one sentence ("Angelegt:
  **WB-17 — <Titel>**, Priorität normal, liegt in Offen.") and where it will
  appear (board column Offen). Name the estimated assignee WITH its reason in
  half a sentence („empfohlen: opencode — klein, geprüft über ‚Tests laufen
  durch', keine Eile") so the user can veto before starting the ticket.
- Commit the new ticket file per `git-discipline`:
  `git add tickets/ && git commit -m "Add ticket <id>: <short title>"`.
  Do not push unless the session's normal push practice applies.
- The board picks the file up on its next poll — no restart needed.

## Rules

- Creating is not starting: the ticket stays in `offen`. Only the user's drag (or
  an explicit "arbeite es ab") dispatches it.
- Never invent acceptance criteria the user didn't imply — a wrong criterion
  steers the working agent into the wrong build.
