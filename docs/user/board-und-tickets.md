---
title: Das Board und die Tickets benutzen
date: 2026-08-15
tags: [docs, feature]
summary: Wie man das Kanban-Board öffnet, Tickets anlegt und sie von Agenten abarbeiten lässt.
---

# Das Board und die Tickets benutzen

## Einrichten

Zwei Wege — such dir aus, was du lieber magst:

- **Chat-Weg (bequem):** Öffne die Werkbank in Claude Code und tippe **init**.
  Der Assistent führt dich durch: fragt nach deinem Standard-Projekt, erklärt
  „zieh dir dein Ticket" und bietet an, den dafür nötigen Skill zu
  installieren. Du kannst „init" jederzeit erneut tippen, um z. B. das
  Standard-Projekt zu wechseln.
- **Manueller Weg:** In der README stehen dieselben Schritte zum Nachlesen
  — sinnvoll, wenn du noch keinen Claude-Code-Chat mit dem Ordner offen hast.

## Das Board öffnen

Öffne <http://127.0.0.1:8765>. Es läuft nur auf deinem Rechner; niemand sonst
kann es sehen oder bedienen (das bleibt so, solange du im Chat nichts anderes
sagst).

**Wenn du den Autostart eingerichtet hast** (README-Schritt 6 „Start it
automatically" mit systemd, launchd oder dem Windows-Autostart-Ordner), kommt
das Board bei jeder Anmeldung von selbst hoch und startet auch nach einem
Absturz automatisch neu. **Ohne diesen Schritt musst du das Board selbst
starten**, mit `python3 src/werkbank/server.py` oder per Chat-Befehl **„Starte
das Board."** Ein Lesezeichen auf die Adresse spart dir das Tippen.

Das Board hat sechs Spalten:

| Spalte | Bedeutung |
|---|---|
| **Offen** | Wartet auf Bearbeitung |
| **Zu bearbeiten** | Deine Warteschlange: startet automatisch, sobald das vorige Ticket fertig ist |
| **In Arbeit** | Ein Agent arbeitet gerade daran |
| **Review** | Der Agent ist fertig — du prüfst das Ergebnis |
| **Fehlgeschlagen** | Der Agenten-Lauf ist technisch gescheitert (Absturz, Zeitlimit o. Ä.) — der Grund steht im Ticket |
| **Erledigt** | Von dir abgenommen. Nur du schiebst Tickets hierher |

In **Review** landet nur, was der Agent tatsächlich zu Ende gebracht hat.
Technisch gescheiterte Läufe landen getrennt in **Fehlgeschlagen** (rote
Überschrift), damit du sie nicht versehentlich abnimmst. Jede Karte dort hat
einen Knopf **„Erneut versuchen"** — ein Klick (oder Ziehen nach „In Arbeit")
startet den Agenten neu. Auch Tickets, deren Lauf durch einen Board-Neustart
abgeschnitten wurde, landen beim nächsten Start automatisch hier statt ewig in
„In Arbeit" zu hängen.

## Vom Handy aus benutzen

Das Board läuft zunächst **nur auf deinem Rechner**. Für den Zugriff vom Handy
muss es einmal fürs Heimnetz freigeschaltet werden — und das geht nur **mit
Passwort**: Ohne gesetztes Passwort weigert sich das Board zu starten, sobald es
ins Netz soll. Sag im Chat „mach das Board im Heimnetz erreichbar", dann richte
ich beides ein. Danach kommst du so mit dem Handy drauf:

1. Handy ins selbe WLAN wie der Rechner.
2. Im Browser die Adresse deines Rechners öffnen (der Assistent nennt sie dir;
   sie sieht aus wie `http://192.168.x.x:8765`). Als Lesezeichen oder
   „Zum Home-Bildschirm" speichern — dann fühlt es sich wie eine App an.
3. Einmal das Passwort eingeben. Das Handy bleibt danach 30 Tage angemeldet.

Am Handy bekommst du eine **eigene, aufgeräumte Ansicht** — keine geschrumpfte
Schreibtisch-Seite:

