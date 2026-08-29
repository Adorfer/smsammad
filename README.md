# zammad2teltonikasms

Arbeitstitel. Bindeglied zwischen Zammad und einem Teltonika-Router (SMS-Gateway),
bidirektional:

- **Zammad -> SMS**: Bei Zammad-Ereignissen (z.B. Trigger/Webhook) eine SMS ueber
  die Teltonika-Router-API versenden.
- **SMS -> Zammad**: Eingehende SMS am Teltonika-Router abholen und daraus
  Zammad-Tickets/Artikel erzeugen.

## Stand

Reines Projektgeruest, Logik/Architektur noch nicht festgelegt.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Test

```bash
pytest
```
