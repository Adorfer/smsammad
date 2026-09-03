"""Orchestrierung SMS -> Zammad-Ticket."""

import logging
import re

from . import access_guard, balance_ticket
from .config import Config
from .htmltext import html_to_text
from .logging_setup import redact_content
from .phone import PhoneNumberError, to_e164
from .sms_budget import SmsBudget
from .teltonika import TeltonikaAuthError, TeltonikaClient
from .zammad import ZammadClient, ZammadError

logger = logging.getLogger("smsammad")

# phonenumbers haelt manche kurzen Ziffernfolgen faelschlich fuer gueltige
# Rufnummern (in DE existieren legitime kurze Dienstenummern, z.B. "224466"
# parst als "gueltig"), wodurch Netzbetreiber-Kurzwahlen faelschlich als
# "echte Nummer" durchgehen wuerden. Deshalb der Direktversuch nur, wenn der
# rohe Absender bereits wie eine vollstaendige Nummer aussieht (internationales
# Format "+49..." oder nationales Format mit Trunk-Praefix "01..." fuer
# deutsche Mobilfunknummern); alles andere geht ueber short_number_prefix-
# Rekonstruktion bzw. landet als Kurzwahl.
def _looks_like_complete_number(sender: str) -> bool:
    return sender.startswith("+49") or sender.startswith("01")


def _subject_excerpt(text: str, limit: int = 50) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 4] + "[..]"


def _resolve_new_ticket_group(
    customer_id: int | None, was_created: bool, zammad: ZammadClient, config: Config
) -> str:
    """Gruppe fuer ein neu anzulegendes Ticket (kein offenes Ticket
    vorhanden). Neuer Kunde -> new_customer_group. Bekannter Kunde -> per
    Default die feste `group` (Alt-Verhalten), oder -- falls
    `group_from_last_ticket` gesetzt ist -- die Gruppe seines zuletzt
    kontaktierten Tickets (offen oder geschlossen), fuer
    kundenzentrisch arbeitende Teams. Fallback auf `group`, falls der
    Kunde noch gar kein Ticket hatte, dessen Gruppe nicht ermittelbar ist,
    ODER der SMSammad-API-User keinen Zugriff auf diese Gruppe hat (HTTP
    403 -- kann z.B. passieren, wenn ein Agent ein Ticket manuell in eine
    Gruppe verschoben hat, fuer die der API-User nie freigeschaltet
    wurde)."""
    if was_created:
        return config.zammad.new_customer_group
    if config.zammad.group_from_last_ticket and customer_id is not None:
        last_ticket = zammad.find_last_ticket_for_customer(customer_id)
        if last_ticket is not None:
            try:
                last_ticket_full = zammad.get_ticket(last_ticket.id)
                group_id = last_ticket_full.get("group_id")
                if group_id:
                    return zammad.get_group_name(group_id)
            except ZammadError:
                logger.warning(
                    "Gruppe des letzten Tickets von Kunde %s nicht zugreifbar (fehlende "
                    "Berechtigung?), verwende stattdessen die Default-Gruppe '%s'",
                    customer_id,
                    config.zammad.group,
                )
    return config.zammad.group


def run(
    teltonika: TeltonikaClient,
    zammad: ZammadClient,
    config: Config,
    dry_run: bool = False,
    budget: SmsBudget | None = None,
) -> None:
    budget = budget or SmsBudget(
        config.ticket_to_sms.stats_db_file,
        config.ticket_to_sms.max_sms_per_hour,
        config.ticket_to_sms.max_sms_per_24h,
    )

    messages = access_guard.guarded_call(
        budget,
        "cgi",
        (TeltonikaAuthError,),
        config.notification,
        "SMS abrufen",
        teltonika.list_messages,
    )
    logger.info("sms_to_ticket: %d SMS auf dem Router", len(messages))

    failures = 0
    for message in messages:
        try:
            _process_one(message, teltonika, zammad, config, dry_run, budget)
        except Exception:
            failures += 1
            logger.exception("sms_to_ticket: Verarbeitung von SMS #%s fehlgeschlagen", message.index)

    if failures:
        raise RuntimeError(f"sms_to_ticket: {failures} von {len(messages)} SMS fehlgeschlagen")


