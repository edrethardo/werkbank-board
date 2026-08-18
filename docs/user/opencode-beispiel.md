---
title: opencode-Beispiel — vom Anlegen bis zum grünen Gate
date: 2026-08-17
tags: [docs, feature]
summary: Ein durchgespieltes Beispiel: einen kleinen Bugfix an das lokale Modell (opencode) geben, mit einer Prüfung als Abnahmekriterium. Ende zu Ende — von der `config.json` bis zur grünen Karte.
---

# opencode-Beispiel: einen kleinen Bugfix vom eigenen Modell erledigen lassen

Dieses Beispiel zeigt dir, wie ein **opencode**-Ticket in der Praxis aussieht.
opencode ist das Modell, das auf deinem eigenen Rechner läuft — es kostet
kein Kontingent, dafür ist es 5–10× langsamer als Claude. Es braucht immer
eine **Prüfung** als Abnahmekriterium, weil ein lokales Modell auch dann
„fertig" meldet, wenn nichts funktioniert (siehe [Board und
Tickets](board-und-tickets.md#tickets-vom-modell-auf-deinem-eigenen-rechner-bearbeiten-lassen)).

Wir spielen den Fall durch: **ein kleiner Bug in einem Python-Projekt, das
`pytest`-Tests hat**. Die Tests sind unser Beweis, dass die Änderung stimmt.

## 1. Einmal einrichten: eine Prüfung für dein Projekt

opencode-Tickets brauchen eine **benannte Prüfung**. Der Name wandert
mit dem Ticket über das Board, der eigentliche Shell-Befehl steht nur in
deiner `config.json` — **über das Prüfungs-Feld** lässt sich also kein
Befehl einschleusen.

> Das ist ausdrücklich **keine** allgemeine Zusage. Wer Tickets anlegen und
> ziehen kann, kann Agenten starten — und die führen Befehle auf deinem
> Rechner aus. Genau deshalb gehört das Board nicht ungeschützt ins Netz.

Ergänze in `config.json` einen Abschnitt `gates` (falls noch nicht da),
gruppiert nach Projektpfad:

```json
{
  "port": 8765,
  "default_project": "/home/USER/code/mein-projekt",
  "projects": {
    "Mein Projekt": "/home/USER/code/mein-projekt"
  },
  "gates": {
    "/home/USER/code/mein-projekt": {
      "Tests laufen durch": "python3 -m pytest tests/ -q"
    }
  }
}
```

- Der Schlüssel unter `gates` ist der **absolute Pfad** deines Projekts.
- „Tests laufen durch" ist ein **Name**, den du frei wählst — er
  erscheint später im Ticket-Fenster als Auswahl. Der Befehl daneben ist
  das, was die Werkbank tatsächlich ausführt, um zu prüfen, ob die
  Arbeit gilt.
- Du kannst pro Projekt mehrere Prüfungen hinterlegen (z. B. „Tests
  laufen durch" und „Nur Typprüfung"), das Ticket wählt eine davon.

Speichere die Datei; das Board liest sie beim nächsten Ticket-Start
automatisch.

## 2. Ticket anlegen

Öffne im Board **„+ Neues Ticket"** und fülle so aus:

- **Titel** — was das Ergebnis ist, nicht was du tust. Beispiel:
  *„Fix: `parse_date('')` gibt None statt zu crashen"*.
- **Beschreibung** — was schiefläuft (kurz reproduzierbar) und was danach
  gelten soll. Beispiel:
  > `parse_date('')` wirft aktuell einen `ValueError`. Erwartet: gibt
  > `None` zurück und lässt die Tests grün. Der bestehende Test
  > `tests/test_dates.py::test_empty_input` ist derzeit rot.
- **Typ** — hier `Bug` (die Werkbank leitet dann auch die
  Debugging-Disziplin durch die Prompts).
- **Zugewiesen an** — **opencode (lokales Modell)**.
- **Priorität** — passt für lokale Läufe meist auf „Niedrig" oder
  „Normal" — sie sind langsam, aber niemand wartet aktiv drauf.
- Sobald du „opencode" wählst, blendet die Werkbank ein Feld
  **„Prüfung"** ein. Wähle **„Tests laufen durch"** — das ist der Name
  aus deiner `config.json`.

**Wenn keine Prüfung erscheint**, gibt es für dieses Projekt noch keinen
Eintrag unter `gates`. Die Werkbank verweigert dann den Start und sagt
dir das im Ergebnisfeld. Kein Gate → nichts läuft.

Bestätige mit **„Anlegen"**. Das Ticket landet in **Offen**.

## 3. Ticket starten und zuschauen

Zieh die Karte von **Offen** nach **In Arbeit**. Was jetzt passiert
(alles auf deinem Rechner, du siehst es live auf dem Board):

1. **opencode arbeitet.** Die Karte zeigt „⏱ seit HH:MM · setzt fort:
   …". Das kann bei einem kleinen Bugfix ~20–40 Minuten dauern (in
   einem gemessenen Beispiel: WB-106 hat 40 min gebraucht, das
   Kontingent bleibt bei $0 — nur Strom).
