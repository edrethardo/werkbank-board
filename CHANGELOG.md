# Changelog

Alle Änderungen, die für dich als Nutzer der Werkbank relevant sind — neueste zuerst.
Technische Interna bleiben bewusst außen vor.

<!-- Format: https://keepachangelog.com. This header is rewritten into the user's
language during setup; entries are maintained by the documenting skill. -->

## [Unreleased]

_(noch nichts)_

## [1.1.0] — 2026-08-18

Sammel-Release nach 1.0.0, in zwei Wellen entstanden.

**Neu für dich:** ein Ticket-Typ **Epic**, ein Häkchen und ein Knopf für
**interaktive Chat-Bearbeitung**, ein **Review-Bot** auf Knopfdruck samt
Kostenanzeige, ein dritter Weg für Tickets (**`dsh`** — dasselbe Modell auf
deinem eigenen Rechner über eine zweite Software) — und für beide lokalen Wege
liegt jetzt ein lauffähiges Startprogramm bei, sodass sie auch auf einem
fremden Rechner ohne Bastelei funktionieren.

**Vier Dinge, die nicht funktionierten und es jetzt tun.** Alle vier wurden in
der Nacht auf den 18.08. gefunden, keines davon durch Nachdenken:

- Das Board **arbeitete die Warteschlange nur ab, solange ein Browser-Tab
  offen war.** Ohne offenen Tab blieben Tickets liegen — ohne Fehlermeldung,
  unbegrenzt.
- **„In Arbeit" behauptete Arbeit, die nicht stattfand**, sobald eine
  Chat-Sitzung ein Ticket übernommen hatte.
- Die **Warteschlange ließ sich nicht sortieren.** Der Knopf dafür wirkte
  zwar — die Spalte zeigte nur nie die Reihenfolge, in der gearbeitet wird.
- Der **Bericht des Review-Bots war unauffindbar**: er stand am Ende eines
  vierzehn Zeilen hohen Kastens, hinter dem gesamten Agenten-Bericht.

Dazu: Berichte zweier Sitzungen **löschen sich nicht mehr gegenseitig**, und
das Board behauptet nicht mehr fälschlich, Claude sei nicht installiert.

Die automatische Prüfung läuft auf **Linux, Windows und macOS** und ist auf
allen dreien grün. Was sie NICHT abdeckt, steht im README: den
Chat-Übergabe-Wachposten und die Prüfung des lokalen Modells (beide
Shell-basiert) sowie den Einrichtungsweg auf Windows und macOS hat dort noch
niemand wirklich ausgeführt — **das Board ist unter Windows und macOS nie
gestartet worden.** Und der `dsh`-Weg hat genau einen echten Lauf über das
Board hinter sich; der scheiterte am Test-Entwurf des lokalen Modells, nicht
an der Werkbank.

- 2026-08-18 — **Ein fertiges Startprogramm für `dsh` liegt jetzt bei.** Der
  dritte Weg war bisher nur eingebaut, aber niemand außer uns konnte ihn
  benutzen: es fehlte das Programm, das den DeepSeek-Harness startet.
  `examples/dsh-task` ist eine lauffähige Vorlage, die ohne Zusatz-Software
  auskommt und über Umgebungsvariablen auf deinen Rechner zeigt. Vier Fallen,
  die uns echte Fehlersuche gekostet haben, sind darin gelöst und erklärt —
  jede davon war ein Fehlschlag, dessen Meldung etwas ganz anderes behauptete.