def _resolve_sender_id(
    sender: str, default_region: str, short_number_prefix: str, unresolved_sender_prefix: str
) -> tuple[str, bool]:
    """Rufnummer normalisieren, mit Rekonstruktions-Fallback fuer zu kurze
    Absender (z.B. Kurzrufnummern ohne Vorwahl): erst direkt versuchen, dann
    mit konfiguriertem short_number_prefix davor. Bleibt das erfolglos (z.B.
    echte Kurzwahlen wie "22543" oder alphanumerische Absender-IDs wie
    "CALLYA"), wird der rohe Absender-String hinter unresolved_sender_prefix
    gesetzt (falls konfiguriert), damit der Rueckweg (ticket_to_sms) das
    erkennen/rueckgaengig machen kann. Liefert (sender_id,
    war_eine_gueltige_rufnummer).
    """
    if _looks_like_complete_number(sender):
        try:
            return to_e164(sender, default_region), True
        except PhoneNumberError:
            pass

    if short_number_prefix:
        try:
            return to_e164(f"{short_number_prefix}{sender}", default_region), True
        except PhoneNumberError:
            pass

    if unresolved_sender_prefix:
        return f"{unresolved_sender_prefix}{sender}", False
    return sender, False


def _process_one(
    message,
    teltonika: TeltonikaClient,
    zammad: ZammadClient,
    config: Config,
    dry_run: bool,
    budget: SmsBudget,
) -> None:
    sender_id, is_valid_number = _resolve_sender_id(
        message.sender,
        config.teltonika.default_country_code,
        config.teltonika.short_number_prefix,
        config.teltonika.unresolved_sender_prefix,
    )
    if not is_valid_number:
        logger.warning(
            "SMS #%s: Absender '%s' ist keine gueltige Rufnummer (auch nicht mit Praefix), "
            "verwende %s",
            message.index,
            message.sender,
            f"'{sender_id}'" if config.teltonika.unresolved_sender_prefix else "Rohwert",
        )

    message_text = html_to_text(message.text) or message.text

    if (
        config.balance is not None
        and config.balance.reply_sender
        and message.sender == config.balance.reply_sender
    ):
        # Absichtlich unabhaengig von balance.method: falls die Default-
        # Methode "ussd" ist, aber zusaetzlich SMS-Fallback-Zugangsdaten
        # konfiguriert sind (siehe balance_check.py), kann trotzdem eine
        # Abfrage-SMS unterwegs gewesen sein -- deren Antwort muss hier
        # genauso erkannt werden.
        #
        # ABER: dieselbe Kurzwahl (z.B. Vodafones "80808") verschickt auch
        # voellig andere automatische Nachrichten (Vertragsaenderungen,
        # Abbuchungshinweise usw.), keine Guthaben-Antworten. Live
        # reproduziert: eine solche SMS liess den Betrags-Regex ins Leere
        # laufen, was bisher die GESAMTE Verarbeitung crashen liess UND
        # (weil vor teltonika.delete()) die SMS dauerhaft auf dem Router
        # stehen liess -- jeder folgende Lauf crashte erneut. Deshalb nur
        # dann als Guthaben-Antwort behandeln, wenn tatsaechlich ein Betrag
        # erkennbar ist; sonst normal wie jede andere SMS weiterverarbeiten
        # (faellt durch in den Code unten).
        amount_eur = _parse_balance(message_text, config.balance.sms_balance_regex)
        if amount_eur is not None:
            _process_balance_reply(
                message, sender_id, amount_eur, message_text, teltonika, zammad, config, dry_run, budget
            )
            return
        logger.info(
            "SMS #%s von '%s' (Guthaben-Absender) enthaelt keinen erkennbaren Betrag, wird "
            "als normale SMS weiterverarbeitet: %r",
            message.index,
            message.sender,
            redact_content(message_text),
        )

    body = message_text
    if message.timestamp:
        # Vom Router gemeldeter Zeitstempel (vermutlich Empfangszeit am
        # Modem, ggf. auch Absendezeit laut SMSC -- die cgi-bin-API
        # spezifiziert das nicht genauer).
        body = f"{body}\n---\nSMS-Empfang: {message.timestamp}"

    if dry_run:
        # Nur lesende Suche -- find_or_create_customer_by_phone wuerde bei
        # unbekanntem Absender einen echten Zammad-Kunden anlegen, das waere
        # ein Seiteneffekt im Dry-Run.
        customer_id = zammad.find_customer_by_phone(sender_id, config.teltonika.default_country_code)
        was_created = customer_id is None
        logger.info(
            "[dry-run] SMS #%s von %s -> Kunde %s",
            message.index,
            sender_id,
            "wuerde neu angelegt" if was_created else customer_id,
        )
        open_ticket = (
            zammad.find_open_ticket_for_customer(customer_id) if customer_id is not None else None
        )
        if open_ticket:
            logger.info(
                "[dry-run] wuerde Artikel an Ticket %s haengen: %r",
                open_ticket.number,
                redact_content(body),
            )
        else:
            group = _resolve_new_ticket_group(customer_id, was_created, zammad, config)
            logger.info(
                "[dry-run] wuerde neues Ticket in Gruppe %r anlegen: %r", group, redact_content(body)
            )
        logger.info("[dry-run] wuerde SMS #%s auf dem Router loeschen", message.index)
        return

    customer_id, was_created = zammad.find_or_create_customer_by_phone(
        sender_id, config.teltonika.default_country_code
    )
    logger.info(
        "SMS #%s von %s -> Kunde %s (%s)",
        message.index,
        sender_id,
        customer_id,
        "neu angelegt" if was_created else "bekannt",
    )

    open_ticket = zammad.find_open_ticket_for_customer(customer_id)

    if open_ticket:
        zammad.add_article(open_ticket.id, body)
        open_ticket_full = zammad.get_ticket(open_ticket.id)
        group_name = (
            zammad.get_group_name(open_ticket_full["group_id"])
            if open_ticket_full.get("group_id")
            else None
        )
        budget.record_received(group=group_name, ticket_number=open_ticket.number)
    else:
        group = _resolve_new_ticket_group(customer_id, was_created, zammad, config)
        # Betreff mit Textauszug statt festem Platzhalter: neuer Kunde ODER
        # Kunde hat nur geschlossene Tickets (kein offenes/neues/wartendes)
        # -- in beiden Faellen wird hier neu angelegt.
        subject = f"Neues SMS-Ticket: {_subject_excerpt(message_text)}"
        zammad.create_ticket(customer_id, group, subject, body)
        budget.record_received(group=group)

    teltonika.delete(message.index)


def _parse_balance(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _process_balance_reply(
    message,
    sender_id: str,
    amount_eur: float,
    message_text: str,
    teltonika: TeltonikaClient,
    zammad: ZammadClient,
    config: Config,
    dry_run: bool,
    budget: SmsBudget,
) -> None:
    """`amount_eur`/`message_text` werden bereits von _process_one geliefert
    (dort wird der Betrag VOR der Entscheidung "ist das ueberhaupt eine
    Guthaben-Antwort" geprueft, siehe dort)."""
    assert config.balance is not None  # von _process_one bereits geprueft

    body = message_text
    if message.timestamp:
        body = f"{body}\n---\nSMS-Empfang: {message.timestamp}"

    balance_ticket.apply_balance_result(amount_eur, sender_id, body, zammad, config, dry_run, budget)

    if dry_run:
        logger.info("[dry-run] wuerde SMS #%s auf dem Router loeschen", message.index)
        return
    teltonika.delete(message.index)
