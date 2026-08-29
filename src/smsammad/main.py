"""CLI-Einstieg fuer smsammad.

Vier Subcommands:
- ticket-to-sms: Zammad-Ticket mit Tag 'sms-out' -> SMS ueber Teltonika-Router
  (Cronjob-Polling)
- sms-to-ticket: eingehende SMS am Teltonika-Router -> Zammad-Ticket
  (Cronjob-Polling)
- stats: On-Demand SMS-Statistik-Mail (in/out nach Gruppe, letzte 7/30 Tage),
  rein lokale Auswertung, kein Zammad-/Teltonika-Zugriff
- balance-check: taegliche Guthaben-Abfrage der Prepaid-SIM-Karte
  (Cronjob, 1x/Tag) -- per USSD (synchron) oder SMS (Antwort wird von
  sms-to-ticket verarbeitet), siehe [balance] method in der config.ini
"""

import argparse
import dataclasses
import sys
import traceback
from pathlib import Path

from . import balance_check, sms_to_ticket, stats_report, ticket_to_sms
from .config import Config, ConfigError, load_config
from .logging_setup import setup_logging
from .notify import send_mail
from .sms_budget import SmsBudget
from .teltonika import TeltonikaClient
from .zammad import ZammadClient


def _run_direction(name: str, config: Config, dry_run: bool) -> None:
    if name == "stats":
        # Rein lokale Auswertung + Mail, keine Zammad-/Teltonika-Zugriffe noetig.
        budget = SmsBudget(
            config.ticket_to_sms.stats_db_file,
            config.ticket_to_sms.max_sms_per_hour,
            config.ticket_to_sms.max_sms_per_24h,
        )
        stats_report.run(budget, config, dry_run=dry_run)
        return

    teltonika = TeltonikaClient(config.teltonika)
    zammad = ZammadClient(config.zammad)

    if name == "balance-check":
        # Zammad-Client wird fuer die USSD-Methode gebraucht (synchrones
        # Ticket-Handling im selben Lauf, siehe balance_ticket.py).
        balance_check.run(teltonika, zammad, config, dry_run=dry_run)
        return

    if name == "ticket-to-sms":
        ticket_to_sms.run(zammad, teltonika, config, dry_run=dry_run)
    else:
        sms_to_ticket.run(teltonika, zammad, config, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(prog="smsammad")
    parser.add_argument("--config", type=str, default=None, help="Pfad zur config.ini")
    parser.add_argument("--dry-run", action="store_true", help="keine Seiteneffekte, nur loggen")
    parser.add_argument("--verbose", action="store_true", help="Debug-Logging")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ticket-to-sms")
    subparsers.add_parser("sms-to-ticket")
    subparsers.add_parser("stats")
    subparsers.add_parser("balance-check")

    args = parser.parse_args()
    logger = setup_logging(args.verbose)

    try:
        config = load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        logger.error("Config-Fehler: %s", exc)
        sys.exit(1)

    if args.dry_run and config.notification is not None and config.notification.enabled:
        logger.info("--dry-run: Fehlerbenachrichtigung per Mail ist fuer diesen Lauf deaktiviert")
        config = dataclasses.replace(
            config, notification=dataclasses.replace(config.notification, enabled=False)
        )

    try:
        _run_direction(args.command, config, args.dry_run)
    except Exception as exc:
        logger.exception("%s fehlgeschlagen", args.command)
        try:
            send_mail(
                config.notification,
                subject=f"SMSammad: {args.command} fehlgeschlagen",
                body=f"{exc}\n\n{traceback.format_exc()}",
            )
        except Exception:
            logger.exception("Fehlerbenachrichtigung per Mail konnte nicht verschickt werden")
        sys.exit(1)


if __name__ == "__main__":
    main()