- **Wischen wechselt die Liste:** Nach links wischen zeigt die nächste Spalte,
  nach rechts die vorherige — der passende Schalter oben rutscht automatisch
  mit ins Bild. Senkrechtes Scrollen und Tippen auf Knöpfe bleiben unberührt.
- **Oben eine Reihe von Schaltern** (Offen · Zu bearbeiten · In Arbeit · Review ·
  Fehlgeschlagen · Erledigt) mit der jeweiligen Anzahl. Du tippst einen an und
  siehst genau diese Liste — statt sechs Blöcke untereinander zu scrollen. Das
  Board merkt sich, wo du zuletzt warst.
- **Ein-Tipp-Aktionen auf jeder Karte**: In „Offen" stehen dort **▶ Starten**
  und **≡ Warteschlange**, in „Review" **Annehmen** und **Ablehnen**, in
  „Fehlgeschlagen" **Erneut versuchen**. Für alles andere gibt es das Feld
  **„→ verschieben nach …"** (Ziehen funktioniert auf Touch-Bildschirmen nicht).
- **Großer + Knopf unten rechts** für ein neues Ticket — bequem mit dem Daumen.
- **Detailfenster füllt den Bildschirm**, Eingabefelder sind groß genug, dass
  das iPhone nicht hineinzoomt.
- Über „Teilen → Zum Home-Bildschirm" startet die Werkbank **ohne Adresszeile**
  wie eine echte App.

**Was du wissen musst:** Wer das Passwort hat, kann Tickets starten — und damit
Agenten, die auf deinem Rechner arbeiten. Gib es niemandem, den du nicht auch
an deine Tastatur lassen würdest. Der Verkehr im WLAN ist unverschlüsselt;
in fremden Netzen (Café, Büro) solltest du das Board nicht benutzen. Ohne
Passwort kommt niemand rein, nach fünf Fehlversuchen wird kurz gesperrt, und
fremde Webseiten kommen weiterhin nicht ans Board. Sag jederzeit „mach das
Board wieder nur lokal", dann drehe ich die Freigabe zurück.

## Tickets anlegen

Zwei Wege, beide gleichwertig:

1. **Im Board:** Knopf „+ Neues Ticket", Titel und Beschreibung eintragen, fertig.
2. **Im Chat:** Sag einfach „erstelle ein Ticket für …" oder „schreib das als
   Ticket auf". Der Assistent formuliert Titel und Beschreibung aus dem Gespräch,
   fragt nur nach, was wirklich fehlt (z. B. „Woran erkennst du, dass es fertig
   ist?"), und bestätigt dir die Ticket-Nummer. Das Ticket landet in **Offen** —
   gestartet wird erst, wenn du es ziehst oder darum bittest.

Jedes Ticket hat eine Priorität (Hoch/Normal/Niedrig), einen zugewiesenen Agenten
(Standard: `claude`) und ein Zielprojekt (der Ordner, in dem gearbeitet werden soll).
Außerdem einen **Typ**: **Aufgabe** (Standard) oder **Bug**. Bug-Karten tragen auf
dem Board ein rotes **BUG**-Abzeichen, damit Fehler sofort ins Auge fallen; ältere
Tickets ohne Typ gelten automatisch als Aufgabe.

## Mehrere Projekte

Über den Knopf **„📁 Projekte"** oben im Board legst du weitere Projekte an:
Name vergeben, Ordner angeben, fertig — die Werkbank prüft, dass es den Ordner
wirklich gibt. Den Ordner musst du nicht tippen: **„📂 Durchsuchen"** öffnet
einen Ordner-Browser zum Durchklicken (⬆️ geht eine Ebene hoch, „Diesen Ordner
wählen" übernimmt den Pfad). Danach wählst du das Projekt beim Anlegen oder Bearbeiten eines
Tickets einfach im Menü aus, und jede Karte zeigt ihr Projekt als kleines
Abzeichen (voller Pfad beim Drüberfahren). Im Chat genügt der Projektname
(„erstelle ein Ticket für Mein Spiel: …"). Ältere Tickets mit eigenen Pfaden
bleiben unverändert gültig.

Wird es voller auf dem Board, hilft das **Filter-Menü** oben: Projekt auswählen,
und das Board zeigt nur noch dessen Tickets („Alle Projekte" hebt den Filter
wieder auf). Das Board merkt sich deine Wahl. Ticket-Verknüpfungen wirken
weiterhin über alle Projekte hinweg, auch wenn sie gerade ausgeblendet sind.

## Bugs melden

**Direkt vom Ticket aus:** Auf jeder Karte in **Review** und **Erledigt** sitzt
ein Knopf **🐞 Bug melden**. Du beschreibst in einem Satz, was nicht stimmt —
fertig. Das neue Bug-Ticket bekommt automatisch den ganzen Zusammenhang mit:
worum es im Ursprungsticket ging und was der Agent damals berichtet hat.
Dadurch startet der Agent, der den Fehler behebt, nicht bei null.

Findest du irgendwo einen Fehler, kannst du ihn in jeder Projekt-Unterhaltung
einfach melden: **„Ich hab einen Bug gefunden."** Der Assistent stellt dir dann
drei kurze Fragen — Was passiert? Was hast du erwartet? Wie stellt man es nach? —
und legt daraus ein Bug-Ticket auf dem Board an (Priorität je nach Schwere).
Melden und Beheben sind getrennt: Das Ticket wartet in **Offen**, bis du es
abarbeiten lässt.

Bug-Tickets werden mit besonderer Sorgfalt abgearbeitet: Der Agent muss den Fehler
erst nachstellen, dann die Ursache beheben und zum Schluss einen Test hinterlegen,
der das Wiederauftreten verhindert — der Nachweis steht im Ergebnis.

Beide Skills — „zieh dir dein Ticket" und das Bug-Melden — müssen einmal
installiert werden (das macht die Einrichtung, siehe „init"); danach stehen sie
in jeder Projekt-Unterhaltung zur Verfügung. Frag im Zweifel: „sind die
Werkbank-Skills installiert?"

## Tickets abarbeiten lassen

Der schnellste Weg: **Zieh das Ticket einfach von „Offen" nach „In Arbeit".**
Das Board startet dann selbstständig einen Claude-Agenten im Zielprojekt — und
zwar als Fortsetzung der Unterhaltung, die in diesem Projekt **zuletzt ein Ticket
bearbeitet** hat (mit deren ganzem Wissen) — auch dann, wenn diese Bearbeitung
in einem Chat geschah; laufende Chat-Unterhaltungen werden dabei immer nur auf
einer Abzweigung fortgesetzt, nie direkt verändert. Was du zwischendurch im Chat
machst, ohne Tickets zu bearbeiten, ändert daran nichts. Gab es noch keine Ticket-Bearbeitung, nimmt der Agent die
zuletzt aktive Unterhaltung des Projekts; gab es gar keine, startet er frisch. Mehrere gezogene Tickets werden nacheinander abgearbeitet; jeder Start
verbraucht Claude-Kontingent. Wichtig zu wissen: Diese Agenten arbeiten **ohne
Rückfragen** — sie dürfen im Zielprojekt Dateien ändern und Befehle ausführen.

Jedes Ticket hat außerdem eine Checkbox **„⑂ Auf Abzweigung arbeiten"**
(Standard: aus). Ausgeschaltet setzt der Agent die gemerkte Ticket-Session
direkt fort — sie wächst als eine durchgehende Unterhaltung mit. Eingeschaltet
arbeitet er auf einer Abzweigung, und die gemerkte Session bleibt unverändert.
Gibt es noch keine gemerkte Ticket-Session, wird zur Sicherheit immer
abgezweigt, damit keine fremde Unterhaltung (z. B. dein Chat) verändert wird.

**Chat-Übergabe:** Ist die gemerkte Ticket-Session eine gerade **offene
Chat-Unterhaltung** (weil der Assistent dort zuletzt ein Ticket bearbeitet
hat), wird nicht heimlich im Hintergrund gearbeitet: Das Ticket wird der
offenen Unterhaltung übergeben — der Assistent meldet sich dort und bearbeitet
es sichtbar vor deinen Augen. Die Karte zeigt solange „an Chat-Session
übergeben". Übernimmt der Chat nicht binnen weniger Minuten (Fenster zu,
Session beendet), startet automatisch der gewohnte Hintergrund-Lauf als
Abzweigung — nichts bleibt hängen. Angehakte ⑂-Checkbox heißt: bewusst ohne
Übergabe, direkt im Hintergrund.

Alternativ geht es weiter per Chat: **„Arbeite die Tickets ab"** (oder gezielt:
„erledige WB-3"). In beiden Fällen passiert dasselbe:

1. Das Ticket wandert nach **In Arbeit**.
2. Der zugewiesene Agent erledigt die Aufgabe im Zielprojekt.
3. Das Ergebnis steht danach im Ticket (anklicken → Feld „Ergebnis"), und das Ticket
   wandert nach **Review** — auch wenn etwas schiefging; dann steht dort, woran es
   hakte.
4. Du prüfst das Ergebnis. Jede Karte in der Spalte **Review** hat dafür zwei
   Knöpfe: **„Annehmen"** schiebt das Ticket nach **Erledigt**. **„Ablehnen"**
   fragt dich nach dem Grund (Pflichtfeld), schreibt ihn ins Ticket und schiebt es
   zurück nach **Offen**, damit nachgebessert wird.

## Tickets vom Modell auf deinem eigenen Rechner bearbeiten lassen

Neben Claude kann auch **opencode** Tickets abarbeiten — das ist das Modell, das
auf deinem eigenen Rechner läuft. Es kostet kein Kontingent und keine Gebühren.
Du wählst es, indem du bei **„Zugewiesen an"** `opencode` einträgst.

**Dafür braucht das Ticket eine Prüfung.** Sobald du `opencode` einträgst,
erscheint im Ticket ein Auswahlfeld **„Prüfung"**. Das ist der Grund:

> Ein Modell auf dem eigenen Rechner meldet auch dann „fertig", wenn nichts
> funktioniert. Seine eigene Auskunft darf deshalb nicht darüber entscheiden, ob
> die Arbeit gilt. Das entscheidet die Prüfung — zum Beispiel „Tests laufen
> durch". Sie läuft nach der Arbeit automatisch.

So läuft ein solches Ticket:

1. opencode arbeitet das Ticket auf deinem Rechner ab.
2. Die gewählte Prüfung läuft. **Grün:** das Ticket geht nach **Review**, und
   Claude schaut vorher noch kurz auf die Änderung (ein paar Cent).
3. **Rot:** opencode bekommt die Fehlermeldung und darf **einmal kostenlos**
   nachbessern.
4. **Zweimal rot:** das Ticket geht zurück nach **Offen** und ist jetzt Claude
   zugewiesen. Beide Versuche und die Fehlerausgabe stehen im Ticket — Claude
   fängt also nicht bei null an.

Ist für ein Projekt keine Prüfung hinterlegt, sagt das Ticket dir das und wird
**nicht** gestartet. Sag mir dann im Chat, woran man in dem Projekt sieht, dass
etwas funktioniert — ich trage es ein. Die Prüfungen selbst stehen in der
Konfiguration, nicht im Ticket: Über das Board wandert nur der **Name** einer
Prüfung, nie ein ausführbarer Befehl. Sonst könnte jemand, der ans Board kommt,
sich damit Befehle auf deinem Rechner ausführen lassen.

## Die Warteschlange „Zu bearbeiten"

Statt jedes Ticket einzeln zu ziehen, kannst du mehrere in die Spalte **Zu
bearbeiten** legen. Die Werkbank arbeitet sie dann **nacheinander von selbst**
ab: Sobald das laufende Ticket fertig ist, startet das nächste — nach Priorität
geordnet (Hoch vor Normal vor Niedrig). Jede wartende Karte schreibt dir dazu,
worauf sie gerade wartet.

Standardmäßig **pausiert die Warteschlange, solange ein Ticket dieses Projekts
in Review liegt** — so behältst du die Kontrolle und prüfst jedes Ergebnis,
bevor weitergearbeitet wird. Willst du das nicht, öffne **„📁 Projekte"** und
setz bei dem Projekt das Häkchen **„Review blockiert die Warteschlange nicht"**:
Dann läuft ein Ticket nach dem anderen durch, und die fertigen sammeln sich in
Review, bis du Zeit zum Abnehmen hast.

Andere Projekte halten deine Warteschlange nie auf — jedes Projekt hat seine
eigene Reihe. (Gleichzeitig gearbeitet wird trotzdem nie: Agenten laufen immer
einer nach dem anderen.)

## Tickets verknüpfen

Beim Anlegen und im Detailfenster gibt es zwei Verknüpfungsfelder (einfach
WB-Nummern eintragen, mehrere mit Komma):

- **„Muss warten auf":** Das Ticket startet erst, wenn die genannten Tickets
  **Erledigt** sind. Vorher ist die Karte gedämpft dargestellt und trägt ein
  ⛓-Zeichen — mit der Maus darüberfahren zeigt dir, worauf es wartet und wie
  weit das ist (z. B. „Wartet auf WB-8 (noch offen)").
- **„Nicht gleichzeitig mit":** Zwei so verbundene Tickets werden nie
  gleichzeitig bearbeitet — egal, in welchem der beiden du die Verknüpfung
  einträgst. Auf der Karte als 🚫 sichtbar.

Ziehst du ein blockiertes Ticket trotzdem nach „In Arbeit", startet nichts:
Du bekommst eine kurze Meldung, welche Verknüpfung gerade blockiert, und das
Ticket bleibt in Offen. Verweise auf gelöschte Tickets werden angezeigt
(„unbekannt"), blockieren aber nicht.

## Wer arbeitet gerade — und wie läuft es?

Jede Karte in **In Arbeit** zeigt unten, seit wann der Lauf läuft und welche
Session fortgesetzt wird (⑂ bedeutet: auf einer Abzweigung). Dazu kommt der
Live-Zustand des Agenten:

- **Fortschritt:** wie viele Arbeitsschritte er gemacht hat, welches Werkzeug
  er zuletzt benutzt hat und wie viele Token verbraucht sind.
- **Kontingent:** Ab 75 % Auslastung deines Claude-Kontingents warnt die Karte
  („⚠️ Kontingent 82 % (7-Tage)"); ist es aufgebraucht, steht das dort — dann
  weißt du sofort, warum nichts vorangeht.
- **Hängt er?** Meldet sich ein Agent drei Minuten lang nicht, erscheint rot
  „seit X min keine Rückmeldung". Meldet er einen Fehler, steht der Fehler da.
- **Mitlesen:** Das Lauf-Protokoll wird laufend mitgeschrieben (Pfad im
  Detailfenster) — du kannst also jederzeit hineinschauen, statt bis zum Ende
  zu warten.

Scheitert ein Lauf, sagt das Ticket in klarem Deutsch warum — zum Beispiel
„Nutzungslimit erreicht, später mit Erneut versuchen nochmal starten".

Klick die Karte an, und du siehst die vollständige Session-Kennung und den
Pfad zum Lauf-Protokoll.
Nach dem Lauf bleibt die Kennung im Ticket gespeichert — du kannst also auch
später nachschlagen, wer ein Ticket bearbeitet hat. Steht dort „kein Board-Lauf
aktiv", wartet das Ticket in der Warteschlange oder wird gerade im Chat
bearbeitet.

## Netzwerk-Zugang selbst ein- und ausschalten

Falls du den Handy-Zugang je zurückdrehen oder das Passwort wechseln willst,
geht das mit zwei Befehlen — der Assistent macht das auf Zuruf, du brauchst
kein Terminal:

- **Passwort ändern:** „setz ein neues Board-Passwort" → fragt es zweimal ab und
  speichert nur den Fingerabdruck, nie das Passwort selbst.
- **Netzwerk aus:** „mach das Board wieder nur lokal" → danach ist es nur noch
  auf diesem Rechner erreichbar.
- **Handy verloren?** Sag „setz ein neues Passwort für das Board". Damit werden
  **alle angemeldeten Geräte abgemeldet** — auch das verlorene, sofort. Nur das
  Passwort im Kopf zu ändern reicht nicht; deshalb macht die Werkbank beides in
  einem Schritt.
- **Netzwerk an:** „mach das Board im Heimnetz erreichbar" → nennt dir die
  Adresse fürs Handy. Ohne gesetztes Passwort weigert sich die Werkbank.

## Bilder vom Handy hochladen

Öffne am Handy **http://192.168.x.x:8765/upload** — dieselbe Adresse
wie fürs Board, nur mit `/upload` am Ende (dieselbe Anmeldung wie
beim Board), wähle ein oder mehrere Bilder aus und tippe „Hochladen". Sie
landen im Ordner `docs/images/` deiner Werkbank, und der Assistent kann sie
danach verwenden — praktisch für Screenshots, die ins Handbuch oder in einen
Fehlerbericht sollen. Angenommen werden nur echte Bilder (bis 15 MB); die
Dateinamen werden automatisch entschärft.

## Wenn das Claude-Kontingent aufgebraucht ist

Läuft dein Kontingent während eines Agenten-Laufs leer, gilt das nicht als
Fehler: Das Ticket **bleibt in „In Arbeit" stehen und wird rot markiert** —
mit dem Hinweis, wann es weitergeht („Claude-Kontingent aufgebraucht — macht um
14:30 Uhr von selbst weiter"). Zur Reset-Zeit nimmt die Werkbank es automatisch
wieder auf. **Du musst nichts tun und nichts schreiben.** Solange die Pause
läuft, startet die Werkbank auch nichts anderes — sie würde ja sofort wieder
ins Limit rennen.

## Fragen an das Board

„**Was steht an?**" im Chat liefert dir jederzeit eine Übersicht, ohne das Board zu
öffnen.

## Gut zu wissen

- Jedes Ticket ist eine einfache Textdatei im Ordner `tickets/` — nichts kann in
  einer Datenbank verloren gehen.
- **Gesichert ist aber nur, was auch committet wurde.** Das Board selbst
  committet nichts; das tun die Agenten, wenn sie ein Ticket abarbeiten. Ein
  Ticket, das du anlegst und gleich wieder löschst, war unter Umständen nie in
  git — dann ist es weg.
- Löschen geht im Detailfenster (roter Knopf, mit Rückfrage). Wiederherstellen
  kann der Assistent ein Ticket nur, wenn es vorher committet wurde — sag im
  Zweifel „sichere die Tickets", bevor du aufräumst.
- Das Board aktualisiert sich alle paar Sekunden von selbst.
- Bearbeitest du ein Ticket, während der Agent gleichzeitig sein Ergebnis
  hineinschreibt, geht nichts verloren: Beides bleibt erhalten, und wo das nicht
  sicher ginge, lehnt das Board dein Speichern mit einem kurzen Hinweis ab —
  dann einfach kurz prüfen und noch einmal speichern.
- Ist eine Ticket-Datei beschädigt (z. B. nach einer Handbearbeitung), fällt
  nicht mehr das ganze Board aus: Alle lesbaren Tickets erscheinen normal, und
  oben zeigt ein roter Hinweis, welche Datei kaputt ist und warum.
- Die Ticketfenster kannst du an der rechten unteren Ecke auf die gewünschte
  Größe ziehen.
- Das Board ist standardmäßig dunkel. Der Knopf mit Sonne bzw. Mond oben rechts
  wechselt zwischen hellem und dunklem Design; das Board merkt sich deine Wahl.