2. **Die Prüfung läuft.** Wenn opencode fertig zu sein glaubt, ruft die
   Werkbank deinen Befehl (`python3 -m pytest tests/ -q`) im Projekt
   auf. Grün heißt: die Arbeit gilt.
3. **Bei Grün** wandert das Ticket nach **Review**. Ein kurzer
   Claude-Blick auf den Diff (ein paar Cent — nicht das große Modell)
   sucht noch nach stillen Zusagen ohne Codebeleg. Nimmst du das
   Ticket per „Annehmen" ab, ist es erledigt.
4. **Bei Rot** darf opencode **einmal kostenlos** nachbessern — mit der
   Fehlermeldung als Hinweis. Zweimal rot: die Karte springt zurück
   nach **Offen** und trägt jetzt **claude** als Bearbeiter. Beide
   Versuche und die Prüfungsausgabe stehen im Ticket — Claude fängt
   also nicht bei null an.

**Zwei Spuren nebeneinander.** Läuft parallel bereits ein Claude-Ticket
für dasselbe (oder ein anderes) Projekt, ist das kein Problem —
opencode und Claude haben getrennte Spuren, jede Spur arbeitet ein
Ticket zur Zeit, die beiden Spuren laufen parallel.

## 4. Was du nach dem Lauf auf der Karte siehst

Nach Abschluss (Review, Erledigt oder Fehlgeschlagen) trägt die Karte
eine Zeile mit den echten Zahlen:

- **⏱** — Wanduhr des letzten Laufs.
- **💰** — Kosten des Lauf-Ereignisses (bei opencode leer, weil das
  lokale Modell nichts kostet).
- **Token / Cache** — was das Ergebnis-Ereignis der Claude-CLI gemeldet
  hat (bei opencode leer aus demselben Grund).
- **🔍 $X.XX** — kumulierte Kosten aller Klicks auf **🔍 Review-Bot**
  auf diesem Ticket (wenn du den harten Reviewer angefordert hast).

## 5. Wann opencode passt — und wann nicht

opencode ist die richtige Wahl, wenn ALLE drei gelten:

- **Klein und abgegrenzt** — neue Datei oder eine sauber umrissene
  Änderung. Kein modul-übergreifender Umbau, nichts
  sicherheits- oder Nebenläufigkeits-Sensibles.
- **Es gibt eine benannte Prüfung** für das Projekt, die genau das
  prüft, was fertig heißt.
- **Es eilt niemandem.** Die lokale Spur ist 5–10× langsamer;
  gemessen: WB-102 (~20-Zeilen-Skript) hat ≈20 min gebraucht,
  WB-108 (neue Testdatei mit Nebenwirkungen) hat opencode nach einer
  Stunde nicht geschafft, Claude in 7,6 min. Grenzfall → lieber
  gleich `claude` — ein fehlgeschlagener opencode-Versuch, der dann
  eskaliert, kostet unterm Strich mehr als direkt Claude.

Als Faustregel: der `create-ticket`-Skill schlägt dir opencode
selbstständig vor, wenn diese drei Bedingungen erfüllt sind (siehe
[Skill](../../.claude/skills/create-ticket/SKILL.md)). Du kannst den
Vorschlag jederzeit überstimmen.

## Was diese Anleitung NICHT abdeckt

- **Chat-Session-Übergabe.** opencode arbeitet immer als
  Hintergrundlauf — das „🗨️ Besprechen"-Häkchen und
  Chat-Übergaben (WB-22) betreffen nur Claude-Tickets, weil die
  lokale Spur keine Chat-Session hat.
- **opencode auf Windows.** Der Prüfungs-Aufruf geht über `/bin/sh -c`
  — auf Windows funktioniert das nicht. Für Windows-Rechner bleibt
  Claude als Bearbeiter.
- **Das `opencode-task`-Programm einrichten.** Es startet das lokale Modell
  und ist bewusst nicht Teil des Boards — welches Modell du benutzt, ist deine
  Sache. Ein fertiges Beispiel liegt aber bei: `examples/opencode-task`.
  Einmal kopieren, dann kennt der Rechner es:

      cp examples/opencode-task ~/.local/bin/ && chmod +x ~/.local/bin/opencode-task

  Wie du es auf dein Modell zeigst, steht in `examples/README.md`. Fehlt das
  Programm, sagt dir das Ticket genau das — statt still fehlzuschlagen.

- **Statt opencode geht auch `dsh`** — derselbe Weg über dasselbe Modell, nur
  ein anderes Startprogramm. Dafür liegt `examples/dsh-task` bei, gleiche
  Handgriffe:

      cp examples/dsh-task ~/.local/bin/ && chmod +x ~/.local/bin/dsh-task

  Alles in dieser Anleitung gilt unverändert: die Prüfung ist Pflicht, und
  beide teilen sich eine Spur, weil sie sich eine Grafikkarte teilen.
