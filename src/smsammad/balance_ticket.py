"""Gemeinsame Zammad-Ticket-Eskalationslogik fuer die Guthaben-Ueberwachung.

Wird von BEIDEN Abfragewegen genutzt: dem synchronen USSD-Weg
(balance_check.py, sendet und wertet die Antwort direkt in einem Lauf aus)
und dem asynchronen SMS-Weg (sms_to_ticket.py, wertet eine spaeter
eintreffende Antwort-SMS aus). Die Eskalationsstufen (ok/warn/alarm) und
das Ticket-Verhalten sollen unabhaengig vom Abfrageweg identisch sein,
daher hier gebuendelt statt dupliziert.
"""

import logging

from .config import BalanceConfig, Config
from .sms_budget import SmsBudget
from .zammad import ZammadClient

logger = logging.getLogger("smsammad")

TICKET_SUBJECT_BALANCE = "SMS-Guthaben"
TICKET_SUBJECT_WARN = "SMS Guthaben sollte aufgeladen werden"
TICKET_SUBJECT_ALARM = "SMS-Guthaben KRITISCH niedrig - SMS-Versand gefaehrdet"

# Pseudo-Kunde fuer per USSD ermittelte Werte -- es gibt dabei keinen
# SMS-Absender, daher ein fester Identifikator. Bewusst ein eigener Wert
# (nicht "Kurzwahl:<reply_sender>" wie beim SMS-Weg), damit ein spaeterer
# Wechsel der Methode nicht ploetzlich das alte Ticket des jeweils anderen
# Wegs wiederverwendet/uebernimmt.
USSD_PSEUDO_CUSTOMER_ID = "USSD-Guthaben"


def determine_tier(amount_eur: float, balance_config: BalanceConfig) -> str:
    if amount_eur >= balance_config.warn_threshold_eur:
        return "ok"
    if amount_eur >= balance_config.alarm_threshold_eur:
        return "warn"
    return "alarm"


def apply_balance_result(
    amount_eur: float,
    pseudo_customer_id: str,
    body: str,
    zammad: ZammadClient,
    config: Config,
    dry_run: bool,
    budget: SmsBudget,
) -> None:
    """Legt/aktualisiert das Guthaben-Ticket und speichert den Wert in der
    Statistik-DB. `body` ist der bereits aufbereitete oeffentliche
    Artikeltext (SMS-Text bzw. rohe USSD-Antwort)."""
    balance_config = config.balance
    assert balance_config is not None
    tier = determine_tier(amount_eur, balance_config)

    if tier == "ok":
        subject = TICKET_SUBJECT_BALANCE
        verdict = "noch ausreichend"
    elif tier == "warn":
        subject = TICKET_SUBJECT_WARN
        verdict = "WARNUNG - sollte bald aufgeladen werden"
    else:
        subject = TICKET_SUBJECT_ALARM
        verdict = "ALARM - kritisch niedrig, SMS-Versand gefaehrdet"

    note = (
        f"Guthaben erkannt: {amount_eur:.2f} Euro "
        f"(Warn: {balance_config.warn_threshold_eur:.2f} Euro, "
        f"Alarm: {balance_config.alarm_threshold_eur:.2f} Euro).\nStatus: {verdict}."
    )

    if dry_run:
        customer_id = zammad.find_customer_by_phone(
            pseudo_customer_id, config.teltonika.default_country_code
        )
        open_ticket = (
            zammad.find_open_ticket_for_customer(customer_id) if customer_id is not None else None
        )
        action = {
            "ok": f"schliessen (state_id={balance_config.closed_state_id})",
            "warn": "offen lassen, Prioritaet 2",
            "alarm": "offen lassen, Prioritaet 3",
        }[tier]
        logger.info(
            "[dry-run] Guthaben-Antwort %.2f Euro -> Stufe '%s', Ticket %s: Betreff %r, %s. "
            "Notiz: %r",
            amount_eur,
            tier,
            open_ticket.number if open_ticket else "(neu)",
            subject,
            action,
            note,
        )
        return

    customer_id, was_created = zammad.find_or_create_customer_by_phone(
        pseudo_customer_id, config.teltonika.default_country_code
    )
    open_ticket = zammad.find_open_ticket_for_customer(customer_id)

    if open_ticket:
        ticket_id = open_ticket.id
        zammad.add_article(ticket_id, body)
    else:
        group = config.zammad.new_customer_group if was_created else config.zammad.group
        ticket_id = zammad.create_ticket(customer_id, group, TICKET_SUBJECT_BALANCE, body)

    zammad.add_article(ticket_id, note, internal=True, article_type="note", sender="Agent")
    zammad.set_subject(ticket_id, subject)
    if tier == "ok":
        zammad.set_state(ticket_id, balance_config.closed_state_id)
    elif tier == "warn":
        zammad.set_priority(ticket_id, 2)
    else:
        zammad.set_priority(ticket_id, 3)

    # Bewusst KEIN budget.record_received: Guthaben-Abfrage ist System-/
    # Gateway-Traffic, kein Kundenkontakt -- soll die "Eingehend"-Zahlen der
    # normalen Statistik nicht verfaelschen.
    budget.record_balance(amount_eur)

    logger.info("Guthaben-Antwort %.2f Euro -> Stufe '%s' (Ticket %s)", amount_eur, tier, ticket_id)
