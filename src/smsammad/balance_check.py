"""Guthaben-Abfrage der Prepaid-SIM-Karte -- zwei Wege, ueber [balance]
method in der config.ini waehlbar (Default "ussd"):

- "ussd": synchron per RutOS-REST-API (/api/...), Ergebnis liegt sofort
  vor, i.d.R. kostenlos. Ticket-Handling passiert direkt in diesem Lauf
  (siehe balance_ticket.apply_balance_result).
- "sms": Abfrage-SMS an eine Kurzwahl, Antwort trifft asynchron ein und
  wird vom naechsten sms_to_ticket-Lauf ausgewertet
  (sms_to_ticket._process_balance_reply).

Schlaegt die konfigurierte Default-Methode fehl (Zugriff verweigert oder
Antwort nicht parsebar -- beides real zu erwarten, siehe Kommentare unten),
wird EINMALIG automatisch auf die jeweils andere Methode umgeschaltet,
sofern deren Zugangsdaten ebenfalls in der config.ini hinterlegt sind.
Ohne konfigurierte Fallback-Methode wird der Fehler normal durchgereicht
(ueblicher Fehlermail-Pfad in main.py).
"""

import logging
import re

from . import balance_ticket
from .config import Config
from .sms_budget import SmsBudget
from .teltonika import TeltonikaClient, TeltonikaError
from .teltonika_api import TeltonikaApiClient, TeltonikaApiError
from .zammad import ZammadClient

logger = logging.getLogger("smsammad")

_FALLBACK_TRIGGERS = (TeltonikaError, TeltonikaApiError, ValueError)


def _parse_ussd_balance(text: str, pattern: str) -> float | None:
    # Provider-spezifisch (hier: Vodafone Callya) -- die genaue Formulierung
    # der USSD-Menue-Antwort kann sich jederzeit aendern (Werbetexte etc.).
    # Pattern kommt aus config.ini (balance.ussd_balance_regex), damit das
    # ohne Code-Aenderung nachziehbar ist.
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _run_ussd(
    teltonika: TeltonikaClient,
    zammad: ZammadClient,
    config: Config,
    dry_run: bool,
    budget: SmsBudget,
) -> None:
    balance_config = config.balance
    if dry_run:
        logger.info(
            "[dry-run] wuerde USSD-Code %r an Modem %r senden",
            balance_config.ussd_code,
            balance_config.modem_id,
        )
        return

    api = TeltonikaApiClient(config.teltonika.host, balance_config, verify_tls=config.teltonika.verify_tls)
    response_text = api.send_ussd(balance_config.ussd_code)  # -> TeltonikaApiError bei Zugriffsfehlern

    amount_eur = _parse_ussd_balance(response_text, balance_config.ussd_balance_regex)
    if amount_eur is None:
        raise ValueError(f"USSD-Antwort: Betrag nicht erkennbar: {response_text!r}")

    budget.mark_balance_queried()
    balance_ticket.apply_balance_result(
        amount_eur, balance_ticket.USSD_PSEUDO_CUSTOMER_ID, response_text, zammad, config, dry_run, budget
    )


def _run_sms(
    teltonika: TeltonikaClient,
    zammad: ZammadClient,
    config: Config,
    dry_run: bool,
    budget: SmsBudget,
) -> None:
    balance_config = config.balance
    if dry_run:
        logger.info(
            "[dry-run] wuerde Guthaben-Abfrage-SMS an %r senden: %r",
            balance_config.query_number,
            balance_config.query_text,
        )
        return

    teltonika.send(balance_config.query_number, balance_config.query_text)  # -> TeltonikaError bei Fehlern
    budget.mark_balance_queried()
    logger.info("balance-check: Guthaben-Abfrage-SMS an %r gesendet", balance_config.query_number)


def run(
    teltonika: TeltonikaClient,
    zammad: ZammadClient,
    config: Config,
    dry_run: bool = False,
    budget: SmsBudget | None = None,
) -> None:
    if config.balance is None:
        logger.warning("balance-check: [balance] nicht konfiguriert, ueberspringe")
        return

    balance_config = config.balance
    budget = budget or SmsBudget(
        config.ticket_to_sms.stats_db_file,
        config.ticket_to_sms.max_sms_per_hour,
        config.ticket_to_sms.max_sms_per_24h,
    )

    if not budget.should_query_balance(balance_config.query_interval_hours):
        logger.info(
            "balance-check: letzte Abfrage noch nicht %d Stunden her, ueberspringe",
            balance_config.query_interval_hours,
        )
        return

    use_sms = balance_config.method == "sms"
    primary_name, primary_fn = ("sms", _run_sms) if use_sms else ("ussd", _run_ussd)
    fallback_name, fallback_fn = ("ussd", _run_ussd) if use_sms else ("sms", _run_sms)
    fallback_configured = (
        bool(balance_config.query_number and balance_config.reply_sender)
        if fallback_name == "sms"
        else bool(balance_config.api_username and balance_config.api_password)
    )

    try:
        primary_fn(teltonika, zammad, config, dry_run, budget)
    except _FALLBACK_TRIGGERS as exc:
        if not fallback_configured:
            raise
        logger.warning(
            "balance-check: Methode '%s' fehlgeschlagen (%s) -- wechsle einmalig auf '%s'",
            primary_name,
            exc,
            fallback_name,
        )
        fallback_fn(teltonika, zammad, config, dry_run, budget)
