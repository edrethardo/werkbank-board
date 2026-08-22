---
name: werkbank-work-ticket
description: Use this skill whenever you work a Werkbank ticket inside this repository — via chat request ("bearbeite WB-n"), via a handover from the board, or as a dispatched run. Single source of truth for the workflow; do not duplicate it elsewhere.
version: 2
---

# Werkbank: Ein Ticket bearbeiten (einzige Quelle der Wahrheit)

Dieser Skill beschreibt den gesamten Arbeitsablauf für ein Werkbank-Ticket in
diesem Repository. Er wird von drei Wegen aufgerufen — Chat-Anforderung,
Übergabe an eine offene Chat-Session (WB-22), und Board-Dispatch (WB-70).
Alle folgen denselben Schritten; wenn etwas hier steht, steht es sonst nirgends.

## 0. Voraussetzung — Ticket ist beansprucht

Vor diesem Skill beansprucht die Session das Ticket — mit EINEM Aufruf:

    store.claim_ticket(tickets_dir, "WB-42", os.environ["CLAUDE_CODE_SESSION_ID"])

Das setzt Status, Session und `claimed_at` und leert einen Übergabe-Marker.
Schreib den Status NICHT von Hand: ohne `claimed_at` hält das Board dein Ticket
für gestrandet, übergibt es weiter und legt es fünf Minuten später zurück nach
„Offen" — während du noch daran arbeitest (WB-181, live gemessen). Wer den Skill
startet, ohne das Ticket zu halten, hat schon einen Fehler gemacht.

**Beanspruchen heisst ANFANGEN, nicht vormerken.** „In Arbeit" ist ein
Versprechen an den Nutzer: die Karte wechselt die Spalte, das Board behandelt
das Ticket als vergeben, und der Nutzer sieht Arbeit, die stattfindet. Wer
beansprucht und dann erst etwas anderes zu Ende bringt, lässt das Board lügen —
am 2026-08-17 bei WB-203 gemessen: minutenlang „in Arbeit", ohne dass irgendwer
daran arbeitete. Der Nutzer hat es bemerkt und dem Board nicht mehr geglaubt.
Muss ein Übergabe-Marker gehalten werden, bevor du wirklich anfangen kannst,
sag das im Chat in einer Zeile — und fang direkt danach an. Seit WB-204 zählt
die Karte die Minuten seit `claimed_at` und benennt einen zu lange stehenden
Anspruch; es fällt also auf.

## 1. Klarheits-Prüfung — VOR dem Coden

Lies das Ticket vollständig. Frag den Nutzer, wenn irgendetwas davon zutrifft:

- Beschreibung leer, vage oder mehrdeutig
- widerspricht Projektregeln oder sieht zerstörerisch aus
- „Fertig" ist nicht konkret sagbar