- 2026-08-18 — **`dsh`- und `opencode`-Tickets können keine Rückfragen
  stellen — und das steht jetzt sichtbar am Ticket-Formular.** Das lokale
  Modell wird über einen Ein-Schuss-Aufruf gestartet: jede Aufgabe fängt
  frisch an, es gibt keine Sitzung, die eine Antwort später fortsetzen
  könnte. Sollte ein solcher Lauf trotzdem mit einer Frage antworten
  („RÜCKFRAGE AN DEN NUTZER: …"), landet das Ticket jetzt in
  **Fehlgeschlagen** mit einer klaren Begründung — statt still in
  **Review** mit der Frage im Ergebnis-Feld. Unter der Auswahl
  „Zugewiesen an" steht sichtbar, sobald du `opencode` oder `dsh`
  wählst: „Kann keine Rückfragen stellen — Wenn Klärung nötig ist,
  `claude` wählen."
- 2026-08-18 — **Ein Bericht kann einen anderen nicht mehr still löschen.**
  Wenn zwei Sitzungen am selben Ticket arbeiten, schrieb bisher die zweite ihr
  Ergebnis **über** die erste — ohne Warnung, ohne Fehlermeldung. An einem
  einzigen Tag ist das zweimal passiert; einmal verschwand ein 49-zeiliges
  Review, einmal ein 73-Zeilen-Bericht, beides nur durch Zufall bemerkt. Ab
  jetzt hängen Agenten ihren Bericht an, statt zu ersetzen — und zwar so, dass
  auch zwei gleichzeitig fertig werdende Läufe sich nicht gegenseitig
  überschreiben können. Das Formular auf dem Board ersetzt weiterhin bewusst:
  dort siehst du ja, was du überschreibst.

- 2026-08-18 — **Das Board arbeitet die Warteschlange jetzt auch ohne offenen
  Tab ab.** Bisher wurde der Teil, der Tickets startet, erst beim ersten
  Seitenaufruf überhaupt gebaut. Wer das Board nur laufen ließ und nicht
  hinschaute — nachts, nach einem Neustart, per Skript —, dessen Tickets in
  „Zu bearbeiten" blieben einfach liegen: **ohne Fehlermeldung, ohne Hinweis,
  unbegrenzt.** Tagsüber fiel das nie auf, weil fast immer ein Tab offen ist.
  Jetzt läuft der Teil ab dem Start. Zusätzlich: Startet das Board, während
  noch ein anderer Lauf die Kontrolle hält, wartet es nicht mehr für immer,
  sondern übernimmt, sobald der andere fertig ist.

- 2026-08-18 — **Dritter Weg für Tickets: `dsh`.** Neben `claude` und
  `opencode` kannst du ein Ticket jetzt an **dsh** geben — den DeepSeek-Harness,
  der ebenfalls das Modell auf deinem eigenen Rechner benutzt. Für dich ändert
  sich nichts an den Regeln: **ohne hinterlegte Prüfung wird nicht gestartet**,
  und was das Modell selbst über seine Arbeit sagt, zählt weiterhin nicht.
  opencode und dsh teilen sich eine Spur, weil sie sich eine Grafikkarte teilen
  — sie laufen nacheinander, nie gleichzeitig.

- 2026-08-18 — **Das Board behauptet nicht mehr, Claude sei nicht installiert.**
  Beim Start stand da: „Das Programm 'claude' wurde nicht gefunden … ein Ticket
  zu starten schlägt fehl." Das war falsch — Tickets liefen problemlos. Die
  Warnung suchte nur im Suchpfad, und der Dienst startet ohne den Suchpfad des
  Nutzers; der Dispatcher dagegen kennt die üblichen Installationsorte. Beide
  benutzen jetzt dieselbe Suche, also warnt das Board nur noch, wenn Claude
  wirklich fehlt.

- 2026-08-17 — **„In Arbeit" behauptet nicht mehr mehr, als das Board weiß.**
  Hat eine Chat-Sitzung ein Ticket übernommen, stand auf der Karte „wird sichtbar
  in Chat-Session … bearbeitet" — eine Behauptung, die das Board nicht prüfen
  kann. Jetzt steht dort, wie lange der Anspruch schon steht („🗨️ seit 3 min"),
  und nach zehn Minuten ohne Ergebnis rot: **„⚠️ seit 12 min beansprucht, kein
  Ergebnis"**, dazu ein Knopf **„↩︎ zurück in die Warteschlange"**, der den
  Anspruch zurücknimmt und das Ticket sofort als Agenten-Lauf startet. Läuft ein
  echter Agenten-Lauf, wird der Klick abgelehnt. Die Frist stellst du bei Bedarf
  über `chat_claim_warn_minutes` ein.

- 2026-08-17 — **Die Warteschlange lässt sich jetzt wirklich sortieren — und
  zeigt endlich die echte Reihenfolge.** „▲ nach oben" sah aus, als täte es
  nichts: Das Ticket rückte tatsächlich vor, aber die Spalte war nach
  Ticket-Nummer sortiert statt nach Warteschlangen-Position, also bewegte sich
  die Karte nicht. Jetzt steht die Spalte **Zu bearbeiten** in genau der
  Reihenfolge, in der abgearbeitet wird. Neu: **Karten lassen sich innerhalb der
  Spalte mit der Maus umsortieren** (ein farbiger Strich zeigt beim Ziehen, wo
  die Karte landet) — vorher wurde so ein Zug stillschweigend verworfen.
  Priorität bleibt stärker als die Reihenfolge: ein „Normal"-Ticket landet über
  einem „Hoch"-Ticket ganz oben in seiner eigenen Priorität, nicht darüber.

- 2026-08-17 — **Der Bericht des 🔍 Review-Bot ist jetzt zu finden.** Er stand
  bisher als letzter Abschnitt im Ergebnis-Kasten — bei einem langen
  Agenten-Bericht also hinter über hundert Zeilen, in einem Kasten, der nur
  vierzehn Zeilen hoch ist und in sich selbst scrollt. Wer den Knopf drückte,
  bezahlte und fand nichts. Jetzt hat der Bericht einen **eigenen aufklappbaren
  Abschnitt direkt über dem Ergebnis**, der neueste zuoberst und offen, mit
  Zeitpunkt und Preis in der Überschrift. Dazu: ein laufender Review ist am Knopf
  **und** im offenen Ticket sichtbar (vorher verschwand die Anzeige nach fünf
  Sekunden), und ein fertiger Bericht erscheint im offenen Ticket von selbst.
  Neu im Handbuch: [ein Abschnitt, was der Review-Bot ist und was er
  kostet](docs/user/board-und-tickets.md).

- 2026-08-17 — **Die Karte sagt jetzt, wenn eine Chat-Sitzung nicht antwortet.**
  Ein an eine Sitzung übergebenes Ticket sah bisher fünf Minuten lang so aus wie
  ein hängendes Board. Jetzt steht die **Restzeit** auf der Karte, und sobald
  die Hälfte um ist: „⚠️ Sitzung meldet sich nicht — Hintergrund-Lauf in 1:40",
  samt Hinweis, dass ein „zieh dir dein Ticket" in der Sitzung sofort hilft.
  (Seite neu laden genügt.)

- 2026-08-17 — **Tickets fürs lokale Modell werden jetzt so geschrieben, dass es
  sie schaffen kann.** Der Assistent verlangt bei `opencode`-Tickets ab sofort
  nummerierte Schritte, genaue Datei- und Funktionsnamen, einen Abschnitt
  „Tests / Abnahme" mit **kopierbaren Befehlen und erwartetem Ergebnis", eine
  „Fertig, wenn"-Liste und die hinterlegte Prüfung. Grund: Ein kleines Modell
  fragt bei einer Lücke nicht nach — es rät, scheitert zweimal an der Prüfung
  und landet dann doch bei Claude, was teurer ist als ein ordentliches Ticket.

- 2026-08-17 — **Behoben (Windows): Zwei gleichzeitige Schreibzugriffe auf
  dasselbe Ticket konnten einen davon abstürzen lassen.** Betraf jeden, der
  die Werkbank unter Windows mit mehreren Chat-Sitzungen benutzt. Zwei Stellen
  im Dateischutz gingen davon aus, dass Dateizugriffe immer sofort erlaubt sind
  — unter Windows dürfen sie kurzzeitig verweigert werden, wenn ein anderer
  Prozess dieselbe Datei anfasst.

- 2026-08-17 — **Behoben: Bei einer frischen Installation lief nie ein Agent.**
  Wer das Board zum ersten Mal öffnete, bevor das erste Ticket existierte,
  bekam eine Werkbank, die jedes Ticket zwar auf „In Arbeit" schob, aber
  **nie etwas startete** — ohne Fehlermeldung, bis zum nächsten Neustart. Der
  Grund: Der Ordner `tickets/` gab es noch nicht, und daran scheiterte still
  eine interne Sperre. Gefunden vom neuen Frische-Maschine-Test.

- 2026-08-17 — Ein Board, das sich aus Sicherheitsgründen weigert zu starten,
  fasst vorher keine Tickets mehr an. Bisher hat es beim Beenden noch ein
  laufendes Ticket als fehlgeschlagen markiert — man stand dann ohne Board
  UND mit einem kaputten Ticket da.

- 2026-08-17 — **Behoben: Tickets desselben Projekts liefen nicht mehr in deiner
  Reihenfolge.** Seit der Parallelisierung verschiedener Projekte entschied der
  Zufall, welches von zwei Tickets desselben Projekts zuerst drankam — womit
  Priorität, Ticket-Nummer und der Knopf „nach vorne schieben" wirkungslos
  waren, ohne dass man es sehen konnte. Jede Projekt-Warteschlange hat jetzt
  genau einen Bearbeiter: innerhalb eines Projekts strikt der Reihe nach,
  verschiedene Projekte weiterhin gleichzeitig.

- 2026-08-17 — **Behoben: Das Board nahm einer Chat-Sitzung das Ticket wieder
  weg.** Sagtest du dem Chat „bearbeite Ticket X", zog er es ordnungsgemäß —
  aber das Board hielt es für gestrandet, reichte es zehn Sekunden später
  weiter und legte es nach fünf Minuten zurück nach **Offen**, während der
  Chat noch daran arbeitete. Von außen sah es aus, als hätte sich nichts
  bewegt. Ein Ticket merkt sich jetzt, **wer es wann übernommen hat**; solange
  dieser Anspruch frisch ist, rührt das Board es nicht an. Bleibt eine Sitzung
  weg, wird das Ticket nach einer Stunde wieder freigegeben — es kann also
  auch nicht ewig hängen bleiben.

- 2026-08-17 — **Die automatische Prüfung läuft jetzt auch unter Windows durch.**
  Bisher war sie dort bei jedem Push rot (rund 52 Fehler, danach ein hängender
  Test) — und das README behauptete trotzdem, sie liefe auf beiden Systemen.
  Die Fehler lagen ausnahmslos in den Test-Attrappen, nicht im Board: Sie waren
  als Unix-Shell-Skripte geschrieben, die Windows nicht ausführen kann. Jetzt
  laufen dort **427 Tests grün**, 22 davon bewusst übersprungen (Prozessgruppen,
  Signale und Ähnliches gibt es unter Windows schlicht nicht) — jeweils mit
  sichtbarer Begründung im Protokoll. Zwei echte Windows-Fehler im Board kamen
  dabei mit heraus und sind behoben: ein gesundes Ticket konnte dort als
  „kaputt" gemeldet werden, während es gerade geschrieben wurde.

- 2026-08-17 — **Das Startprogramm für das lokale Modell liegt jetzt bei.**
  Bisher stand im Handbuch „setzt voraus, dass `opencode-task` installiert
  ist" — genau das Stück, das Fremde nicht hatten. Jetzt liegt eine fertige,
  lauffähige Umsetzung in `examples/opencode-task` (nur Python-Standard,
  einmal kopieren, per Umgebungsvariablen auf dein Modell zeigen), erklärt in
  `examples/README.md`. Sieben Tests fahren dieses Programm wirklich gegen
  einen Ersatz-Agenten und halten fest, dass es zum Board passt.

- 2026-08-17 — **„Unbekannte Felder"-Fehler jetzt auf Deutsch und mit Fix
  drin.** Wenn du beim Setzen von „🗨️ nur interaktiv" oder beim
  „Besprechen"-Knopf plötzlich eine Meldung wie
  **„cannot update keys: ['interactive']"** siehst, ist das nicht kaputt
  — dein laufendes Board ist einfach älter als das Ticket-Formular in
  deinem Browser. Sobald das Board neu startet, kennt es die neuen
  Felder (Häkchen, Kosten-Feld usw.) auch serverseitig. Die Meldung
  selbst wurde entsprechend umformuliert: **„Unbekannte Felder: X.
  Meist heißt das, das laufende Board ist älter als das Ticket-Formular
  — starte das Board neu, dann kennt es die neuen Felder auch
  serverseitig. Sonst prüfe die Feldnamen auf Tippfehler."** So sagt
  dir der Fehler jetzt selbst, was zu tun ist.

- 2026-08-17 — **Vorschlag für „Zugewiesen an" beim Anlegen.** Direkt unter
  dem Assignee-Feld erscheint jetzt beim Tippen des Titels ein kleiner
  Hinweis: **„Vorschlag: opencode — passt zu ‚doku'"** oder
  **„Vorschlag: claude — passt zu ‚refactor'"**. Die Regeln sind
  einfache Regexe in `config.json` unter `assignee_router` (die kannst
  du selbst pflegen — die Standard-Liste ist ein Startpunkt). **Der
  Vorschlag fasst das Feld nicht an** — du wählst weiter selbst, was da
  reinkommt; der Hinweis ist nur Startpunkt. **Sicherheitsregel**: fällt
  ein Titel auf beide Listen, gewinnt claude (WB-146 „Claude-Läufe
  parallelisieren" hat $28.61 gekostet und hätte einer opencode-
  Fehl-Empfehlung nicht überlebt). Wenn du den Vorschlag überschreibst,
  wird das leise in `state/router_overrides.jsonl` mitgeschrieben (eine
  JSON-Zeile pro Fall) — nach ein paar Wochen kannst du damit deine
  Regexe kalibrieren. Wirkt nach Neuladen der Board-Seite.

- 2026-08-17 — **Neu im Handbuch: opencode-Beispiel.** Eine durchgespielte
  Anleitung, wie du einem lokalen Modell (opencode) einen kleinen
  Bugfix übergibst — mit einer Prüfung als Abnahmekriterium, Ende zu
  Ende von der `config.json` bis zur grünen Karte. Nachzulesen unter
  [docs/user/opencode-beispiel.md](docs/user/opencode-beispiel.md).

- 2026-08-17 — **Review-Bot fasst sich jetzt kurz.** Bisher schrieb der
  **🔍 Review-Bot** oft mehrere Bildschirmseiten Fließtext — bei
  gemessenen 26 Klicks im Schnitt gute 21 000 Ausgabe-Tokens pro Klick,
  also grob $0.30–$0.60 pro Ticket rein für die Prosa drumherum. Der
  Prompt verlangt jetzt ausdrücklich **≤200 Wörter**, eine Zeile pro
  Fund im Format `- <datei>:<zeile> — <konkretes Fehlerszenario>`,
  keine Präambel, keine Schluss-Zusammenfassung, keine Wiederholung der
  Ticket-Beschreibung. Wenn nichts zu meckern ist: ein Satz. Der
  Trade-off ist bewusst: kompakte Antworten sind billiger und leichter
  zu lesen; sollte die Kurzform mal einen Fund verpassen, den die
  Langform gefunden hätte, hilft ein zweiter Klick oder ein konkreter
  Bug-Report. Wirkt für neue Review-Bot-Klicks.

- 2026-08-17 — **Review-Bot räumt seine Prozesse zuverlässig auf.** Bisher
  konnte in seltenen Fällen ein „Enkel"-Prozess des Review-Bots die
  Standard-Ausgabe offen halten, wenn das Zeitlimit von 5 Minuten
  überschritten wurde — dann hing die Sperre pro Ticket fest und weitere
  Klicks auf **🔍 Review-Bot** wurden bis zum nächsten Board-Neustart
  abgelehnt. Der Review-Bot benutzt jetzt dieselbe Prozessgruppen-Reap-
  Mechanik, mit der auch die normalen Läufe seit WB-92 laufen: bei
  Zeitüberschreitung wird die ganze Gruppe sauber signalisiert, der
  Reviewer-Thread endet und das Ticket ist sofort wieder klickbar. Reine
  Innen-Änderung — im normalen Fall merkst du nichts; nur wenn du zuvor
  „Ein Review läuft bereits" gesehen hast, obwohl offenbar keiner mehr
  lief, tritt das seltener bis nie wieder auf.

- 2026-08-17 — **„🗨️ Besprechen"-Knopf auf offenen Tickets.** Auf jedem
  Ticket in **Offen** (an Claude vergeben, nicht blockiert) sitzt jetzt
  ein Knopf **🗨️ Besprechen**. Ein Klick übergibt das Ticket an eine
  offene Claude-Code-Session in dem Zielprojekt zur interaktiven
  Bearbeitung. Ist gerade keine Session offen, legt sich das Ticket
  kurz zurück in „Offen" mit der bekannten Meldung („Öffne Claude Code
  in dem Projekt und sag: ‚zieh dir dein Ticket'"). Das Häkchen
  „🗨️ nur interaktiv" wird gleich mitgesetzt, sodass auch spätere
  Läufe denselben Weg gehen — bis du es im Detail-Dialog wieder
  ausschaltest. Wirkt nach Neuladen der Board-Seite.

- 2026-08-17 — **Review-Bot rechnet seine eigenen Kosten mit.** Bisher lief
  jeder Klick auf **🔍 Review-Bot** unsichtbar auf deiner Rechnung — der
  Prozess hat kein Ergebnis-Ereignis geliefert, also gab es nichts zu
  messen. Ab jetzt fragt die Werkbank die Claude-CLI ausdrücklich nach
  JSON-Ausgabe, zieht Kosten und Token aus dem Ergebnis, hängt eine
  kleine Zeile (`💰 $0.1234 · 5 in / 15 out / 100 cache`) unter jeden
  Review-Bericht und zählt die Dollar in einem neuen Feld
  `review_cost_usd` zusammen. Auf der Karte siehst du das kumulierte
  Ergebnis rechts neben den Lauf-Kosten als **🔍 $X.XX $** (nur wenn
  überhaupt reviewed wurde). Wenn die CLI mal keine JSON zurückgibt,
  landet der Bericht trotzdem — nur die Kostenzeile fehlt in dem einen
  Fall. Wirkt für neue Klicks; alte Reviews bleiben ohne Zahl.

- 2026-08-17 — **„🗨️ nur interaktiv"-Häkchen pro Ticket.** Direkt unter
  „Zugewiesen an" gibt es beim Anlegen (und im Detail-Dialog) jetzt eine
  Checkbox. Ist sie angehakt, geht das Ticket beim Ziehen nach „In Arbeit"
  **nur** an eine offene Chat-Session in dem Zielprojekt — genauso wie ein
  Epic. Ist gerade keine Chat-Session offen, legt sich das Ticket zurück
  nach „Offen" mit einer klaren Meldung („Öffne Claude Code in dem
  Projekt und sag: ‚zieh dir dein Ticket‘"), statt still im Hintergrund zu
  laufen. Auf der Karte erscheint dann ein kleines 🗨️ neben der
  Ticketnummer, und wenn das Ticket zurückbouncte, bekommt es dieselbe
  auffällige Umrandung wie ein wartendes Epic. Für opencode-Tickets ohne
  Wirkung — die kennen keine Chat-Session.

- 2026-08-16 — **Neuer Ticket-Typ: Epic.** Ein **Epic** ist ein größeres
  Paket, aus dem mehrere Aufgaben werden sollen. Beim Anlegen (im „Neues
  Ticket"-Dialog gibt es jetzt die Auswahl „Epic") beschreibst du grob das
  Ziel; wenn du das Epic nach **In Arbeit** ziehst, geht es an die
  **Chat-Session in dem Zielprojekt** (nicht an einen stillen
  Hintergrund-Lauf). Ist gerade keine Chat-Session offen, legt sich das
  Epic zurück in **Offen** — mit einer klaren Meldung im Ergebnisfeld
  („Öffne Claude Code in dem Projekt und sag: ‚zieh dir dein Ticket‘")
  und einer auffälligen Umrandung auf der Karte, damit du siehst, dass
  eine Aktion nötig ist. Die Chat-Session plant das Epic mit dir und
  schreibt die Kind-Tickets; jedes Kind trägt einen Verweis (🧭 WB-N) auf
  das Epic, und das Epic zeigt seine Kinder samt Fortschritt („Kinder:
  2/5 erledigt") direkt auf der Karte. Danach wandert das Epic wie jedes
  andere Ticket in **Review** — die Kinder leben eigenständig weiter.

- 2026-08-16 — **Handy-Board bleibt in Ruhe.** Wenn du auf dem Handy nach
  unten durch eine lange Ticket-Liste gescrollt hast, sprang die Seite
  bisher alle paar Sekunden zurück ganz nach oben — bei jedem Auto-Refresh.
  Ursache: die Werkbank hat versucht, den aktiven Spalten-Schalter in die
  Mitte der Leiste zu schieben, und dabei aus Versehen die ganze Seite
  mitgezogen. Jetzt bewegt sich nur noch die Schalter-Leiste selbst,
  waagerecht — deine Scroll-Position bleibt, wo sie war. Wirkt nach dem
  nächsten Laden der Seite auf dem Handy.

- 2026-08-16 — **Laufende Tickets bleiben in ihrer Spalte.** Ein Ticket in
  **In Arbeit** oder **Rückfrage** lässt sich nicht mehr per Ziehen woanders
  hinschieben — die Karte ist nicht mehr greifbar, und selbst wenn jemand
  das per API versuchen würde, lehnt die Werkbank es mit einer klaren
  Erklärung ab. Grund: das Verschieben hat den laufenden Agenten nie
  gestoppt (er lief weiter und verbrauchte Tokens), das Ergebnis fiel dann
  still unter den Tisch. Ein Ticket in Arbeit wandert erst, wenn der Agent
  selbst fertig wird (Review/Fehlgeschlagen); eine Rückfrage wandert erst,
  wenn du sie über das Antwortfeld auf der Karte beantwortest. „Erneut
  versuchen" auf einem fehlgeschlagenen Ticket und „Annehmen/Ablehnen" auf
  einem Review-Ticket funktionieren unverändert. Wirkt nach Neuladen der
  Board-Seite (Server-Neustart nur nötig, wenn die neue server.py noch
  nicht geladen ist).

- 2026-08-16 — **Spalten füllen jetzt die ganze Höhe.** Eine leere oder kurze
  Spalte war bisher nur so hoch wie ihr Inhalt (mindestens 12rem) — darunter
  konnte man keine Karte fallen lassen. Jetzt streckt sich jede Spalte über
  die volle Höhe des Bretts, egal wie voll sie ist. Karten von jeder Spalte
  in jede andere zu ziehen ist damit sichtbar leichter. Reine Anzeigeänderung:
  ein Neuladen der Board-Seite reicht.

- 2026-08-16 — **Claude-Läufe verschiedener Projekte laufen jetzt WIRKLICH
  parallel.** Bisher war die Werkbank-Regel „ein Claude-Lauf gleichzeitig,
  egal in welchem Projekt", weil zwei `claude -p`-Prozesse die gemeinsame
  Datei `~/.claude.json` beschädigen können (bekannter Claude-Code-Fehler).
  Jetzt bekommt jedes Projekt sein eigenes Konfigurationsverzeichnis (unter
  `~/.local/share/werkbank/claude-configs/<projekt>`, nur für dich lesbar),
  und die Anmeldedatei wird nur symbolisch dorthin verknüpft — nicht kopiert.
  Damit gibt es keine geteilte Datei mehr zwischen Projekten. **Innerhalb
  eines Projekts** bleibt es weiter bei einem Lauf gleichzeitig (die Dateien
  sind ja dieselben). Die Sitzungshistorie eines Projekts bleibt erhalten
  (die JSONL landet im projekteigenen Verzeichnis), damit Fortsetzungen mit
  `--resume`/`--fork-session` weiter funktionieren. Empirisch belegt vor
  Umsetzung: WB-146-Beweislauf.

- 2026-08-16 — **Review-Bot per Knopfdruck.** Auf jeder Karte in **Review**,
  **Erledigt** oder **Fehlgeschlagen** sitzt jetzt der Knopf **🔍 Review-Bot**.
  Ein Klick startet Claude Sonnet in einer **frischen Instanz** (ohne Werkzeuge,
  ohne Kontext-Übertrag) als adversarialen Reviewer — er sucht aktiv nach
  Fehlern, Lücken und stillen Zusagen im Diff des Tickets. Läuft im
  Hintergrund neben den normalen Ticket-Läufen; der Bericht landet nach ein
  paar Sekunden als neuer Abschnitt **„## Review-Bot (Datum)"** im Ticket
  (mehrere Reviews stapeln sich). Ein Review pro Ticket zur Zeit. Kostet ein
  paar Cent pro Klick. Wirkt nach dem nächsten Board-Neustart.

- 2026-08-16 — **Tickets in „Zu bearbeiten" nach vorne schieben.** Jede Karte in
  der Warteschlange bekommt einen Knopf **▲ nach oben** — ein Klick, und das
  Ticket wandert einen Platz nach vorn. Die Priorität bleibt das stärkere
  Sortierkriterium (ein normal-Ticket kann kein hoch überholen), aber innerhalb
  einer Priorität hast du die Reihenfolge selbst in der Hand — kein
  Raus-und-wieder-Reinziehen mehr. Wirkt nach dem nächsten Board-Neustart.

- 2026-08-16 — **Chat-Sessions verlieren ihren Anspruch nicht mehr durch
  Hintergrundläufe.** Wenn du in einer Chat-Session ein Ticket bearbeitest,
  merkt die Werkbank die Session als „interaktiv" — daran hängt der Schutz,
  dass parallele Hintergrundläufe deinen Anspruch nicht überschreiben.
  Bisher konnte ein späterer Hintergrundlauf desselben Projekts die Marke
  stillschweigend entfernen; danach fiel der Schutz weg und eine
  Doppelbearbeitung war möglich (2026-08-16 an WB-142 gemessen). Der Anspruch
  bleibt jetzt bestehen, egal wie viele Hintergrundläufe später am selben
  Projekt arbeiten.

- 2026-08-16 — **Bearbeiter wird ausgewählt statt getippt.** Im Ticket-Fenster ist
  „Zugewiesen an" jetzt ein Auswahlfeld: **claude** oder **opencode (lokales
  Modell)** — die beiden Spuren, die von selbst starten. Tippfehler wie
  „opencde" können damit kein Ticket mehr stillschweigend liegen lassen. Trägt
  ein Ticket einen anderen Bearbeiter (z. B. einen Personennamen), bleibt der
  erhalten und wird als „startet nicht automatisch" angezeigt.

- 2026-08-16 — **Tickets zeigen jetzt auch, WIE LANGE der Lauf gedauert hat.**
  Auf jeder Karte in Review/Erledigt/Fehlgeschlagen steht die reine Arbeitszeit
  des letzten Laufs (⏱ Sekunden bis eine Minute, danach in Minuten). Die
  Wartezeit in der Warteschlange und Kontingent-Pausen zählen bewusst NICHT
  mit — sonst würde ein Ticket, das einmal wegen aufgebrauchtem Kontingent
  vier Stunden schlief, jede Statistik verfälschen. Bei Neuversuchen zählt
  der jeweils letzte, abgeschlossene Lauf. Wirkt nach dem nächsten
  Board-Neustart.

- 2026-08-16 — **Tickets zeigen, was ein Lauf verbraucht hat.** Für jedes von
  Claude erledigte Ticket steht ab jetzt auf der Karte, was der Lauf gekostet
  hat (in Dollar, aus dem Ereignisstrom der Claude-CLI übernommen) und wie
  viele Tokens er verbraucht hat (Ein-/Ausgabe zusammengefasst, plus Cache
  extra). Für **opencode**-Läufe bleibt die Zeile leer — das lokale Modell
  meldet keine Kosten (die eigentliche Rechnung ist die kurze Claude-Prüfung
  des Diffs). Damit lassen sich Läufe später ehrlich vergleichen. Wirkt nach
  dem nächsten Board-Neustart — für neue Läufe; alte Tickets bleiben leer,
  weil ihre Zahlen nirgends festgehalten wurden.

- 2026-08-16 — **Geisterläufe können sich nicht mehr vermehren.** Drei Lücken
  aus der Schwarm-Nacht geschlossen: (1) Es kann nur noch EIN Verteiler pro
  Werkbank aktiv sein — ein versehentlich mitgestartetes Duplikat (z. B. aus
  einem Testlauf) bleibt stumm, statt dieselben Tickets doppelt zu starten.
  (2) Die Aufräum-Automatik erkennt jetzt zweifelsfrei, welche Läufe zu DIESER
  Werkbank gehören, und fasst fremde oder unbekannte Prozesse nie mehr an —
  vorher hätte sie nach einem Neustart auch Läufe anderer Boards oder der
  Test-Suite beendet. (3) Ein frisch gestarteter Lauf wird nicht mehr
  versehentlich in seiner ersten Sekunde beendet (Wettlauf zwischen Start und
  Aufräumer, im Test nachgestellt). Außerdem trägt jetzt auch bei
  Claude-Läufen das Ticket wieder die Prozessnummer — das war still
  ausgefallen. **Wichtig: wirkt erst nach dem nächsten Board-Neustart.**

- 2026-08-16 — **„Review nicht blockierend" gilt jetzt überall — auch für
  verknüpfte Tickets.** Wenn du für ein Projekt einstellst, dass ein Ticket
  in **Review** die Warteschlange nicht aufhält, dann heißt das ab jetzt: kein
  Review-Ticket dieses Projekts hält irgendetwas auf — weder das nächste
  Ticket im selben Projekt noch ein Ticket, das per **nach**-Verknüpfung
  darauf wartet. Bisher galt die Regel nur im ersten Fall; im zweiten
  wartete die Warteschlange weiter, obwohl du das Gegenteil eingestellt hattest.
  Fehlgeschlagene oder aktive Blocker halten die Warteschlange weiterhin an —
  nur „Review" wird als „vom Agenten fertig" behandelt.

- 2026-08-16 — **Agenten-Läufe sind jetzt zuordenbar, und herrenlose Läufe
  werden abgeräumt.** Jeder gestartete Agent trägt seine Ticket-Nummer als
  Umgebungsvariable — damit ist von außen (z. B. `ps` mit einem Blick in
  `/proc/<pid>/environ`) eindeutig zu sagen, welcher laufende Prozess zu
  welchem Ticket gehört. Alle ~15 Sekunden räumt die Werkbank Läufe ab, deren
  Ticket nicht mehr in Arbeit ist (weil du es abgenommen, abgelehnt oder
  zurück in die Warteschlange gezogen hast) — der Fall von sechs parallelen
  opencode-Prozessen im selben Ordner, den wir heute beobachtet haben, kann
  so nicht mehr entstehen.

- 2026-08-16 — **Zwei stille Fehler in der lokalen Spur behoben.** Wenn ein
  opencode-Ticket zu Ende war, konnten ein oder mehrere Prozesse des Laufs
  weiterlaufen — sie belegten das lokale Modell und verstopften die
  Warteschlange, bis jemand von Hand aufräumte. Am Ende jedes Laufs (auch beim
  normalen Abschluss) beendet die Werkbank jetzt die ganze Prozessgruppe.
  Zweitens: Hast du ein Ticket abgenommen, abgelehnt oder von Hand neu in die
  Warteschlange gezogen, und ein spät zurückkehrender Lauf will sein Ergebnis
  nachschieben, überschreibt er deine Entscheidung nicht mehr — was du am
  Ticket entschieden hast, bleibt so.

- 2026-08-16 — **Zwei falsche Zusagen sind aus dem Board und dem Handbuch
  entfernt.** Das Lösch-Fenster versprach früher, ein Ticket bleibe „über die
  Sicherungs-Historie" wiederholbar — das Board führt aber keine eigene
  Sicherung. Jetzt warnt es ehrlich: wiederherstellen kann der Assistent ein
  Ticket **nur, wenn es vorher mit git committet wurde**; war es das nie, ist es
  mit dem Löschen weg (im Zweifel vorher „sichere die Tickets" sagen). Das
  Handbuch behauptete außerdem, Agenten liefen „nie gleichzeitig" — das war seit
  den zwei Spuren (WB-92) falsch. Es beschreibt jetzt korrekt: **zwei Spuren
  (Claude und opencode), je Spur ein Lauf, die Spuren parallel** nebeneinander.

- 2026-08-16 — **Ein neues Projekt meldet sich selbst an.** Sag in der
  Unterhaltung eines neuen Projekts „registriere dieses Projekt bei der
  Werkbank" — die Session trägt es ein (Namensvorschlag aus dem Ordnernamen),
  und das Board zeigt es **ohne Neustart** nach einem Neuladen der Seite. Ein
  schon angemeldeter Ordner wird abgelehnt statt doppelt eingetragen, auch
  unter anderem Namen. Nötig ist dafür einmalig der neue Skill
  `werkbank-register-project`, den der `init`-Dialog mit anbietet.

- 2026-08-16 — **Rückfragen statt Fehlschläge (neue Spalte „Rückfrage").**
  Braucht ein im Hintergrund gestarteter Agent eine Entscheidung von dir,
  scheitert das Ticket nicht mehr und der Agent rät auch nicht — er pausiert
  und stellt dir seine Frage direkt auf der Karte. Du antwortest am Rechner
  oder am Handy in ein Feld auf der Karte, und der Agent **setzt in derselben
  Unterhaltung fort** (kein Kontext-Neuaufbau, kein zusätzliches Kontingent
  für den vollen Verlauf). Wartende Rückfragen blockieren die Warteschlange
  nicht — andere Tickets laufen weiter. Karten mit offener Rückfrage haben
  einen roten Rahmen, die Spalte selbst eine rote Überschrift, damit du sie
  nicht übersiehst. Für opencode-Tickets in einer späteren Runde.

- 2026-08-16 — **Handy-Uploads sind jetzt privat.** Hochgeladene Bilder landen
  in `uploads/` — nie mehr in git oder in der Veröffentlichung. Vorher gingen
  sie nach `docs/images/` und eine hochgeladene Datei hat es so bis ins
  öffentliche Repo geschafft. Die Veröffentlichung **verweigert** jetzt jede
  Binärdatei, die nicht ausdrücklich erlaubt wurde (Text-Prüfungen sehen
  Bilder nicht — jetzt gibt es eine Prüfung, die genau das tut). Außerdem
  behoben: Zwei gleichzeitige Uploads mit gleichem Namen konnten die
  Upload-Funktion in eine Endlosschleife schicken.

- 2026-08-16 — Beim Anlegen eines Tickets im Chat schlägt der Assistent jetzt
  begründet vor, **wer es bearbeiten soll**: `opencode` nur für kleine,
  abgegrenzte Aufgaben mit hinterlegter Prüfung und ohne Eile (kostenlos, aber
  gemessen 5–10× langsamer), sonst `claude`. Du kannst den Vorschlag einfach
  überstimmen.

- 2026-08-16 — **Abgebrochene opencode-Läufe sind jetzt wirklich beendet.** Beim
  WB-92-Vorfall lief der Agent nach dem Zeitlimit-Abbruch als Waise weiter und
  schrieb weiter in Dateien. Ein Abbruch beendet jetzt den ganzen
  Prozess-Stammbaum. Außerdem hat das lokale Modell sein **eigenes Zeitbudget**
  (`opencode_timeout_minutes`, Standard 60 Minuten) statt heimlich das knappere
  Claude-Limit zu erben — und ein überschrittenes Budget steht als
  verständliche Meldung im Ticket statt als „interner Fehler".

- 2026-08-16 — **opencode- und Claude-Tickets laufen jetzt wirklich gleichzeitig.**
  Die erste Reparatur (WB-92) hatte nur die Buchhaltung getrennt — intern
  arbeitete weiterhin ein einziger Arbeiter alle Läufe nacheinander ab, ein
  opencode-Ticket stand als „in Arbeit" da und wartete doch. Jetzt hat jede
  Spur ihren eigenen Arbeiter; je Spur bleibt es bei einem Lauf gleichzeitig.
  Belegt mit den drei Beweis-Tests aus dem Ticket.

- 2026-08-16 — **Kleine Ticket-Übersicht per Skript** — nur im Arbeits-Repo des Autors: `scripts/` gehört bewusst nicht zur veröffentlichten Kopie (dort steht das Werkzeug, das beim Veröffentlichen schwärzt, und es muss benennen, was es schwärzt).

- 2026-08-16 — **Passwort ändern meldet jetzt alle Geräte ab.** Wer sein Handy
  verliert, ändert das Passwort — bisher lief die Anmeldung auf dem verlorenen
  Gerät trotzdem noch bis zu 30 Tage weiter. Jetzt wird der Sitzungs-Schlüssel
  mit erneuert: Alle angemeldeten Geräte müssen sich neu anmelden.

- 2026-08-16 — Das Board weigert sich zu starten, wenn es im Netz erreichbar
  wäre, ohne dass ein Passwort gesetzt ist — und sagt, was zu tun ist. Vorher
  hat diese Regel nur der Einrichtungs-Befehl geprüft; wer `config.json` von
  Hand bearbeitete, konnte das Board unbemerkt ohne Anmeldung ins Netz stellen.

## [1.0.0] — 2026-08-16

Erste öffentliche Veröffentlichung. Die Werkbank hat an einem intensiven
Wochenende Form angenommen: Kanban-Board, selbstlaufende Warteschlange,
sichtbare Chat-Übergabe, Handy-Ansicht mit Netzwerk-Modus, ehrliches
Kontingent-Verhalten, opencode-Support mit sauberen Prüf-Gates,
strenge Sicherheits-Schranken und automatische Tests bei jedem Push
auf Linux und Windows.

- 2026-08-16 — **Veröffentlichungs-Automatik**: Das neue
  `scripts/publish-clean-copy.py` baut die öffentliche Kopie
  reproduzierbar (kopiert, filtert `tickets/`, `state.json`,
  `config.json`, `staged-skills/`, `docs/superpowers/` raus, ersetzt
  `/home/<user>` durch `/home/USER`) und **weigert sich zu veröffentlichen**,
  solange nicht: keine privaten `/home`-Pfade übrig, keine geleakte
  E-Mail-Adresse außerhalb von SECURITY.md, keine `~/`-Tilde in
  Python-Zeichenketten (WB-47-Klasse), und die volle Test-Suite grün IN
  der Kopie läuft. Damit kann der Publish-Schritt nicht mehr im Kopf
  eines Menschen fehlgehen.

- 2026-08-16 — **Wischen am Handy funktioniert jetzt ÜBERALL** (vierter Anlauf
  zu WB-68): Der Wisch-Sensor saß bisher auf dem Board-Bereich — der endet
  aber unter der letzten Karte. Ein Wisch über leerem Raum, über dem Header
  oder über den Status-Schaltern kam gar nicht an. Der Sensor sitzt jetzt
  auf der gesamten Bildschirmfläche; offene Dialoge (Details, Login,
  Bug-Melder, Ordner-Auswahl) schlucken den Wisch weiter, damit er nicht
  hinter einem Dialog die Spalte wechselt. (Seite neu laden — auf iOS Tab
  schließen und neu öffnen; auf dem Rechner Cmd/Strg+Shift+R.)

- 2026-08-16 — **Handy-Wisch wieder verlässlich** (dritter Anlauf zu WB-68):
  Nach zwei Runden Konstanten-Justierung war der Wisch immer noch
  unzuverlässig. Ursache war nicht die Schwelle, sondern der synthetische
  Klick, den der Browser nach einer kurzen Berührung nachfeuert — der
  öffnete auf einer Karte das Detail-Fenster **statt** die Spalte zu
  wechseln. Jetzt merkt sich das Board eine kurze Zeit lang (400 ms), dass
  gerade waagerecht bewegt wurde, und der Karten-Klick weiß dann: nicht
  reagieren. Tippen zum Öffnen bleibt unverändert, Buttons ebenfalls.
  (Seite neu laden.)

- 2026-08-16 — **README neu geordnet für die Veröffentlichung**: Die Warnung
  „Ein Ticket ist ein ausführbarer Auftrag" steht jetzt direkt unter dem
  Titel (mit Sprung zum Sicherheits-Abschnitt), daneben ein
  Drei-Zeilen-Schnellstart. Die Funktions-Aufzählung ist von neun auf vier
  Punkte gekürzt; Detail-Interna liegen jetzt in
  `docs/dev/board-internals.md`. Zwei Ungenauigkeiten entfernt: „every
  change is committed to git" (der Server ruft nie git auf) und die
  missverständliche Beschreibung von `host` (allein Ändern bringt keinen
  Netz-Zugriff; der LAN-Modus tut das, mit Passwort). Neu benannt: Deutsch
  als UI-Sprache, Windows-Startbefehl und der Unix-only-Wachposten fürs
  Chat-Handover.

- 2026-08-16 — **Neu: Tickets vom Modell auf deinem eigenen Rechner bearbeiten
  lassen.** Trägst du bei „Zugewiesen an" `opencode` ein, arbeitet das lokale
  Modell das Ticket ab — ohne Kontingent, ohne Kosten. Damit das verlässlich
  ist, wählst du im Ticket eine **Prüfung** aus (z. B. „Tests laufen durch"):
  Sie entscheidet, ob die Arbeit gilt, nicht die Selbstauskunft des Modells.
  Grün heißt Review (mit einem kurzen Blick von Claude auf die Änderung, ein
  paar Cent), rot heißt ein kostenloser zweiter Versuch, zweimal rot heißt
  zurück an Claude — mit beiden Versuchen und der Fehlerausgabe im Ticket. Ohne
  hinterlegte Prüfung wird gar nicht erst gestartet, und das Ticket sagt dir das.
  Über das Board wandert dabei nur der **Name** einer Prüfung, nie ein
  ausführbarer Befehl.

- 2026-08-16 — Behoben: Legte ein opencode-Lauf eine **neue Datei** an, sah die
  Kurz-Prüfung durch Claude sie nicht — im Ticket stand dann „kein Diff",
  obwohl gerade etwas Neues entstanden war. Genau dieser Fall ist bei „bau mir
  X" der Normalfall. Neue Dateien gehen jetzt mit in die Prüfung.

- 2026-08-16 — Ein opencode-Ticket wird nicht mehr dem Modell angelastet, wenn
  in Wahrheit das Werkzeug gescheitert ist: Bricht der Lauf selbst ab, sagt das
  Ticket das mit Rückgabecode — statt zweimal zu probieren und „das Modell hat
  versagt" zu melden. Ist die Prüfung grün, zählt die Arbeit trotzdem.

- 2026-08-16 — Das Board bekommt vom Server keinen Passwort-Abdruck mehr
  geschickt; er bleibt jetzt vollständig auf dem Rechner, auf dem die Werkbank
  läuft.

- 2026-08-16 — **Behoben: Ein fertiger Agent hielt die ganze Warteschlange an.**
  Wenn ein Agent nebenbei eine Hintergrund-Aufgabe laufen ließ (etwa eine
  Warteschleife), blieb er nach Abgabe seines Ergebnisses „am Leben" — die
  Werkbank hielt ihn für beschäftigt. Gemessen: 19 Minuten Stillstand auf
  „in Arbeit", dahinter vier wartende Tickets, und nach 30 Minuten wäre die
  **erfolgreiche** Arbeit als *fehlgeschlagen* eingetragen worden. Die
  Werkbank wartet jetzt nicht mehr auf die Leitung, sondern auf das Ergebnis,
  und beendet einen fertigen Lauf samt allem, was er gestartet hat. Agenten
  werden im Auftrag ausdrücklich angewiesen, keine Hintergrund-Aufgaben
  zurückzulassen.

- 2026-08-16 — **Automatische Tests bei jedem Push** — ein GitHub-Actions-Lauf
  (`.github/workflows/tests.yml`) führt die Test-Sammlung jetzt auf
  ubuntu-latest **und** windows-latest aus. Windows-spezifische
  Auslassungen (Attrappen aus sh-Skripten) stehen sichtbar im Log statt
  still durchzugehen. Kein Test-Badge im README, solange der CI-Lauf nicht
  wenigstens einmal grün war — sonst wäre es eine Lüge. Das erste
  Windows-Ergebnis landet im Journal, sobald der Push durch ist, und
  ersetzt die bisherige „nicht auf echtem Windows geprüft"-Notiz.

- 2026-08-16 — Doku-Widersprüche für Fremde geglättet: Das Handbuch behandelt
  „init" jetzt als eine von zwei gleichwertigen Einrichtungsarten (Chat oder
  README-Schritte); die Aussage „Board startet automatisch bei der Anmeldung"
  steht mit Bedingung („wenn du den Autostart eingerichtet hast, README-Schritt
  6") — sonst startest du das Board selbst. Das leere Board (Desktop) zeigt
  jetzt in der Spalte „Offen" einen freundlichen Startpunkt („Klick oben auf
  „+ Neues Ticket" …") statt einer blanken Fläche.

- 2026-08-16 — **Release-Beiwerk** für die öffentliche Kopie: Ein knappes
  `SECURITY.md` sagt, wohin man Funde meldet (keine Belohnung — persönliches
  Werkzeug), das README trägt einen sichtbaren Hinweis „Issues willkommen"
  und verlinkt CHANGELOG und SECURITY, und das öffentliche Repo bekommt fünf
  Themen (`claude-code, kanban, agents, python, no-dependencies`), damit es
  auf GitHub überhaupt findbar wird.

- 2026-08-16 — Handy-Wisch reagiert wieder wie erwartet: Der erste WB-68-Fix
  hatte drei echte Ursachen behoben, aber die Schwellwerte (60 Pixel Weg,
  Verhältnis 2:1 waagerecht/senkrecht) unverändert gelassen — schon eine
  leichte Diagonale ließ einen normalen Daumenwisch stumm scheitern. Jetzt
  40 Pixel und Verhältnis 1,5 : 1, beide als benannte Konstanten an einer
  Stelle. Tippen auf Knöpfe und normales Scrollen bleiben unbehelligt.
  Neuer Regressionstest belegt: ein realistischer Wisch löst aus, die
  alte Regel hätte ihn verworfen. In der Hand bitte prüfen — wenn's
  jetzt zu leicht auslöst, kurze Rückmeldung, dann heben wir die Schwelle
  etwas an.

- 2026-08-16 — Verwaiste Agenten-Prozesse werden beim Start-Aufräumen jetzt
  mitbeendet: Wird das Board während eines Laufs neu gestartet, hängt der
  claude-Prozess sonst als Waise weiter und verbraucht Kontingent für ein
  Ergebnis, das nirgends mehr ankommen kann. Die Werkbank merkt sich die
  Prozess-Kennung im Ticket und beendet den Lauf beim Aufräumen — aber nur,
  wenn Kennung UND Kommandozeile wirklich zu diesem Ticket passen (nie blind
  nach Namen).

- 2026-08-16 — Der Bearbeitungs-Ablauf für Werkbank-Tickets steht jetzt nur
  noch an einer Stelle (Skill `werkbank-work-ticket`); Auftragstext und die
  anderen beiden Ticket-Skills verweisen darauf, statt Regeln dreimal parallel
  zu pflegen. Wirkt intern — die Arbeit sieht für dich gleich aus.

- 2026-08-16 — **🐞 Bug melden** direkt auf der Karte (in Review und Erledigt,
  neben „Wieder öffnen"): Ein Satz genügt — das neue Bug-Ticket erbt den
  Zusammenhang (ursprüngliche Aufgabe und was der Agent damals berichtet hat),
  damit der Agent beim Beheben nicht bei null anfängt.

- 2026-08-16 — Handy-Zugang ohne Bastelei: Zwei Befehle setzen das Passwort und
  schalten den Netzwerk-Modus an oder aus (und nennen dir die Adresse fürs
  Handy). Ohne Passwort weigert sich die Werkbank, ins Netz zu gehen. Im Chat
  genügt „mach das Board im Heimnetz erreichbar" bzw. „wieder nur lokal".

- 2026-08-16 — Kontingent-Stopp sieht jetzt aus wie das, was er ist: Das Ticket
  **bleibt in „In Arbeit" und wird rot** (statt in die Warteschlange
  zurückzuwandern), mit Uhrzeit, wann es weitergeht — und genau dann nimmt die
  Werkbank es von selbst wieder auf. (Gilt ab dem nächsten Board-Neustart.)

- 2026-08-16 — Wischen am Handy wieder verlässlich: Es wird nicht mehr durch
  Zwei-Finger-Gesten oder abgebrochene Berührungen ausgelöst, wechselt nicht
  mehr die Spalte, wenn du auf einen Knopf oder ein Auswahlfeld tippst, und
  senkrechtes Scrollen bleibt Scrollen. (Seite neu laden.)

- 2026-08-16 — Am Handy wechselst du die Spalte jetzt auch durch **Wischen**
  (links/rechts); der aktive Schalter oben rutscht mit ins Bild. Am Rechner tun
  die Pfeiltasten dasselbe, wenn die schmale Ansicht aktiv ist. (Seite neu
  laden.)

- 2026-08-16 — Fehler behoben: Ein an eine Chat-Unterhaltung übergebenes Ticket
  konnte beliebig lange liegen bleiben, wenn diese es nicht bemerkte — jeder
  Board-Neustart setzte die 5-Minuten-Frist von vorn. Die Frist steht jetzt im
  Ticket selbst; nach Ablauf übernimmt automatisch ein Hintergrund-Lauf,
  Neustarts hin oder her. (Gilt ab dem nächsten Board-Neustart.)

- 2026-08-16 — Das README zeigt jetzt Bilder: die Handy-Ansicht des Boards und
  ein Beispiel, wie Claude Code aus einem Plan mehrere Tickets macht.

- 2026-08-16 — **Bilder vom Handy hochladen**: Neue Seite `/upload` im Board
  (gleiche Anmeldung) — Bilder auswählen, hochladen, fertig; sie landen in
  `docs/images/`. Angenommen wird nur, was wirklich ein Bild ist, Dateinamen
  werden entschärft. Dazu ein neuer Skill, damit der Assistent dich künftig von
  selbst dorthin schickt, wenn du ein Foto vom Handy brauchst.

- 2026-08-16 — Fehler behoben (betraf die öffentliche Fassung): Die beiden
  Skills „zieh dir dein Ticket" und „Bug melden" waren dort unbrauchbar — beim
  Veröffentlichen war ein Pfad in Python-Text gelandet, wo die Tilde nicht
  aufgelöst wird. Der Pfad steht jetzt genau einmal pro Skill an einer klar
  markierten Stelle, und ein Test verhindert den Rückfall.

- 2026-08-16 — Fehler behoben: Manchmal blieb ein Ticket in der Warteschlange
  liegen, obwohl nichts lief und nichts blockierte. Ursache: Die Warteschlange
  wurde nur angestoßen, wenn jemand das Board bediente — beendete dagegen eine
  Chat-Unterhaltung ein Ticket, passierte nichts. Sie prüft sich jetzt alle
  15 Sekunden selbst. (Gilt ab dem nächsten Board-Neustart.)

- 2026-08-15 — Schutz vor einem gefährlichen Anfängerfehler: Ist die Werkbank
  noch nicht eingerichtet (keine config.json), warnt sie jetzt beim Start und
  oben im Board — und weigert sich, einen Agenten auf den Werkbank-Ordner
  selbst loszulassen. Wer sein Projekt bewusst einträgt, merkt nichts davon.

- 2026-08-15 — Kontingent aufgebraucht? Die Werkbank macht **von selbst weiter**:
  Ein Lauf, der am Nutzungslimit scheitert, gilt nicht mehr als Fehler — das
  Ticket wartet in der Warteschlange, das Board zeigt oben, wann es weitergeht,
  und zur Reset-Zeit startet es automatisch neu. Kein „continue" mehr nötig.

- 2026-08-15 — Handy-Ansicht nachgebessert: Sie startet nicht mehr auf einem
  leeren Schalter (springt auf den ersten mit Inhalt), der Leer-Text sagt dir,
  was zu tun ist, und die Kopfzeile passt in eine Reihe (kein doppelter
  „Neues Ticket"-Knopf mehr, „Projekte" nur als Symbol).
- 2026-08-15 — **Eigene Handy-Ansicht**: Statt sechs Spalten untereinander gibt
  es oben Schalter mit Anzahl — tippen, und du siehst genau diese Liste. Auf den
  Karten stehen Ein-Tipp-Aktionen (▶ Starten, ≡ Warteschlange, Annehmen,
  Ablehnen, Erneut versuchen), unten rechts ein großer **+** für neue Tickets,
  Detailfenster füllen den Bildschirm, und über „Zum Home-Bildschirm" startet
  die Werkbank ohne Adresszeile wie eine App. Am Desktop bleibt alles beim
  Alten. (Seite einmal neu laden.)

- 2026-08-15 — Am Handy lassen sich Tickets jetzt **verschieben, ohne zu ziehen**:
  Jede Karte hat ein Feld „→ verschieben nach …" mit allen Spalten (Ziehen
  funktioniert auf Touch-Bildschirmen technisch nicht). Eingabefelder springen
  beim Antippen außerdem nicht mehr in den Zoom. Am Desktop bleibt alles wie
  gewohnt. (Seite einmal neu laden.)

- 2026-08-15 — **Board vom Handy aus benutzbar**: Auf deinen Wunsch ist die
  Werkbank jetzt im Heimnetz erreichbar — aber nur mit **Passwort-Anmeldung**
  (das Handy bleibt 30 Tage angemeldet), mit Sperre nach fünf Fehlversuchen und
  unverändertem Schutz gegen fremde Webseiten. Die Ansicht passt sich schmalen
  Bildschirmen an: Spalten untereinander, größere Knöpfe.

- 2026-08-15 — Die Werkbank läuft jetzt auch unter **Windows** (und macOS):
  Dateisperren, Speicherorte und Zeilenenden funktionieren plattformübergreifend,
  und die Anleitung erklärt den Windows-Autostart. Ehrlich dazu gesagt: Ich habe
  keinen Windows-Rechner zum Ausprobieren — der Code ist geschrieben und
  getestet, der letzte Beweis fehlt noch.

- 2026-08-15 — Die öffentliche Kopie hat jetzt eine vollständige
  Inbetriebnahme-Anleitung: Voraussetzungen, Einrichten, Board starten, erstes
  Ticket, die Chat-Befehle, Autostart und eine Übersicht aller
  Einstellungen — damit Fremde die Werkbank in wenigen Minuten laufen haben.

- 2026-08-15 — Die Werkbank ist öffentlich — als **saubere Kopie**:
  <https://github.com/edrethardo/werkbank-board> zeigt Code, Tests, Handbuch
  und das komplette Arbeitsjournal. Dein **lebendes Board bleibt privat**:
  keine Tickets, keine alte Historie, keine persönlichen Pfade und nicht deine
  private E-Mail-Adresse sind dort zu finden.

- 2026-08-15 — Sicherheit deutlich gehärtet (vor der geplanten Veröffentlichung
  von einem unabhängigen Prüfer untersucht): Eine bösartige Webseite hätte im
  Hintergrund Tickets anlegen und starten können — und damit Befehle auf deinem
  Rechner ausführen. Das ist geschlossen (das Board nimmt Änderungen nur noch
  von seiner eigenen Seite an). Außerdem behoben: manipulierte Ticket-Felder
  konnten Dateien außerhalb des Ticket-Ordners schreiben, ein präparierter
  Ticket-Text konnte Schadcode in die Board-Anzeige bringen, der Ordner-Browser
  zeigte den ganzen Rechner, und die Lauf-Protokolle lagen für alle lesbar in
  /tmp (jetzt privat unter ~/.local/state/werkbank/logs). 14 neue Tests wachen
  darüber. (Gilt ab dem nächsten Board-Neustart.)

- 2026-08-15 — Live-Status der Agenten: Karten in **In Arbeit** zeigen jetzt
  mit, was gerade passiert — Anzahl der Arbeitsschritte, das zuletzt benutzte
  Werkzeug, verbrauchte Token und dein **Claude-Kontingent** (ab 75 % mit
  Warnung, „aufgebraucht" wenn nichts mehr geht). Meldet sich ein Agent
  länger nicht, steht das rot auf der Karte („seit 5 min keine Rückmeldung");
  meldet er einen Fehler, steht der Fehler da. Scheitert ein Lauf am
  Nutzungslimit, sagt das Ticket das jetzt in klarem Deutsch statt in
  Fehlercodes. Außerdem wird das Lauf-Protokoll live mitgeschrieben — du
  musst nicht mehr bis zum Ende warten. (Gilt ab dem nächsten Board-Neustart.)
- 2026-08-15 — Neue Spalte **Zu bearbeiten**: Deine Warteschlange. Zieh Tickets
  dorthin, und die Werkbank startet sie **nacheinander von selbst** — das
  nächste beginnt, sobald das laufende fertig ist. Jede wartende Karte sagt
  dir, worauf sie gerade wartet. Standardmäßig pausiert die Warteschlange,
  solange ein Ticket des Projekts auf deine Abnahme in **Review** wartet; im
  „📁 Projekte"-Dialog kannst du das **pro Projekt** abschalten
  („Review blockiert die Warteschlange nicht") — fertige Tickets landen
  weiterhin in Review, sie halten dann nur die Warteschlange nicht mehr auf.
  Andere Projekte blockieren deine Warteschlange nie. (Gilt ab dem nächsten
  Board-Neustart.)
- 2026-08-15 — Das Board startet jetzt automatisch bei jeder Anmeldung und
  kommt nach einem Absturz von selbst wieder hoch (auf deine Wahl hin
  eingerichtet). Es bleibt bewusst nur auf diesem Rechner erreichbar — eine
  Freigabe ins Heimnetz gäbe jedem Gerät dort volle Kontrolle und passiert
  nie ohne deine ausdrückliche Entscheidung.
- 2026-08-15 — Fehler behoben: Beim Ziehen eines Tickets blitzte oft fälschlich
  die rote Meldung „Kaputte Ticket-Datei" auf. Ursache: Das Board konnte eine
  Datei genau im Moment des Speicherns halb-geschrieben lesen. Tickets werden
  jetzt atomar geschrieben — halbe Dateien kann es nicht mehr geben. (Gilt ab
  dem nächsten Board-Neustart.)
- 2026-08-15 — Tickets löschen: Im Detailfenster gibt es jetzt einen roten
  **„Löschen"**-Knopf (mit Sicherheitsabfrage). Gelöschte Tickets bleiben über
  die git-Sicherung wiederherstellbar — sag einfach Bescheid, wenn eines
  zurückkommen soll. (Gilt ab dem nächsten Board-Neustart.)
- 2026-08-15 — Die Werkbank-Skills sind jetzt überall verfügbar (nach deiner
  Freigabe installiert): In **jeder** Projekt-Unterhaltung funktioniert „zieh
  dir dein Ticket" (inklusive sichtbarer Chat-Übergabe beim Ziehen) und „ich
  hab einen Bug gefunden" (legt ein sauberes Bug-Ticket auf dem Board an).
- 2026-08-15 — Ist der Projekt-Filter aktiv, wählt „+ Neues Ticket" dieses
  Projekt jetzt automatisch vor. (Seite einmal neu laden.)
- 2026-08-15 — Projekt-Filter: Ein neues Auswahlmenü oben im Board grenzt die
  Anzeige auf ein Projekt ein („Alle Projekte" zeigt wieder alles). Die Wahl
  bleibt gespeichert. (Seite einmal neu laden.)
- 2026-08-15 — Ordner per Klick auswählen: Im „📁 Projekte"-Dialog gibt es jetzt
  **„📂 Durchsuchen"** — ein eingebauter Ordner-Browser zum Durchklicken statt
  Pfade zu tippen. Funktioniert unabhängig vom Betriebssystem. (Gilt ab dem
  nächsten Board-Neustart.)
- 2026-08-15 — Mehrere Projekte: Neuer Knopf **„📁 Projekte"** im Board zum
  Anlegen weiterer Projekte (Name + Ordner, mit Prüfung). Beim Ticket wählst du
  das Projekt jetzt bequem aus einem Menü statt einen Pfad zu tippen, und jede
  Karte trägt ein Projekt-Abzeichen. Auch im Chat genügt ab sofort der
  Projektname. (Gilt ab dem nächsten Board-Neustart.)
- 2026-08-15 — Chat-Übergabe: Ist der gemerkte Bearbeiter eine offene
  Chat-Unterhaltung, landet ein gezogenes Ticket jetzt **sichtbar dort** — der
  Assistent meldet sich im Chat und arbeitet vor deinen Augen. Die Karte zeigt
  „an Chat-Session übergeben"; übernimmt der Chat nicht binnen ~5 Minuten,
  startet automatisch der gewohnte Hintergrund-Lauf. Mit der ⑂-Checkbox
  erzwingst du weiterhin bewusst den stillen Hintergrund-Weg. (Gilt ab dem
  nächsten Board-Neustart.)
- 2026-08-15 — Du siehst jetzt, **wer** ein Ticket bearbeitet: Karten in „In
  Arbeit" zeigen Startzeit und die fortgesetzte Session (mit ⑂ bei
  Abzweigung); das Detailfenster nennt die vollständige Kennung und den Pfad
  zum Lauf-Protokoll, und nach dem Lauf bleibt die Session-Kennung dauerhaft
  im Ticket nachlesbar. (Gilt ab dem nächsten Board-Neustart.)
- 2026-08-15 — Fehler behoben: Auch wenn die letzte Ticket-Bearbeitung im Chat
  passiert ist, macht der nächste gezogene Agent dort weiter — die
  Chat-Abläufe tragen sich jetzt als letzte Ticket-Session ein. Offene
  Chat-Unterhaltungen werden dabei grundsätzlich nur auf einer Abzweigung
  fortgesetzt, nie direkt beschrieben. (Gilt ab dem nächsten Board-Neustart;
  die aktualisierten Skills muss die Werkbank-Session noch installieren.)
- 2026-08-15 — Gleichzeitiges Speichern verschluckt keine Änderungen mehr:
  Speichert der Agent sein Ergebnis, während du das Ticket bearbeitest, bleiben
  beide Änderungen erhalten; speicherst du auf einem veralteten Stand, lehnt das
  Board freundlich ab statt zu überschreiben („Das Ticket wurde inzwischen
  geändert…"). (Gilt ab dem nächsten Board-Neustart.)
- 2026-08-15 — Das Board zeichnet sich nur noch neu, wenn sich wirklich etwas
  geändert hat — kein Flackern mehr im 5-Sekunden-Takt, und eine laufende
  Zieh-Bewegung wird vom Hintergrund-Aktualisieren nicht mehr unterbrochen.
  (Seite einmal neu laden.)
- 2026-08-15 — Neue Ticket-Option **„⑂ Auf Abzweigung arbeiten"** (Standard:
  aus): Normalerweise setzt der Agent die gemerkte Ticket-Session jetzt direkt
  fort, sie wächst als eine Unterhaltung weiter. Mit Häkchen arbeitet er auf
  einer Abzweigung. Ohne gemerkte Ticket-Session wird zur Sicherheit immer
  abgezweigt. (Gilt ab dem nächsten Board-Neustart.)
- 2026-08-15 — Eine beschädigte Ticket-Datei legt nicht mehr das ganze Board
  lahm: Alle lesbaren Tickets erscheinen normal, die kaputte Datei wird oben im
  Board mit Dateiname und Grund rot gemeldet. (Gilt ab dem nächsten
  Board-Neustart.)
- 2026-08-15 — Fehler behoben: Wenn das Board mitten in einem Agenten-Lauf neu
  gestartet wurde, blieb das Ticket für immer in „In Arbeit" hängen (so bei
  WB-12 passiert). Jetzt räumt das Board beim Start solche verwaisten Tickets
  ehrlich nach **Fehlgeschlagen** (mit Erklärung im Ticket), und Agenten haben
  die feste Regel, das Board nie selbst neu zu starten.
- 2026-08-15 — Tickets lassen sich verknüpfen: **„Muss warten auf"** (startet
  erst, wenn die genannten Tickets erledigt sind) und **„Nicht gleichzeitig
  mit"** (gegenseitiger Ausschluss, wirkt in beide Richtungen). Blockierte
  Karten sind gedämpft und tragen ⛓/🚫-Zeichen mit Erklärung beim
  Drüberfahren; ein blockierter Start wird mit verständlicher Meldung
  abgelehnt. (Gilt ab dem nächsten Board-Neustart.)
- 2026-08-15 — Tickets im Chat anlegen ist jetzt ein fester Ablauf: „erstelle ein
  Ticket für …" genügt — der Assistent formuliert, fragt nur Fehlendes nach und
  bestätigt die Ticket-Nummer. Nummern und Dateiformat stimmen dabei garantiert.
- 2026-08-15 — Fehler behoben: Ein per Ziehen gestarteter Agent setzt jetzt
  zuverlässig die Unterhaltung fort, die zuletzt ein **Ticket** in dem Projekt
  bearbeitet hat — nicht mehr einfach die zuletzt aktive (z. B. deinen Chat).
  Die Werkbank merkt sich dafür pro Projekt die letzte Ticket-Session.
- 2026-08-15 — Neue Spalte **Fehlgeschlagen**: Technisch gescheiterte
  Agenten-Läufe landen nicht mehr in Review, sondern getrennt mit roter
  Überschrift — damit du sie nicht versehentlich annimmst. Mit Knopf
  **„Erneut versuchen"** direkt auf der Karte.
- 2026-08-15 — Tickets haben jetzt einen **Typ**: Aufgabe (Standard) oder Bug.
  Auswahl beim Anlegen und Bearbeiten; Bug-Karten tragen auf dem Board ein rotes
  **BUG**-Abzeichen. Alte Tickets bleiben unverändert gültig. Bug-Tickets werden
  mit Debugging-Disziplin abgearbeitet: erst nachstellen, dann Ursache beheben,
  dann ein Test gegen das Wiederauftreten — der Nachweis steht im Ergebnis.
  (Gilt ab dem nächsten Board-Neustart.)
- 2026-08-15 — Dunkles Design ist jetzt Standard. Neuer Knopf oben rechts
  (Sonne/Mond) wechselt zwischen hell und dunkel; die Wahl bleibt gespeichert.
- 2026-08-15 — Neuer Chat-Befehl **init**: fragt nach deinem Standard-Projekt,
  erklärt, wie sich eine Projekt-Session ihr Ticket vom Board zieht, und bietet
  an, den dafür nötigen Skill zu installieren.
- 2026-08-15 — Die Ticketfenster (Anlegen, Details, Ablehnen) lassen sich jetzt an
  der rechten unteren Ecke größer und kleiner ziehen.
- 2026-08-15 — Ziehen startet die Arbeit: Ein Ticket von **Offen** nach **In
  Arbeit** ziehen startet automatisch einen Claude-Agenten im Zielprojekt — als
  Fortsetzung der Session, die zuletzt an dem Projekt gearbeitet hat. Ergebnis
  und Ticket landen wie gewohnt in **Review**. Achtung: Diese Agenten arbeiten
  ohne Rückfragen, und jeder Start kostet Claude-Kontingent.
- 2026-08-14 — Review-Knöpfe auf den Karten: In der Spalte **Review** kannst du ein
  Ticket direkt **Annehmen** (→ Erledigt) oder mit Begründung **Ablehnen** (→ zurück
  nach Offen; der Grund wird im Ticket festgehalten).
- 2026-08-14 — Titel-Änderungen im Board benennen jetzt auch die Ticket-Datei um —
  Dateiname und Titel passen immer zusammen.
- 2026-08-14 — Kanban-Board im Browser: vier Spalten (Offen / In Arbeit / Review /
  Erledigt), Tickets anlegen, per Ziehen verschieben, anklicken zum Bearbeiten.
  Start mit „Starte das Board" → <http://127.0.0.1:8765>.
- 2026-08-14 — Tickets als einfache Textdateien im Ordner `tickets/` — nichts geht
  verloren, alles wird in git gesichert.
- 2026-08-14 — Abarbeiten auf Zuruf: „Arbeite die Tickets ab" startet die
  zugewiesenen Agenten; Ergebnisse landen im Ticket, fertige Tickets in **Review**.
- 2026-08-14 — Werkbank eingerichtet: Projekt initialisiert, noch keine Funktionen.
