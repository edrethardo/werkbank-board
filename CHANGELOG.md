# Changelog

Alle Änderungen, die für dich als Nutzer der Werkbank relevant sind — neueste zuerst.
Technische Interna bleiben bewusst außen vor.

<!-- Format: https://keepachangelog.com. This header is rewritten into the user's
language during setup; entries are maintained by the documenting skill. -->

## [Unreleased]

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

- 2026-08-16 — **Passwort ändern meldet jetzt alle Geräte ab.** Wer sein Handy
  verliert, ändert das Passwort — bisher lief die Anmeldung auf dem verlorenen
  Gerät trotzdem noch bis zu 30 Tage weiter. Jetzt wird der Sitzungs-Schlüssel
  mit erneuert: Alle angemeldeten Geräte müssen sich neu anmelden.

- 2026-08-16 — Das Board weigert sich zu starten, wenn es im Netz erreichbar
  wäre, ohne dass ein Passwort gesetzt ist — und sagt, was zu tun ist. Vorher
  hat diese Regel nur der Einrichtungs-Befehl geprüft; wer `config.json` von
  Hand bearbeitete, konnte das Board unbemerkt ohne Anmeldung ins Netz stellen.


_(noch keine Änderungen seit 1.0.0)_

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