Interaktive Sitzung: direkt fragen, eine Frage pro Nachricht, in der Sprache
des Nutzers. Nicht-interaktiver Lauf (niemand kann antworten): NICHT raten —
Fragen ins `## Ergebnis` schreiben, Status bleibt `in_arbeit`, du bist fertig.
Der Nutzer weiß dann, was er klären muss (Board: „Ablehnen mit Grund").

## 1b. Schreibst du selbst ein Ticket für opencode?

Dann gilt der opencode-Modus aus dem `create-ticket`-Skill: nummerierte Schritte,
exakte Namen, `## Tests / Abnahme` mit kopierbaren Kommandos und erwartetem
Ergebnis, `## Fertig, wenn` als Haken-Liste, `gate:` gesetzt. Vorher prüfen mit
`store.opencode_ticket_gaps(...)` — ein kleines Modell fragt nicht nach, es rät.

## 2. Arbeiten — im Zielprojekt, unter dessen Regeln

- Änderungen NUR im Zielprojekt (`t.project`); nichts außerhalb anfassen.
- CLAUDE.md dieses Projekts gilt (Commit-Disziplin, Skill-Aufrufe, Sprache).
- **Bug-Disziplin** (nur `type: bug`): Ursache belegen (nachstellen, nicht
  raten) → Ursache beheben, nicht das Symptom → Regressionstest, der ohne
  den Fix fehlschlägt und mit ihm besteht. Der Nachweis (wie nachgestellt,
  welcher Test) gehört ins Ergebnis.
- **Skript-Ersetzungen prüfen** (drei Vorfälle in diesem Projekt): nach
  jedem `str.replace`/`sed` das Ergebnis lesen ODER `assert old in s` — ein
  stiller Fehltreffer täuscht dir Erledigung vor.
- **config.json NIE roh schreiben** — immer über die gesperrten Helfer
  (`werkbank.projects.set_review_mode`, `add_project`, …). Ein roher
  `json.dump` am laufenden Board vorbei hat am 2026-08-16 das
  Häkchen des Nutzers in einem anderen Projekt überschrieben (verlorenes Update, WB-116).
- **Board nie neu starten** aus einem dispatchten Lauf: der Dispatcher stirbt
  mit, dein Abschluss geht verloren.
- **Push** nur, wenn die Projektregeln das ausdrücklich vorsehen.

## 3. Prüfen — vor dem Ergebnis

Fakten sammeln, bevor du zusammenfasst:

- Passende Tests laufen lassen, Ergebnis lesen (nicht raten).
- Wenn die Änderung sichtbar ist und du sie nicht selbst sehen kannst
  (Browser, Handy), sag das ehrlich — nicht behaupten.
- Bug-Tickets: zeigen, dass der Test ohne Fix rot war und jetzt grün ist.

## 4. Doku- und Journal-Pflicht (nur wenn das Zielprojekt sie hat)

Zielprojekt hat `docs/journal/INDEX.md` → **Journal-Eintrag** ist Pflicht
(prüfen, nicht annehmen: in einer frisch geklonten Kopie kann der Ordner
fehlen — dann gilt der Fall „kein Journal“ weiter unten)
(`journaling`-Skill), im selben Commit wie die Änderung; das Ticket-Ergebnis
darf dann kurz sein und aufs Journal verweisen. Kein Journal → das
Ticket-Ergebnis IST der vollständige Bericht.

Nutzer-sichtbare Änderungen zusätzlich in CHANGELOG.md des Zielprojekts,
`documenting`-Skill regelt den Rest.

## 5. Abschluss — ehrliches Ergebnis, dann Review

Regeln für das Ergebnis, ohne Ausnahme:

- **Was tatsächlich getan wurde**, nicht was geplant war.
- **Was tatsächlich geprüft wurde** (Tests, echte Läufe) — mit Zahlen.
- **Was NICHT geprüft werden konnte** (Bildschirmaufnahme, Windows ohne
  Windows-Rechner …) explizit benannt statt kaschiert.
- **Was fehlgeschlagen ist**, mit Beweis (Fehlermeldung, Log-Pfad). Ein
  fehlgeschlagenes Ticket, das still in `in_arbeit` bleibt, ist ein Bug —
  gehört nach `review` mit dem Grund im Ergebnis.
- Nie `erledigt` setzen — das ist die Spalte des Nutzers.

Bei dispatchten Läufen liefert die letzte Nachricht das Ergebnis; die
Werkbank setzt Status und Ergebnis dann selbst.

Bei Chat-Läufen — **Nie `set_result` auf ein fremdes Ergebnis — es ERSETZT still (WB-231).**
Kein Zusammenführen, keine Versions-Prüfung, keine Warnung: vorhandener Text ist
einfach weg. Am 2026-08-18 gemessen — ein Schreibvorgang löschte das 49-zeilige
Review einer anderen Sitzung, ein anderer einen 73-Zeilen-Bericht; beides fiel
nur durch `git diff` auf. `store.append_result` hängt unter dem Store-Lock an
(Lesen und Schreiben in EINEM Lock — ein selbstgebautes Lesen-dann-Schreiben hat
genau diese Lücke). Ein leeres Ergebnis wird ersetzt, nicht ergänzt.

    WERKBANK=/pfad/zur/werkbank WB=WB-42 \
    ERGEBNIS="Kurz, ehrlich, auf Deutsch." python3 - <<'EOF'
    import os, sys
    sys.path.insert(0, os.path.join(os.environ["WERKBANK"], "src"))
    from werkbank import dispatch, store
    tickets = os.path.join(os.environ["WERKBANK"], "tickets")
    store.append_result(tickets, os.environ["WB"], os.environ["ERGEBNIS"])
    store.update_ticket(tickets, os.environ["WB"], {"status": "review"})
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid: dispatch.register_ticket_session(os.environ["PWD"], sid)
    EOF

Danach committen (nicht pushen — die Werkbank-Session pusht):

    git -C "$WERKBANK" add tickets/ && git -C "$WERKBANK" commit -m "Work tickets: <id> …"

## Fallen, in die dieses Projekt tatsächlich getappt ist

- Skript-Ersetzungen scheiterten still (WB-43, WB-48, WB-57).
- Aus einem Lauf das Board neu gestartet — Abschluss weg (WB-17).
- Chat-Änderungen stoßen die Warteschlange nicht an (WB-59, Taktgeber).
- „Frisch installiert" gegen das eigene laufende Board getestet — der antwortet
  für den Klon mit (WB-46).
- Wachposten erst nach Abschluss neu gestartet — Übergabe geht verloren
  (WB-66; siehe werkbank-pull-ticket §5).
