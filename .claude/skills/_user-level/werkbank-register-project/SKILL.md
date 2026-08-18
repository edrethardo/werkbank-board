---
name: werkbank-register-project
description: Use when the user asks THIS session to register its project with the Werkbank — "registriere dieses Projekt bei der Werkbank", "meld dich an der Werkbank an", "trag das Projekt ins Board ein" — add the current project to the Werkbank's project list.
version: 1
---

# Werkbank: Dieses Projekt anmelden

Für Nutzer, die die Werkbank schon haben und ein NEUES Projekt beginnen: Statt
im Board zu klicken, meldet die Projekt-Session sich selbst an. Danach kann
jedes Ticket dieses Projekt per Namen wählen.

**Nur auf ausdrücklichen Wunsch** ("registriere dieses Projekt bei der
Werkbank"). Niemals von selbst, weil eine Sitzung in einem neuen Ordner startet
— dieser Skill ist maschinenweit installiert und würde sonst in fremden
Projekten anspringen.

## Pfad zur Werkbank — die EINZIGE Zeile, die du anpasst

    WERKBANK=/pfad/zur/werkbank

Jeder Befehl unten beginnt mit dieser Zuweisung, damit der Pfad genau einmal
vorkommt. Den Pfad NIE in eine Python-Zeichenkette schreiben: `~` löst die
SHELL auf, niemals Python (dieser Fehler ist schon einmal ausgeliefert worden —
WB-47).

Prüfe ihn zuerst: `ls "$WERKBANK/config.json" >/dev/null` — schlägt das fehl,
sag es dem Nutzer und stopp, statt zu raten.

## 1. Name und Pfad klären

- **Pfad** = das Arbeitsverzeichnis dieser Session, absolut und aufgelöst
  (`pwd -P`). Kein Unterordner, kein Symlink-Pfad.
- **Name** = Vorschlag aus dem Ordnernamen, in lesbar (z. B. `luna_cameraman`
  → „Luna Cameraman"). Zeig ihn dem Nutzer in einem Satz und übernimm seinen
  Gegenvorschlag. Bei einer nicht-interaktiven Sitzung nimmst du den Vorschlag.

## 2. Eintragen

Immer über `projects.add_project` — NIE `config.json` von Hand bearbeiten
(ein roher Schreibvorgang am laufenden Board vorbei hat schon einmal eine
Nutzer-Einstellung überschrieben, WB-116):

    WERKBANK=/pfad/zur/werkbank WB_NAME="Mein Projekt" \
    WB_PFAD="$(pwd -P)" python3 - <<'EOF'
    import os, sys
    sys.path.insert(0, os.path.join(os.environ["WERKBANK"], "src"))
    from werkbank import projects
    try:
        result = projects.add_project(os.path.join(os.environ["WERKBANK"], "config.json"),
                                      os.environ["WB_NAME"], os.environ["WB_PFAD"])
        print("OK:", os.environ["WB_NAME"], "->", os.environ["WB_PFAD"])
    except ValueError as e:
        print("ABGELEHNT:", e)
    EOF

Das Board liest die Projektliste bei der nächsten Anfrage neu ein (WB-124) —
**kein Neustart nötig**, und es braucht weder Passwort noch offene Anmeldung,
weil du an der Datei arbeitest und nicht über das Netz.

## 3. Melden — ehrlich

- **OK:** Sag dem Nutzer den Namen und dass das Projekt jetzt im Board steht
  (Seite neu laden). Erwähne, dass Tickets für dieses Projekt ab jetzt per
  Name gewählt werden können, und dass diese Session ihre Tickets mit
  „zieh dir dein Ticket" holt (`werkbank-pull-ticket`).
- **ABGELEHNT (Name oder Ordner schon vergeben):** Gib die Meldung wörtlich
  weiter. Das ist kein Fehler, sondern meistens „schon angemeldet" — dann ist
  nichts zu tun.
- **Werkbank-Pfad falsch / `config.json` fehlt:** Sag genau das. Rate keinen
  anderen Pfad und lege keine Konfiguration an — eine Werkbank ohne
  `config.json` ist nicht eingerichtet, das gehört in ihre eigene Sitzung.

## Was dieser Skill NICHT tut

- **Keine Prüfung (Gate) anlegen.** Ohne hinterlegte Prüfung können in diesem
  Projekt keine `opencode`-Tickets starten. Sag dem Nutzer diesen einen Satz,
  wenn er lokale Modelle nutzen will — einrichten lässt er es in der
  Werkbank-Sitzung.
- **Keine Tickets anlegen und keine starten.** Dafür gibt es
  `werkbank-report-bug` und `werkbank-pull-ticket`.
