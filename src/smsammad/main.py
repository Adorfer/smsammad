"""CLI-Einstieg fuer smsammad.

Subcommands:
- ticket-to-sms: Zammad-Ticket mit Tag 'sms-out' -> SMS ueber Teltonika-Router
  (Cronjob-Polling)
- sms-to-ticket: eingehende SMS am Teltonika-Router -> Zammad-Ticket
  (Cronjob-Polling)
- stats: On-Demand SMS-Statistik-Mail (in/out nach Gruppe, letzte 7/30 Tage),
  rein lokale Auswertung, kein Zammad-/Teltonika-Zugriff
- balance-check: taegliche Guthaben-Abfrage der Prepaid-SIM-Karte
  (Cronjob, 1x/Tag) -- per USSD (synchron) oder SMS (Antwort wird von
  sms-to-ticket verarbeitet), siehe [balance] method in der config.ini
- check-setup: prueft (und mit --fix repariert) die Zammad-seitige
  Installation (Trigger, Gruppenzugriff) -- nur aktiv, wenn [zammad]
  self_manage_setup=true in der config.ini gesetzt ist, siehe setup_check.py
"""

import argparse
import dataclasses
import sys
import traceback
from pathlib import Path

from . import access_guard, balance_check, setup_check, sms_to_ticket, stats_report, ticket_to_sms
from .config import Config, ConfigError, load_config
from .logging_setup import setup_logging
from .notify import send_mail
from .sms_budget import SmsBudget
from .teltonika import TeltonikaClient
from .zammad import ZammadClient


def _run_direction(name: str, config: Config, dry_run: bool, fix: bool = False) -> None:
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

    if name == "check-setup":
        setup_check.run(zammad, config, fix=fix, dry_run=dry_run)
        return

    if name == "ticket-to-sms":
        ticket_to_sms.run(zammad, teltonika, config, dry_run=dry_run)
    else:
        sms_to_ticket.run(teltonika, zammad, config, dry_run=dry_run)


def _add_global_flags(target: argparse.ArgumentParser, *, suppress_defaults: bool) -> None:
    """--config/--dry-run/--verbose sollen sowohl VOR als auch NACH dem
    Subcommand funktionieren (z.B. sowohl 'smsammad --dry-run
    sms-to-ticket' als auch 'smsammad sms-to-ticket --dry-run'). Dafuer
    werden sie auf dem obersten Parser UND auf jedem Subparser definiert
    (parents=[...] bei add_parser).

    Stolperfalle dabei: parst der Subparser seinen Teil und eine Flag
    wurde dort NICHT angegeben, setzt argparse fuer diese Flag sonst
    trotzdem ihren Default -- und ueberschreibt damit den Wert, den der
    oberste Parser ggf. schon aus dem Teil VOR dem Subcommand gesetzt
    hat. Deshalb bekommen die Subparser-Kopien `default=SUPPRESS`: sie
    setzen dann nur, was tatsaechlich NACH dem Subcommand stand, statt
    den Wert von davor zu ueberschreiben."""
    default_str = argparse.SUPPRESS if suppress_defaults else None
    default_bool = argparse.SUPPRESS if suppress_defaults else False
    target.add_argument("--config", type=str, default=default_str, help="Pfad zur config.ini")
    target.add_argument(
        "--dry-run", action="store_true", default=default_bool, help="keine Seiteneffekte, nur loggen"
    )
    target.add_argument(
        "--verbose", action="store_true", default=default_bool, help="Debug-Logging"
    )


def main() -> None:
    subcommand_flags = argparse.ArgumentParser(add_help=False)
    _add_global_flags(subcommand_flags, suppress_defaults=True)

    parser = argparse.ArgumentParser(prog="smsammad")
    _add_global_flags(parser, suppress_defaults=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ticket-to-sms", parents=[subcommand_flags])
    subparsers.add_parser("sms-to-ticket", parents=[subcommand_flags])
    subparsers.add_parser("stats", parents=[subcommand_flags])
    subparsers.add_parser("balance-check", parents=[subcommand_flags])
    check_setup_parser = subparsers.add_parser("check-setup", parents=[subcommand_flags])
    check_setup_parser.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help=(
            "gefundene Luecken tatsaechlich reparieren (Trigger anlegen, Gruppenzugriff "
            "gewaehren) statt nur zu berichten -- erfordert zusaetzlich "
            "[zammad] self_manage_setup=true in der config.ini"
        ),
    )

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
        _run_direction(args.command, config, args.dry_run, fix=getattr(args, "fix", False))
    except setup_check.SetupProblem as exc:
        # Kein Traceback: die Meldung ist bereits ein vollstaendiger,
        # lesbarer Diagnosebericht (siehe setup_check.py), kein
        # unerwarteter Absturz -- ein Traceback obendrauf waere nur
        # Rauschen in Log und Mail.
        logger.error("%s", exc)
        try:
            send_mail(
                config.notification,
                subject="SMSammad: check-setup hat Probleme gefunden",
                body=str(exc),
            )
        except Exception:
            logger.exception("Fehlerbenachrichtigung per Mail konnte nicht verschickt werden")
        sys.exit(1)
    except access_guard.AccessBlocked as exc:
        # access_guard.py hat die Benachrichtigung schon selbst uebernommen
        # (Mail nur beim Auslösen der Sperre, nicht bei jedem
        # uebersprungenen Lauf waehrend der Sperre) -- hier bewusst KEINE
        # weitere Mail, sonst Doppel-Mail bzw. Mail-Spam bei jedem
        # uebersprungenen Cron-Lauf.
        if exc.just_entered:
            logger.error("%s", exc)
            sys.exit(1)
        logger.info("%s", exc)
        sys.exit(0)
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
