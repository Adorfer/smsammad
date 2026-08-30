# Zammad-Connector-Experiment: nativer Teltonika-SMS-Kanal (Spike)

**Status: experimenteller Spike, nicht auf `main`, ungetestet gegen ein
echtes Zammad.** Lokal verifiziert (siehe unten), aber die eigentliche
Ende-zu-Ende-Prüfung (Rails-Autoloading, Admin-UI, echte Zustellung) steht
noch aus und kann nur auf dem echten Zammad-Docker-Host durchgeführt
werden.

## Warum das hier liegt

SMSammad koppelt Zammad und den Teltonika-Router bisher komplett am
nativen SMS-Mechanismus von Zammad vorbei (Anruf-Tab + Trigger, siehe
Haupt-README). Diese Datei hier ist der Versuch, stattdessen die native
SMS-"Sprechblase" im Ticket-Editor nutzbar zu machen -- bessere
Agenten-UX, kein Anruf-Tab-Workaround.

Zammad entdeckt SMS-Connectors rein über Dateisystem-Scan
(`app/controllers/channels_sms_controller.rb#channels_config`,
`Rails.root.glob('app/models/channel/driver/sms/*.rb')`) und lädt sie per
Zeitwerk-Autoload. Eine eigene Datei am richtigen Pfad reicht also aus,
um "Teltonika RUT240" als echten, nativen Provider im Admin-Dropdown
anzubieten -- kein Monkeypatch nötig (anders als beim bestehenden
`ai_max_tokens.rb`-Patch, der eine bestehende Zammad-Konstante
überschreibt).

**Bewusst NUR der Versand** (native Sprechblase im Editor). Der Empfang
bleibt unverändert über den bestehenden `sms-to-ticket`-Polling-Pfad.

## Bewusste Einschränkungen dieses Spikes

Dieser Connector ist **kleiner** als der bestehende Python-Pfad
(`ticket_to_sms.py`) -- absichtlich, um zuerst nur zu prüfen, ob die
native Sprechblase überhaupt zuverlässig funktioniert:

- **Kein Mehrteil-Splitting.** Längere Texte werden vom Router NICHT
  automatisch aufgeteilt (siehe Haupt-README, Abschnitt
  "Teltonika-API-Dokumentation und ihre Lücken") -- können also
  abgeschnitten/verschluckt werden.
- **Kein Sende-Budget/Rate-Limit.** Die SQLite-Budget-Logik aus
  `sms_budget.py` existiert hier nicht -- ein Agent könnte beliebig viele
  SMS über diesen Kanal verschicken, ohne Stunden-/24h-Deckel.
- **Kein Überlauf-Handling** (`sms-overflow`-Tag, Prioritäts-Eskalation).
- Das Passwort-Feld im Zammad-Admin-Formular ist **nicht maskiert**
  (`tag: 'input', type: 'text'`, wie auch beim Twilio-Token-Feld in
  Zammads eigenem Code) -- im Klartext auf dem Bildschirm sichtbar beim
  Eintragen.

Falls sich der Spike bewährt, wäre der nächste Schritt, diese Lücken zu
schließen (z.B. Splitting/Budget aus der Python-Seite nach Ruby
portieren) -- hier absichtlich noch nicht gemacht.

## Dateien

- `teltonika.rb` -- der eigentliche Connector.
- `test_teltonika.rb` -- Standalone-Ruby-Test (kein echtes Zammad nötig),
  verifiziert `definition()` und die HTTP-Bau-Logik von `deliver()` gegen
  einen echten lokalen Test-Server (inkl. URL-Encoding von
  Sonderzeichen im Passwort, und dass Passwort/Host bei Fehlern nicht im
  Exception-Text landen). Aufruf: `ruby zammad-connector/test_teltonika.rb`
- `docker-compose.override.snippet.yml` -- die zusätzliche Mount-Zeile
  für die bestehende `docker-compose.override.yml` auf dem Zammad-Host.

## Deployment auf dem echten Zammad-Docker-Host

1. `teltonika.rb` nach `/opt/zammad-docker-compose/zammad-overrides/`
   kopieren.
2. In der bestehenden `docker-compose.override.yml` die zusätzliche
   Mount-Zeile bei allen drei Services ergänzen (siehe
   `docker-compose.override.snippet.yml`).
3. `docker compose up -d` (Container neu erstellen, damit der neue Mount
   greift).
4. Verifizieren, dass die Klasse lädt:
   ```bash
   docker compose exec zammad-railsserver /docker-entrypoint.sh \
     bundle exec rails r 'p Channel::Driver::Sms::Teltonika.definition'
   ```
5. Admin → Channels → SMS → Accounts → "+" → Provider-Dropdown sollte
   jetzt "Teltonika RUT240" zeigen. Echten Router-Host/User/Passwort +
   Ziel-Gruppe eintragen, speichern, **explizit "Enable" klicken**
   (Speichern allein reicht laut Quellcode nicht aus, `active: true`
   wird erst durch den separaten Enable-Klick gesetzt).
6. In einem Ticket dieser Gruppe prüfen, ob die SMS-Sprechblase im Editor
   erscheint, und eine kurze Test-SMS an eine echte Nummer verschicken --
   echte Zustellung prüfen.
7. Bei Fehlern: `docker compose logs zammad-railsserver` -- `send_sms`
   wirft bei Fehlern eine einfache Fehlermeldung ohne URL/Passwort,
   sollte also gefahrlos ins Log gehen.

## Nach jedem Zammad-Update

```bash
docker compose exec zammad-railsserver /docker-entrypoint.sh \
  bundle exec rails r 'p Channel::Driver::Sms::Teltonika.definition'
```
prüft, dass der Connector nach dem Update noch lädt (die interne
`Channel::Driver::Sms::Base`-Schnittstelle ist nicht offiziell
versioniert/zugesichert).
