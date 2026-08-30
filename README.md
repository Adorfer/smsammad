# SMSammad

[![Tests](https://github.com/Adorfer/smsammad/actions/workflows/tests.yml/badge.svg)](https://github.com/Adorfer/smsammad/actions/workflows/tests.yml)

Bindeglied zwischen [Zammad](https://zammad.org/) und einem
[Teltonika RUT240](https://teltonika-networks.com/products/routers/rut240)
als SMS-Gateway: bidirektional, beide Richtungen über die jeweilige
HTTP-API (kein E-Mail-/POP3-Bezug, siehe
[Warum HTTP-API statt E-Mail/POP3](#warum-http-api-statt-e-mailpop3)).

[Zammad](https://zammad.org/) ist ein quelloffenes, selbst gehostetes
Ticket-/Helpdesk-System der deutschen Zammad GmbH; im Umfeld des Autors
wird es seit Jahren erfolgreich selfhosted in mehreren Projekten
eingesetzt. 

Der [Teltonika RUT240](https://teltonika-networks.com/products/routers/rut240)
ist ein kompakter industrieller 4G/LTE-Router mit SIM-Karten-Steckplatz,
der über seine Web-API u.a. auch als SMS-Gateway ansprechbar ist (SMS
senden/empfangen/verwalten über das eingebaute Mobilfunkmodem) — genau
diese Funktion nutzt dieses Projekt.

## Overview (English)

This is a personal-use integration between the Zammad ticketing system and
a Teltonika RUT-series router's SMS gateway, built and iteratively
hardened against a real device and a real Zammad instance. It was written
to scratch a specific itch (German-language support workflows over SMS via
a Vodafone Callya prepaid SIM) and the documentation below is therefore in
German throughout.

That said: if you're interested in **internationalizing** this (e.g.
English-language code comments/log output as a build option) or in
**adapting the prepaid-balance monitoring feature to a different mobile
provider** than the Vodafone Callya reply format this was built against,
feel free to reach out — the codebase is small and modular enough that
either should be a reasonably contained change. See
[Guthaben-Überwachung](#guthaben-überwachung-optional) for the
provider-specific part.

## Über dieses Projekt

Dies ist eine **Eigenbedarfs-Entwicklung**: gebaut für eine konkrete
Konstellation (eine Zammad-Instanz, ein Teltonika RUT240 mit einer
Vodafone-Callya-Prepaid-SIM als SMS-Gateway -- warum überhaupt Hardware
statt eines Cloud-SMS-API-Anbieters: siehe
[Warum ein Hardware-SMS-Gateway](#warum-ein-hardware-sms-gateway-statt-eines-cloud-sms-api-anbieters))
und iterativ gegen genau diese beiden echten Systeme entwickelt und
gehärtet — nicht als generisches, für beliebige Provider/Zammad-
Installationen vorab abstrahiertes Produkt. Deutschsprachige
Code-Kommentare, Log-Ausgaben und diese Dokumentation sind entsprechend
bewusst so gewählt.

Falls Interesse an **Internationalisierung** (z.B. englischsprachige Logs/
Kommentare als Option) oder an **Anpassung der Guthaben-Überwachung an
einen anderen Mobilfunk-Anbieter** als das hier verwendete Vodafone-Callya-
Antwortformat besteht: gerne melden. Die Codebasis ist klein und modular
genug, dass beides mit überschaubarem Aufwand machbar sein sollte.

## Architektur-Überblick

Drei/vier unabhängige, per Cron gepollte Richtungen, ein gemeinsamer
Zammad-Client (`zammad.py`) und Teltonika-Client (`teltonika.py`):

- **Zammad → SMS** (`ticket-to-sms`): Tickets mit Tag `sms-out` werden per
  Cronjob-Poll gefunden, der Artikeltext des letzten öffentlichen
  Agenten-Anrufartikels wird als SMS über die Teltonika-cgi-bin-API
  verschickt.
- **SMS → Zammad** (`sms-to-ticket`): eingehende SMS werden per Cronjob-
  Poll vom Router geholt, legen ein Zammad-Ticket an bzw. hängen sich an
  ein bestehendes offenes Ticket des Absenders — und werden danach vom
  Router gelöscht.
- **Guthaben-Überwachung** (`balance-check`, optional): tägliche
  Guthaben-Abfrage der Prepaid-SIM-Karte, per Default synchron per USSD
  (RutOS-REST-API), alternativ per SMS (Antwort wird von `sms-to-ticket`
  ausgewertet) -- mit automatischem Fallback auf die jeweils andere
  Methode bei Fehlern der Default-Methode. Siehe
  [unten](#guthaben-überwachung-optional).
- **Statistik** (`stats`, on-demand/wöchentlich per Cron): reine
  Auswertung der lokalen SQLite-Datenbank, keine Zammad-/Teltonika-
  Zugriffe nötig.

Jede Richtung ist ein eigenes Modul (`sms_to_ticket.py`, `ticket_to_sms.py`,
`balance_check.py`, `stats_report.py`) mit einer einzigen `run(...)`-
Funktion, orchestriert von `main.py`. Fuer die Guthaben-Ueberwachung kommen
zwei weitere Module dazu: `teltonika_api.py` (RutOS-REST-API-Client fuer
USSD, ein komplett anderes Protokoll als der cgi-bin-Client `teltonika.py`)
und `balance_ticket.py` (die Zammad-Ticket-Eskalationslogik, gemeinsam
genutzt von USSD- und SMS-Weg). Zustand (Rate-Limit-Zähler,
Statistik-Rohdaten, Guthaben-Verlauf) liegt in einer einzigen SQLite-Datei
(`sms_budget.py`), s. [Warum SQLite](#warum-sqlite-für-budgetstatistik).

## Warum diese Design-Entscheidungen

Ein paar Entscheidungen sind nicht offensichtlich und wurden bewusst so
getroffen — meist, weil ein naiverer Ansatz live gegen die echten Systeme
tatsächlich fehlgeschlagen ist. Hier die Begründungen, damit sie bei
künftigen Änderungen nicht versehentlich wieder rückgängig gemacht werden.

### Warum ein Hardware-SMS-Gateway statt eines Cloud-SMS-API-Anbieters

Die naheliegende Alternative zu einem physischen Router als SMS-Gateway
wäre einer der üblichen Cloud-SMS-API-Anbieter gewesen. Diese akzeptieren
nach Erfahrung des Autors jedoch keine eingetragenen Vereine als
Kundschaft: Die Anbieter unterliegen regulatorischen Auflagen der
Behörden (u.a. zur Identifizierung der Verantwortlichen), die sich mit
den Unterlagen, die deutsche Registergerichte für Vereine ausstellen,
formal nicht erfüllen lassen. Konkret: Im Vereinsregisterauszug stehen
die persönlichen Daten der Vorstandsmitglieder (Geburtsdatum, -ort) nicht
drin, und auch eine beigefügte Satzung mit entsprechenden Eintragungen
genügt den formalen Prüfkriterien der Anbieter nicht. Nach vielen Runden
und Wochen der Eskalation an den Hotlines zweier verschiedener
SMS-Cloud-Anbieter wurde dieser Weg schließlich aufgegeben. Ein eigenes,
physisches SMS-Gateway (Router + Prepaid-SIM) umgeht das Problem
komplett.

Und warum dann ausgerechnet ein vergleichsweise teurer industrieller
Router statt einer günstigeren Hardware-Lösung? Zwei naheliegende
Alternativen wurden vorher ausprobiert und wieder verworfen:

- **Altes Smartphone dauerhaft am USB-Kabel**: scheitert mittelfristig an
  der Akku-Problematik -- ein Akku, der dauerhaft am Ladegerät hängt,
  degradiert und fällt irgendwann aus, das Gerät wird damit zum
  Wartungsfall statt zur "install and forget"-Lösung.
- **Handelsübliche USB-Mobilfunk-Dongles**: zwei Probleme gleichzeitig.
  Erstens ist der Mobilfunkempfang an einem typischen fensterlosen
  Server-/Kellerstandort für einen kleinen internen Dongle-Antenne oft zu
  schwach. Zweitens sind reine UMTS/3G-Dongles nach der UMTS-Abschaltung
  faktisch ausgestorben, und die aktuell erhältlichen "Dongles" sind in
  Wirklichkeit meist eigenständige kleine IP-Router ohne dokumentierte
  Mobilfunk-/SMS-API (die USB-Schnittstelle liefert nur noch eine
  virtuelle Netzwerkkarte für den Internetzugang, keinen AT-Kommando-
  Zugriff mehr).

Ein industrieller Router wie der RUT240 ist dagegen für exakt diesen
Einsatzzweck gebaut: dauerbetriebstauglich (kein Akku), robuste
Bauform, externe Antennenanschlüsse (löst das Empfangsproblem im
Keller), und eine langzeitstabile, dokumentierte (wenn auch lückenhafte,
siehe unten) API.

### Warum HTTP-API statt E-Mail/POP3

Der Router kann SMS grundsätzlich auch per E-Mail (SMS→Mail-Weiterleitung,
Mail→SMS-Versand) koppeln. POP3 als Abholmechanismus dafür gilt als
veraltet und bringt zusätzliche Abhängigkeiten (Mailserver-Konfiguration
auf beiden Seiten, weniger strukturierte Fehlerbehandlung). Die
cgi-bin-Web-API des Routers bietet dieselbe Funktionalität direkt über
HTTP mit klar abgrenzbaren Endpunkten (`sms_send`, `sms_list`,
`sms_delete`, ...) und wurde daher vorgezogen.

### Teltonika-API-Dokumentation und ihre Lücken

Offizielle Doku:
[wiki.teltonika-networks.com/view/RUT240_SMS_Gateway](https://wiki.teltonika-networks.com/view/RUT240_SMS_Gateway).
Sie spezifiziert die Request-Parameter, aber **nicht** das Antwortformat.
Live gegen ein echtes RUT240 verifiziert (siehe `teltonika.py`):

- `sms_list` liefert **kein JSON**, sondern Klartext-Blöcke im Format
  `Feld: Wert`, durch Trennzeilen aus Bindestrichen getrennt (siehe
  `_parse_blocks`/`_FIELD_RE`/`_SEPARATOR_RE` in `teltonika.py`). Die
  plausiblere Annahme (JSON) war schlicht falsch und wurde erst beim
  echten Testlauf sichtbar.
- Ist das SMS-Gateway-Feature am Router deaktiviert, antwortet `sms_list`
  mit einem Text wie `"Disabled"` statt einer leeren Liste. Der Parser
  behandelt eine nicht-leere, aber inhaltlich nicht auswertbare Antwort
  deshalb bewusst als **Fehler**, nicht als "0 SMS" — sonst würde eine
  falsche Router-Konfiguration nie aktiv auffallen, sondern sich nur als
  dauerhaft leerer Posteingang zeigen.
- Das Gerät ist im Praxisbetrieb mit getrennten Netzsegmenten im Homelab
  **nur per HTTPS mit selbstsigniertem Zertifikat** erreichbar (Port 80 
  typischerweise zu). `verify_tls=false` ist daher der praktikable Default, 
  mit einer WARNING-Logzeile beim Start, damit das nicht versehentlich unbemerkt bleibt.
- `sms_read`/`sms_total` sind nur gegen die Doku umgesetzt, nicht am
  echten Gerät verifiziert (werden von den aktuellen Abläufen nicht
  gebraucht) — falls sie mal gebraucht werden, zuerst live gegenprüfen.
- **Schade**: `sms_send` bietet -- soweit in diesem Projekt genutzt/
  erkundet -- keine Möglichkeit, einen längeren Text automatisch als
  korrekt verkettete Mehrteil-SMS (GSM-Concatenated-SMS/UDH) zu
  versenden; jeder Teil geht als eigenständige einzelne SMS raus.
  Deshalb übernimmt `sms_split.py` das Aufteilen selbst und setzt
  lesbare `"(1/3)"`-Präfixe vor jeden Teil, statt dass das Empfänger-
  Handy die Teile automatisch zu einer Nachricht zusammenfügt (siehe
  [SMS-Versand-Verhalten](#sms-versand-verhalten)). Ob eine vom Kunden
  gesendete, im Mobilfunknetz korrekt verkettete Mehrteil-SMS beim
  Empfang durch Router/Modem automatisch wieder zusammengesetzt würde,
  ist in diesem Projekt **nicht verifiziert** (bislang kein solcher Fall
  real beobachtet) -- wünschenswert wäre es so oder so: sowohl der eigene
  Splitting-Code als auch die für den Kunden unübersichtliche Ankunft
  mehrerer einzelner SMS-Nachrichten hintereinander wären damit
  überflüssig.

### Credential-Sicherheit beim Teltonika-Zugriff

Die Teltonika-API verlangt Benutzername/Passwort **als Query-Parameter in
der URL** (von der API so vorgegeben, nicht verhandelbar). Das ist ein
eingebautes Leak-Risiko: jede Exception aus `requests`/`urllib3`, die
ungefiltert geloggt oder per Traceback/Fehlermail verschickt wird, enthält
sonst die volle URL inklusive Zugangsdaten. Deshalb in `teltonika.py`:

- `raise TeltonikaError(...) from None` beim Abfangen von
  `requests.RequestException` — bewusst **ohne** Exception-Chaining, damit
  die Original-Exception (die die URL enthält) nirgends im
  Traceback/in der Fehlermail landet.
- `urllib3`/`requests` werden in `logging_setup.py` **auch bei
  `--verbose`** hart auf `WARNING` gesetzt, weil deren `DEBUG`-Level sonst
  die komplette Request-URL inkl. Zugangsdaten mitloggen würde.

### Warum `run.py` und nicht `smsammad.py` als Launcher

Ein Launcher-Skript direkt im Projekt-Root mit demselben Namen wie das
Package (`smsammad.py`) verdeckt beim Import das eigentliche
Package gleichen Namens (Python findet die lokale Datei zuerst). Deshalb
heißt das Root-Skript bewusst `run.py`.

### Rufnummer-Erkennung und Kurzwahlen

`phonenumbers` (libphonenumber) hält manche kurze Ziffernfolgen
fälschlich für gültige Rufnummern — in Deutschland existieren legitime
kurze Dienstenummern, sodass z.B. eine Netzbetreiber-Kurzwahl wie `224466`
als "gültige 6-stellige Nummer" durchgehen würde, obwohl es keine
anrufbare Teilnehmerrufnummer ist. Deshalb (`sms_to_ticket._looks_like_complete_number`)
wird ein direkter Parse-Versuch nur unternommen, wenn der rohe Absender
bereits wie eine vollständige Nummer aussieht (`+49...` oder `01...`);
alles andere geht über die konfigurierbare `short_number_prefix`-
Rekonstruktion bzw. landet unverändert als Kurzwahl:Absender-ID
(`unresolved_sender_prefix`, Default `"Kurzwahl:"`). Der Rückweg
(`ticket_to_sms._resolve_destination_number`) erkennt den Präfix wieder,
entfernt ihn und sendet roh an genau diesen Absender zurück — so
funktionieren auch Antworten an Kurzwahlen oder alphanumerische
Absender-IDs (z.B. `"CALLYA"`) ohne Sonderfall im Zammad-Kundendatensatz.

### Zammad-Suchtokenisierung

Zammads Volltextsuche (`/api/v1/users/search`) tokenisiert Werte an
nicht-alphanumerischen Zeichen und matcht nur **ganze Tokens**. Eine
durchgehende Ziffernfolge wie `"491721234567"` trifft deshalb **nicht**
auf einen als `"+49 172 1234567"` gespeicherten Wert — dort ist
`"1234567"` das relevante Token. `zammad._search_token()` extrahiert
deshalb das letzte alphanumerische Token, die Suche läuft mit
beidseitigem Wildcard (`*token*`), und die Kandidaten werden anschließend
clientseitig über `_phone_matches()` eindeutig verifiziert (erst exakter
Treffer, dann `phonenumbers`-normalisierter Vergleich) — die Suche selbst
ist bewusst breiter als nötig, um überhaupt Treffer zu bekommen.

### Zammad-Tag-API-Asymmetrie

`tags/add` erwartet `POST`, `tags/remove` dagegen laut Zammad-API `DELETE`
— unterschiedliche HTTP-Methoden für symmetrisch klingende Endpunkte, kein
Copy-Paste-Fehler (`zammad.add_tag`/`zammad.remove_tag`). Live als 404
aufgefallen, als `remove_tag` anfangs ebenfalls `POST` nutzte.

### Zammad-Trigger: warum `sender == Agent` zwingend nötig ist

Zammad hat keinen nativen SMS-Kanal. Die etablierte Konvention: Agenten
antworten im **"Anruf"**-Tab eines Tickets, öffentlich (nicht intern); ein
Zammad-Trigger setzt darauf den Tag `sms-out`, den `ticket-to-sms` dann
abholt. Zwei live beobachtete Fehler, die zur aktuellen, strengeren Logik
geführt haben:

1. **Echo an den Kunden**: Bei einem neu angelegten Ticket ist der erste
   öffentliche Anruf-Artikel die **eingehende Kundennachricht selbst**
   (`sender="Customer"`). Ohne Prüfung auf `sender == "Agent"` hätte der
   Trigger (bzw. `ticket_to_sms._process_one`) diese SMS unverändert an
   den Kunden zurückgeschickt. Live reproduziert (Ticket mit der SMS
   "Zweiter Test von Retter", die postwendend an den Absender zurückging).
2. **Endlosschleife durch eigene Quittungs-Notizen**: Die
   Versand-Quittungs-Notiz (`ticket_to_sms._build_send_note`) wurde
   anfangs mit demselben `type="phone"` wie eine echte Antwort abgelegt.
   Ein Zammad-Trigger, der auf "neuer öffentlicher Anruf-Artikel" reagiert,
   feuerte dadurch **auf die eigene Quittung erneut** → SMS wurde 3x
   identisch verschickt (live beobachtet). Fix: alle eigenen System-/
   Audit-Vermerke laufen über `article_type="note", sender="Agent"`
   (siehe `zammad.add_article`) statt über den Default `"phone"`/
   `"Customer"`.

Der Zammad-Trigger selbst muss daher exakt so konfiguriert sein: Aktion =
erstellt, Typ = Telefon, Sichtbarkeit = öffentlich, **Absender = Agent**
(siehe [Zammad-Trigger-Setup](#zammad-trigger-für-ausgehende-sms)).

### Warum SQLite für Budget/Statistik

Die Rate-Limit-Logik (SMS/Stunde, SMS/24h) brauchte anfangs nur einen
rollierenden Zähler und lag in einer flachen JSON-Datei mit
`fcntl.flock`. Sobald zusätzlich Auswertungen "wie viele SMS pro Gruppe/
Agent in den letzten X Tagen" (Budget-Warn-Mail-Aufschlüsselung,
`stats`-Subcommand, Guthaben-Verlaufsanalyse) dazukamen, wurde das mit
einer flachen Liste unhandlich und ineffizient — SQLite mit `GROUP BY`/
Zeitraum-Abfragen ist hier direkt passender, regelt Nebenläufigkeit selbst
(`timeout=` beim Connect) und macht das eigene Locking überflüssig.
`sms_budget.py` bündelt daher **beides** in einer Datei: Rate-Limiting
(`sms_events`), Statistik-Rohdaten (dieselbe Tabelle) und
Guthaben-Verlauf (`balance_history`) plus ein `meta`-Key/Value-Store für
Cooldown-Zeitstempel.

### Warum USSD ueber eine eigene REST-API statt ubus/gsmctl

Fuer die Guthaben-Abfrage per USSD (`*100#`) wurden live mehrere Wege
gegen das echte RUT240 (RutOS `RUT2_R_00.07.06.21`) durchprobiert:

- **cgi-bin** (wie bei SMS): kein USSD-Endpunkt vorhanden -- alle
  geratenen Pfadnamen (`ussd_send`, `ussd`, ...) landeten im generischen
  WebUI-HTML-Fallback statt in einer echten Antwort (Vergleichstest mit
  dem bekannt funktionierenden `sms_list`-Endpunkt bestaetigt das).
- **SSH + `gsmctl -U "*100#"`**: laut Teltonika-Community/-Wiki
  dokumentiert und funktionsfaehig, aber SSH war am Router (Port 22)
  nicht aktiviert -- haette eine zusaetzliche Konfigurationsaenderung am
  Router gebraucht.
- **`ubus`-JSON-RPC** (`POST /ubus`, `session.login`): Login mit dem
  bestehenden cgi-bin-SMS-Account schlug mit `PERMISSION_DENIED` fehl.
- **Die tatsaechlich von der RutOS-WebUI selbst genutzte REST-API**
  (`/api/login`, `/api/modems/<id>/actions/send_ussd`, Bearer-Token) --
  gefunden, indem der USSD-Request im Browser-Netzwerk-Tab waehrend einer
  echten Abfrage ueber die WebUI mitgeschnitten wurde (Teltonikas eigene
  Entwickler-Doku unter developers.teltonika-networks.com ist eine
  JS-Single-Page-App ohne per einfachem HTTP-Fetch auslesbaren Inhalt).
  Dieser Weg funktioniert und wird genutzt, siehe `teltonika_api.py`.

Auch hier scheiterte der bestehende cgi-bin-Account (`401 Login failed`)
-- die REST-API brauchte einen eigenen, dediziert dafuer berechtigten
Router-Account (siehe [Guthaben-Überwachung](#guthaben-überwachung-optional)).
Konsequenz: `teltonika_api.py` ist bewusst ein **eigener** Client, nicht
in `teltonika.py` integriert -- anderes Protokoll (Bearer-Token statt
Query-Param-Auth), andere Zugangsdaten, andere Fehlerklasse
(`TeltonikaApiError` statt `TeltonikaError`).

### Warum der USSD-Token nicht gecacht wird

Der Login-Response liefert `expires: 299` (Sekunden, live beobachtet) --
mit maximal einer Abfrage pro Tag lohnt sich weder Caching noch
Refresh-Logik: `teltonika_api.py` loggt sich bei jedem `send_ussd()`-
Aufruf frisch ein.

### Warum `balance_ticket.py` als eigenes Modul

Die Ticket-Eskalationslogik (Stufen ok/warn/alarm, Betreff/Prioritaet/
State setzen, Notiz schreiben, Wert in der Statistik-DB speichern) soll
fuer USSD- und SMS-Weg **identisch** sein, obwohl beide strukturell
verschieden ablaufen: USSD ist synchron (Abfrage und Auswertung im selben
`balance-check`-Lauf, `balance_check.py`), SMS ist asynchron (Antwort
trifft irgendwann spaeter per `sms-to-ticket` ein, `sms_to_ticket.py`).
Statt die Logik zu duplizieren oder eines der beiden Module vom anderen
abhaengig zu machen, liegt sie in einem dritten, von beiden importierten
Modul (`balance_ticket.apply_balance_result`). Fuer USSD-Ergebnisse gibt
es dabei einen eigenen, festen Pseudo-Kunden-Identifikator
(`USSD_PSEUDO_CUSTOMER_ID = "USSD-Guthaben"`) statt der SMS-Absender-
basierten ID (`"Kurzwahl:<reply_sender>"`) -- bewusst getrennt, damit ein
spaeterer Wechsel der Default-Methode nicht ploetzlich das offene Ticket
des jeweils anderen Wegs uebernimmt.

### Warum automatischer Fallback nur einmalig und nur wenn konfiguriert

Schlaegt die konfigurierte Default-Methode fehl (Zugriff verweigert,
z.B. `403`/`401`, ODER die Antwort laesst sich nicht parsen), wechselt
`balance_check.run()` fuer **diesen einen Lauf** automatisch auf die
jeweils andere Methode -- aber nur, wenn deren Zugangsdaten ebenfalls in
der `config.ini` hinterlegt sind (sonst wuerde der Fallback selbst sofort
wieder scheitern, nur mit einer unklareren Fehlermeldung). Ohne
konfigurierten Fallback laeuft der Fehler ganz normal durch den
bestehenden Fehlerpfad (Exception -> Fehlermail via `main.py`) --
bewusst **kein** stilles Schlucken, gerade weil laut Erfahrung aus diesem
Projekt (siehe Guthaben-Abschnitt unten) sowohl das SMS-Antwortformat als
auch das USSD-Menü jederzeit vom Provider geändert werden können und ein
Parse-Fehler dann schnell auffallen soll.

### Warum `on_overflow` konfigurierbar ist (reject/truncate)

Ein Agententext, der mehr SMS-Teile ergibt als `max_sms_parts`, kann auf
zwei sinnvolle Arten behandelt werden: gar nicht senden (der Agent muss
kürzen) oder die ersten `max_sms_parts` Teile trotzdem senden (Rest geht
verloren, aber der Kunde bekommt wenigstens den Anfang). Welches
Verhalten richtig ist, hängt vom Einsatzkontext ab — deshalb Config-
Schalter statt fest codierter Entscheidung.

### Budget-Wartehinweis: warum nur einmalig

Ist das Sende-Budget erschöpft, bleibt der Tag `sms-out` stehen und das
Ticket wird beim nächsten Cronlauf automatisch erneut versucht. Ohne
Schutz würde bei jedem Lauf (alle paar Minuten) erneut eine "Budget
erschöpft"-Notiz an das Ticket gehängt werden, solange der Engpass
anhält. Der Tag `sms-budget-warten` markiert daher "Hinweis wurde bereits
einmalig hinterlegt" und wird erst beim nächsten **erfolgreichen** Versand
wieder entfernt (`ticket_to_sms._handle_budget_blocked`/`_send`) —
dasselbe Muster wie der Cooldown für die Budget-Überschreitungs-Mail
(`should_notify`/`mark_notified`).

### Dry-Run: warum er wirklich seiteneffektfrei ist

`--dry-run` darf keine echten Zammad-Kunden anlegen. Der reguläre Pfad
nutzt `find_or_create_customer_by_phone`, das bei unbekanntem Absender
einen neuen Kunden **anlegt**. Der Dry-Run-Zweig in `sms_to_ticket.py`
nutzt deshalb bewusst die rein lesende `find_customer_by_phone` — ein
früherer Bug, bei dem `find_or_create_...` versehentlich auch im Dry-Run
aufgerufen wurde, hat mehrere "Geister"-Testkunden im echten Zammad
hinterlassen, bevor das korrigiert wurde.

### Content-Redaction im Log

SMS-/Ticket-**Inhalte** (nicht Rufnummern) werden im Log ab dem 5. Zeichen
mit `#` überschrieben (`logging_setup.redact_content`) — Länge bleibt für
Debugging sichtbar, der Inhalt selbst nicht. Die verbleibenden sichtbaren
ersten 5 Zeichen sind zusätzlich **ROT13**-verschlüsselt: kein echter
Schutz (trivial umkehrbar), aber sie werden beim Überfliegen eines Logs
nicht mehr unbewusst mitgelesen. Hintergrund: Logs landen unter
`/var/log/`, das potenziell einen größeren Adminkreis erreicht als
Zammad/Mailserver-Zugriff selbst.

## Setup

Keine venv/pip-Installation nötig — Abhängigkeiten kommen aus
Ubuntu-Paketen (bewusste Entscheidung gegen ein zusätzliches
Python-Umgebungs-Management für ein so kleines, einzeln deploytes Tool):

```bash
sudo apt-get install python3-requests python3-phonenumbers python3-pytest python3-responses
```

Config anlegen (siehe `config.ini.example` für alle Optionen und deren
Kommentare):

```bash
cp config.ini.example config.ini
chmod 600 config.ini
$EDITOR config.ini
```

`config.ini` enthält Zugangsdaten (Router-Passwort, Zammad-Token,
SMTP-Passwort) und **muss** `600` (nur Owner lesbar) bleiben — wird beim
Start aktiv geprüft (`config._check_permissions`), ein zu offener Modus
lässt die App mit klarer Fehlermeldung abbrechen statt still weiterzulaufen.
Alle Text-Werte in der Config stehen bewusst in Anführungszeichen (`"..."`
oder `'...'`, beides geht) — vermeidet Rückfragen zum Escaping von
Leerzeichen in Passwörtern/Gruppennamen/Pfaden; Zahlen und `true`/`false`
bleiben unquotiert.

## Ausführen

```bash
python3 run.py ticket-to-sms --config config.ini
python3 run.py sms-to-ticket --config config.ini
python3 run.py balance-check --config config.ini
python3 run.py stats --config config.ini
```

`--dry-run` für einen Testlauf ohne Seiteneffekte (keine Zammad-/
Router-Änderungen, keine Mails), `--verbose` für Debug-Logging. Globale
Flags (`--config`, `--dry-run`, `--verbose`) müssen dem Subcommand
**vorangehen** (argparse-Subparser-Eigenheit).

## Konfigurationsabschnitte

Vollständige, kommentierte Referenz: `config.ini.example`. Kurzüberblick:

| Sektion | Zweck |
|---|---|
| `[teltonika]` | Router-Zugang, TLS-Verhalten, Rufnummer-Region, Kurzwahl:Präfixe |
| `[zammad]` | Zammad-Zugang, Gruppen für bekannte/unbekannte Absender, Telefonfeld |
| `[ticket_to_sms]` | SMS-Teile-Limit, Überlauf-Verhalten, Sende-Budget, SQLite-Pfad |
| `[balance]` | optional, USSD (Default) oder SMS, siehe [Guthaben-Überwachung](#guthaben-überwachung-optional) |
| `[notification]` | optional, Fehler-/Statistik-Mails; Abschnitt weglassen = dauerhaft deaktiviert |

## Zammad-Trigger für ausgehende SMS

Agenten antworten im **"Anruf"**-Tab eines Tickets, öffentlich (nicht
intern) — Zammad hat sonst keinen SMS-Kanal. Ein Zammad-Trigger muss
darauf den Tag `sms-out` setzen:

- Bedingung: Aktion = erstellt, Typ = Telefon, Sichtbarkeit = öffentlich,
  **Absender = Agent** (zwingend nötig, siehe
  [Begründung oben](#zammad-trigger-warum-sender-agent-zwingend-nötig-ist)
  — ohne diese Einschränkung matchen auch eingehende SMS oder eigene
  System-Vermerke fälschlich).
- Aktion: Tag `sms-out` hinzufügen.

## Für Zammad-Agenten: SMS manuell verschicken

Eigenständige Kurzanleitung für den praktischen Alltag am Ticket -- bewusst
mit gewissen Redundanzen zu den technischen Abschnitten oben, damit sie
für sich allein funktioniert. Willst du "zu Fuß" eine SMS an einen Kunden
schicken, gehe so vor:

1. **Kunde anlegen** (falls noch nicht vorhanden) -- wichtig: die
   **Mobiltelefonnummer** muss im entsprechenden Feld eingetragen sein,
   sonst kann keine SMS zugestellt werden.
2. **Neues Ticket** in der Hotline-Queue anlegen, als Artikeltyp
   **"Anruf"** -- wirklich **nicht** "E-Mail"! Nur Anruf-Artikel werden
   überhaupt als SMS erkannt und verschickt.
3. **Betreff ist egal** -- der wird NICHT als SMS verschickt, nur der
   eigentliche Artikeltext.
4. **Bitte kurz fassen**: jede angefangene SMS von 160 Zeichen kostet
   Geld, und mehrteilige SMS kommen beim Empfänger als mehrere einzelne
   Nachrichten hintereinander und unübersichtlich an.
5. Nach Klick auf "Aktualisieren" sollte binnen weniger Sekunden
   automatisch (per Zammad-Trigger) der Tag `sms-out` erscheinen. Falls
   das ausnahmsweise mal nicht klappt: den Tag `sms-out` einfach selbst
   über das "+"-Feld bei den Tags ergänzen.
6. Nach dem nächsten SMSammad-Durchlauf (alle paar Minuten, üblicherweise
   nach rund 3-10 Minuten) erscheint unter der SMS eine **interne Notiz**
   mit Versandstatus und dem **exakt gesendeten Wortlaut** -- bitte kurz
   gegenprüfen, ob z.B. Umlaute/Sonderzeichen korrekt angekommen sind oder
   der Text (je nach Konfiguration) gekürzt wurde, weil er zu lang war.

## Für Zammad-Agenten: Neues Ticket oder bestehendes bei ankommender SMS?

Wenn eine SMS eines Kunden ankommt, entscheidet SMSammad automatisch nach
diesen Regeln:

**Bestehendes Ticket bekommt einen neuen Artikel**, wenn der Kunde
(anhand seiner Mobilnummer) bereits ein Zammad-Kundenkonto **und**
mindestens ein **offenes** Ticket hat (Status nicht "geschlossen" oder
"zusammengeführt"). Gibt es mehrere offene Tickets, wird das mit dem
jüngsten Kundenkontakt gewählt.

**Neues Ticket wird angelegt**, wenn der Absender entweder komplett neu
ist (noch kein Zammad-Kundenkonto) **oder** zwar ein Kundenkonto hat,
aber **kein offenes** Ticket (alle bisherigen sind geschlossen/
zusammengeführt).

**In welche Gruppe/Queue das neue Ticket kommt:**

- **Komplett neuer Kunde** → Gruppe aus `[zammad] new_customer_group`
  (die "unbekannt"/Triage-Queue).
- **Bekannter Kunde ohne offenes Ticket** → Gruppe aus `[zammad] group`
  -- **immer** diese eine feste Default-Gruppe, **unabhängig davon**, in
  welcher Queue frühere (geschlossene) Tickets dieses Kunden lagen! Es
  gibt aktuell **keine** Logik, die die Queue des zuletzt aktiven/
  bearbeiteten Tickets wiederverwendet. Beispiel: War das letzte Ticket
  eines Kunden in der Queue "Technik" und wurde geschlossen, landet die
  nächste SMS desselben Kunden trotzdem in der allgemeinen
  Default-Gruppe (`[zammad] group`), nicht automatisch wieder in
  "Technik". Falls das fachlich nicht passt: bitte manuell in die
  richtige Queue verschieben.

## Für Zammad-Admins: SMSammad-User einrichten

SMSammad braucht einen eigenen Zammad-Benutzer mit API-Token-Zugriff
(`[zammad] token` in `config.ini`):

1. Neuen Benutzer anlegen (z.B. "SMSammad" o.ä.), Rolle **Agent**.
2. Über sein Profil einen **API-Token** generieren (Token Access) mit
   Zugriff auf Ticket-/Artikel-/Tag-/Benutzer-Objekte.
3. Dem Benutzer **vollen Zugriff auf mindestens die Queues** geben, in
   denen er arbeiten soll (die unter `[zammad] group`/`new_customer_group`
   konfigurierten Gruppen) -- ohne diese Berechtigung schlagen
   Ticket-/Artikel-Aktionen trotz gültigem Token mit Rechtefehlern fehl
   (Zammads Gruppen-Berechtigungen sind unabhängig von der Rolle).

**Fallstrick im Zammad-Admin-UI**: Beim Ergänzen einer weiteren
Queue-Berechtigung reicht es **nicht**, die Gruppe im Dropdown
auszuwählen und rechts auf **"Hinzufügen"** zu klicken -- das trägt die
Auswahl erstmal nur lokal ins Formular ein. Erst ein Klick auf
**"Übermitteln"** unten rechts speichert die Änderung tatsächlich. Wird
das vergessen, wirkt die Berechtigung im UI gesetzt, ist aber nie
gespeichert worden.

## SMS-Versand-Verhalten

- **Aufteilung** (`sms_split.split_for_sms`): lange Texte werden an
  Wortgrenzen in Teile ≤150 Zeichen (inkl. `"(N/M) "`-Präfix ab dem
  zweiten Teil) zerlegt -- manuell, weil die Teltonika-API das nicht
  automatisch als verkettete Mehrteil-SMS beherrscht, siehe
  [Schade-Punkt oben](#teltonika-api-dokumentation-und-ihre-lücken).
- **Überlauf** (`[ticket_to_sms] on_overflow`): mehr Teile als
  `max_sms_parts` → `reject` (nichts senden, Tag `sms-overflow`, Priorität
  hoch, Agent muss kürzen) oder `truncate` (erste `max_sms_parts` Teile
  trotzdem senden, Notiz weist auf die Kürzung hin).
- **Sende-Budget** (`max_sms_per_hour`/`max_sms_per_24h`, rollierende
  Fenster, keine Kalenderstunden/-tage): bei Erschöpfung bleibt das
  Ticket getaggt und wird automatisch erneut versucht, siehe
  [Budget-Wartehinweis](#budget-wartehinweis-warum-nur-einmalig).
- **Versand-Quittung**: jede erfolgreich gesendete SMS bekommt eine
  interne Notiz mit Zeichenzahl, Teilanzahl, Zeitstempel und dem exakt
  gesendeten Text (jeder Teil einzeln in `"..."`, damit Anfang/Ende klar
  erkennbar sind) — die Notiz bestätigt nur die Übergabe an den Router,
  eine **SMS-Quittung** (Zustellbestätigung durch die Teltonika-API) gibt
  es nicht und ist auch nicht in Aussicht.

## SMS-Empfangs-Verhalten

- **Kunde/Ticket-Zuordnung**: bekannter Absender mit offenem Ticket →
  Artikel wird angehängt; unbekannter Absender oder nur geschlossene
  Tickets → neues Ticket (Gruppe je nachdem `[zammad] group` oder
  `new_customer_group`).
- **Betreff neuer Tickets**: `"Neues SMS-Ticket: {Textauszug}"` --
  `sms_to_ticket._subject_excerpt` nimmt die ersten 50 Zeichen der SMS;
  bei längeren Texten nur die ersten 46 Zeichen gefolgt von `"[..]"`
  (Gesamtlänge bleibt bei 50), damit der Betreff in der Zammad-
  Ticketliste auf einen Blick erkennbar ist, statt nur "-" oder eines
  festen Platzhalters zu zeigen. Gilt nur für neu angelegte Tickets --
  ein Artikel an ein bestehendes offenes Ticket ändert dessen Betreff
  nicht.
- **Empfangs-Zeitstempel**: der vom Router gemeldete Zeitstempel (Format
  laut `sms_list`-Antwort, vermutlich Empfangszeit am Modem) wird als
  `"\n---\nSMS-Empfang: <Zeitstempel>"` an den Artikeltext angehängt.
- Nach erfolgreicher Verarbeitung wird die SMS per `sms_delete` vom
  Router gelöscht (verhindert Doppelverarbeitung im nächsten Cronlauf).

## Guthaben-Überwachung (optional)

`[balance]` in `config.ini` konfigurieren (siehe `config.ini.example`),
dann fragt `balance-check` einmal täglich das Guthaben der Prepaid-SIM-
Karte im Router ab. Ohne diese Sektion ist das Feature komplett inaktiv,
alles andere verhält sich unverändert. Zwei Wege, per `method` wählbar:

### `method = "ussd"` (Default)

Synchron per RutOS-REST-API (`teltonika_api.py`): sendet den USSD-Code
(Default `*100#`) an `/api/modems/<modem_id>/actions/send_ussd`, wertet
die Antwort **im selben Lauf** aus (kein Warten auf eine Antwort-SMS
nötig), i.d.R. kostenlos. Details/Herleitung dieses Wegs siehe
[Warum USSD ueber eine eigene REST-API](#warum-ussd-ueber-eine-eigene-rest-api-statt-ubusgsmctl).

Braucht einen **eigenen, dedizierten Router-Account** mit Zugriff auf die
Mobile/USSD-API -- live verifiziert, dass der bestehende cgi-bin-
SMS-Account (`[teltonika]`) dort **keinen** Zugriff hat (`401` beim
Login). Einrichtung am Router:

1. WebUI → System → Administration → Users → neuen User anlegen
   (Username/Passwort → `api_username`/`api_password` in `config.ini`).
2. Dem User explizit Zugriff auf **Network → Mobile** geben (ohne diese
   Berechtigung gelingt zwar der Login, aber die eigentliche
   USSD-Aktion liefert `403 Unauthorized` -- live so beobachtet, bis die
   Berechtigung ergänzt wurde).
3. `modem_id` prüfen: sichtbar in der Router-WebUI-URL unter
   Network → Mobile → General (Format `network/mobile/general/<id>`)
   bzw. im dort ausgelösten `/api/modems/<modem_id>/...`-Request
   (Browser-Entwicklertools → Netzwerk-Tab). Default `"1-1"` passt für
   die meisten Single-SIM-Geräte.

Der Betrag wird aus der USSD-Antwort per Regex geparst -- konfigurierbar
über `ussd_balance_regex` in `config.ini` (Default zugeschnitten auf das
Menü-Format von Vodafone Callya, z.B. `"Aktuelles Guthaben: 25,77 EUR"`).

### `method = "sms"`

Schickt eine Abfrage-SMS (z.B. "Guthaben" an die Kurzwahl `"111"`) an die
Prepaid-SIM-Karte. Die Antwort-SMS wird von `sms-to-ticket` automatisch
am konfigurierten `reply_sender` erkannt (roher SMS-Absender-Vergleich,
unabhängig von der sonstigen Rufnummer-Validierung) und **nicht** über
den normalen Kunden-Ticket-Pfad verarbeitet, sondern über
`_process_balance_reply` in `sms_to_ticket.py`. Der Betrag wird ebenfalls
per Regex geparst -- konfigurierbar über `sms_balance_regex` (Default
zugeschnitten auf `"Guthaben beträgt 0,98 Euro"`).

### Betrags-Regex: bewusst in `config.ini`, nicht im Code

**Beide Antwortformate sind Provider-Wortlaut-abhängig und damit fragil**
-- Vodafone/Callya kann den Text jederzeit ändern (auch um Werbung
einzufügen), das wird also vermutlich mehrfach nachgezogen werden müssen.
Damit das ohne Code-Änderung/Deploy geht, sind `ussd_balance_regex` und
`sms_balance_regex` normale `[balance]`-Optionen in `config.ini` (siehe
`config.ini.example`):

- Muss **genau eine** Erfassungsgruppe `(...)` für den Betrag enthalten
  (deutsches Komma als Dezimaltrenner, z.B. `25,77`); `re.IGNORECASE`
  wird immer angewendet.
- Wird beim Config-Laden validiert (`config._validate_balance_regex`):
  ein ungültiger regulärer Ausdruck oder eine fehlende Erfassungsgruppe
  lässt die App mit klarer Fehlermeldung abbrechen, statt erst beim
  nächsten Cronlauf mitten in der Verarbeitung zu scheitern.
- Findet der konfigurierte Regex zur Laufzeit trotzdem keinen Treffer
  (z.B. weil der Provider den Wortlaut zwischenzeitlich geändert hat),
  wird das **nicht** stillschweigend geschluckt, sondern wirft eine
  Exception, die über den normalen Fehlerpfad (Fehlermail) sichtbar wird
  -- siehe
  [Warum automatischer Fallback](#warum-automatischer-fallback-nur-einmalig-und-nur-wenn-konfiguriert).

Bei einem anderen Provider mit komplett abweichendem Ablauf (nicht nur
Wortlaut) siehe auch [Über dieses Projekt](#über-dieses-projekt) --
gerne melden.

### Automatischer Fallback

Sind **beide** Methoden vollständig konfiguriert, schaltet
`balance-check` bei einem Fehler der Default-Methode (Zugriffsfehler oder
nicht parsebare Antwort) für den jeweiligen Lauf automatisch auf die
andere um -- Details/Begründung siehe
[Warum automatischer Fallback](#warum-automatischer-fallback-nur-einmalig-und-nur-wenn-konfiguriert).

### Ticket-Handling (drei Stufen, unabhängig von der Abfragemethode)

Identisch für USSD und SMS (gemeinsame Logik in `balance_ticket.py`):

- Guthaben **≥ `warn_threshold_eur`**: Ticket wird geschlossen (Betreff
  "SMS-Guthaben", interne Notiz "noch ausreichend").
- **Zwischen** `alarm_threshold_eur` und `warn_threshold_eur`: Ticket
  bleibt offen, Priorität "normal", Betreff "SMS Guthaben sollte
  aufgeladen werden".
- **Unter** `alarm_threshold_eur`: Ticket bleibt offen, Priorität "high",
  Betreff "SMS-Guthaben KRITISCH niedrig - SMS-Versand gefährdet", UND
  jede SMS-Versand-Notiz (`ticket-to-sms`) bekommt zusätzlich den Hinweis
  "SMS-Guthaben ist sehr niedrig, SMS wurde evtl. nicht gesendet" (die
  Teltonika-API meldet einen fehlgeschlagenen Versand wegen leerem
  Guthaben nicht als Fehler, siehe [Warum HTTP-API](#warum-http-api-statt-e-mailpop3)-Abschnitt
  zur fehlenden Zustellbestätigung).

Technischer Kniff dahinter: die Pseudo-Kunde/Ticket-Kontinuität nutzt
für den SMS-Weg exakt dieselbe Infrastruktur wie normale Kurzwahl:
Absender (`unresolved_sender_prefix`, z.B. `"Kurzwahl:80808"`), für den
USSD-Weg einen festen Identifikator (`"USSD-Guthaben"`, siehe
[Warum balance_ticket.py](#warum-balance_ticketpy-als-eigenes-modul)) —
dadurch entsteht bei "ok" automatisch ein neues Ticket beim nächsten Mal
(das alte ist ja geschlossen), während bei "warn"/"alarm" sich der
nächste Lauf an dasselbe offene Ticket hängt und Betreff/Priorität
aktualisiert. Kein zusätzlicher Code für Reopen-Logik nötig. Die
Guthaben-Abfrage zählt bewusst **nicht** als Kundenkontakt in der
normalen Eingehend-Statistik (`budget.record_balance` statt
`budget.record_received`).

`closed_state_id` in `config.ini` muss zur eigenen Zammad-Installation
passen (Default `4` gilt für eine Standard-Zammad-Installation) — prüfen
via:

```bash
curl -H "Authorization: Token token=..." https://<zammad>/api/v1/ticket_states
```

Live an der echten Zammad-Instanz verifiziert: `state_id` "closed" = `4`,
`priority_id` "normal" = `2`, "high" = `3` (`GET /api/v1/ticket_priorities`).

## Statistik-Mail

`stats` (typischerweise wöchentlich per Cron) verschickt eine HTML-Mail
(mit Text-Fallback für Clients ohne HTML-Darstellung) mit SMS-Zahlen nach
Zammad-Gruppe für die letzten 24 Stunden/7 Tage/30 Tage (Eingehend/
Ausgehend farblich unterschieden), sowie — falls `[balance]` konfiguriert
ist — einem Guthaben-Abschnitt: aktueller Stand, durchschnittlicher
Verbrauch/Tag je Zeitraum, geschätzte Rest-Reichweite in Tagen (basierend
auf der 7-Tage-Verbrauchsrate; "unbestimmbar", falls in den letzten 7
Tagen kein positiver Verbrauch messbar war, z.B. nach einer Aufladung).

**Beispiel** (erfundene Werte, Text-Variante -- real zusätzlich als
HTML-Tabelle mit Farbcodierung Ein/Aus, s.
[SMS-Versand-Verhalten](#sms-versand-verhalten)):

```
SMS-Statistik nach Gruppe:

Gruppe                 24 Stunden     7 Tage       30 Tage
                         Ein   Aus    Ein   Aus    Ein   Aus
------------------------------------------------------------
Hotline                    4     6     21    28     88   101
SMS-Eingang-Unbekannt      1     0      5     0     17     0

Guthaben:

Aktuelles Guthaben: 25.77 Euro (Stand: 30.08.2026 10:00)

Ø Verbrauch/Tag:
  24 Stunden: 0.15 Euro/Tag
  7 Tage: 0.18 Euro/Tag
  30 Tage: 0.21 Euro/Tag

Geschaetzte Reichweite: 143 Tage (Basis: letzte 7 Tage)
```

Lesehilfe: "Hotline" zeigt z.B. für die letzten 7 Tage 21 eingehende und
28 ausgehende SMS: 88/101 in den letzten 30 Tagen deuten auf ein aktives
Ticketaufkommen hin, während "SMS-Eingang-Unbekannt" (Absender ohne
bestehenden Zammad-Kunden) nur eingehend und ohne Ausgang auftaucht --
dort landen typischerweise SMS, die noch manuell einem Kunden zugeordnet
werden müssen. Das Guthaben von 25,77 Euro reicht laut aktueller
7-Tage-Verbrauchsrate (0,18 Euro/Tag) noch für rund 143 Tage.

## Cron-Betrieb

```bash
sudo mkdir -p /var/log/smsammad
sudo chown <cron-user>:<cron-user> /var/log/smsammad
```

Dann in der **eigenen** Crontab des Cron-Users (`crontab -e`, **nicht**
`sudo crontab -e`) oder als `/etc/cron.d/`-Datei mit explizitem
User-Feld:

```
*/5 * * * * /pfad/zu/smsammad/cron_run.sh sms-to-ticket
*/5 * * * * /pfad/zu/smsammad/cron_run.sh ticket-to-sms
0 7 * * *   /pfad/zu/smsammad/cron_run.sh balance-check
0 8 * * 1   /pfad/zu/smsammad/cron_run.sh stats
```

`cron_run.sh` **niemals** manuell mit `sudo`/als root aufrufen (auch nicht
zum Testen) — sonst gehören Log-/Lock-Dateien danach root, und der normale
Cron-User scheitert anschließend mit "Permission denied" (live passiert:
ein manueller Testlauf mit `sudo` hat eine root-eigene Lock-Datei
hinterlassen, an der der reguläre Cron-Lauf danach scheiterte).

`cron_run.sh` verhindert überlappende Läufe (`flock`, Lock-Datei bewusst
unter `/var/log/smsammad/`, **nicht** `/tmp` — aus genau dem
oben beschriebenen root-Owner-Grund), loggt immer nach
`/var/log/smsammad/<task>.log` und ist bei Erfolg still (kein
Cron-Mail-Spam) — bei Fehlern (Exit-Code != 0) geht die Ausgabe zusätzlich
auf stderr, damit crons eigenes `MAILTO` (falls lokaler Mailversand
eingerichtet ist) als zweite, grobe Absicherung neben der App-eigenen
Fehlermail (`config.ini`: `[notification]`) greift — die App-Mail liefert
präzisere Details, kann aber z.B. bei einer kaputten `config.ini` selbst
nicht mehr greifen.

## Sicherheitshinweise

- `config.ini` **immer** `600`, niemals gruppen-/weltlesbar (aktiv
  geprüft beim Start).
- Bei einem Mehrbenutzer-Setup (z.B. dedizierter Cron-User): gemeinsam
  genutzte Projektverzeichnisse per Gruppenrechte + Setgid-Bit teilen,
  `config.ini` dabei **explizit** von der Gruppenfreigabe ausnehmen
  (`chmod 600` bleibt bestehen, auch wenn der Rest des Verzeichnisses
  `664`/`774` bekommt).
- Log-Inhalte: Rufnummern bleiben lesbar (notwendig fürs Debugging),
  SMS-/Ticket-**Inhalte** werden maskiert (s.
  [Content-Redaction](#content-redaction-im-log)).
- Bei einem Leak von Router- oder SMTP-Zugangsdaten (z.B. versehentlich in
  einem Log/einer Konsolenausgabe sichtbar geworden): zeitnah rotieren.

## Test

```bash
pytest
```

Tests laufen komplett gegen Fakes/eine echte temporäre SQLite-Datei (kein
Netzwerkzugriff nötig, `responses` mockt die Zammad-HTTP-Aufrufe). Echte
Verifikation gegen den realen Router/die reale Zammad-Instanz erfolgt
zusätzlich manuell vor jeder als "fertig" markierten Änderung (siehe
Commit-/Entwicklungshistorie) — Dry-Run zuerst, danach ein bewusster
einzelner echter Lauf.

## Lizenz

[BSD 3-Clause](LICENSE).
