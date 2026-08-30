"""Orchestrierung Zammad-Ticket -> SMS."""

import logging
from datetime import datetime, timedelta, timezone

from .config import Config, TeltonikaConfig
from .htmltext import html_to_text
from .logging_setup import redact_content
from .notify import send_mail
from .phone import is_mobile_number, to_teltonika_format
from .sms_budget import SmsBudget
from .sms_split import split_for_sms
from .teltonika import TeltonikaClient, TeltonikaError
from .zammad import ZammadClient

logger = logging.getLogger("smsammad")

TAG_OUT = "sms-out"
TAG_SENT = "sms-sent"
TAG_OVERFLOW = "sms-overflow"
# Genereller Sammel-Tag fuer alle Faelle, in denen ein Versand gar nicht
# erst versucht/erfolgreich abgeschlossen werden konnte (keine
# Mobilfunknummer, Text zu lang, Router-/Guthaben-Fehler beim Versand) --
# zusaetzlich zu den jeweils spezifischeren Tags, damit Agenten mit EINEM
# Tag/EINER Zammad-Sicht alle "muss ich mich kuemmern"-Tickets sehen.
TAG_CANNOT_SEND = "sms-cannotsend"
# Markiert ein Ticket, fuer das schon EINMALIG ein Budget-Wartehinweis
# hinterlegt wurde -- verhindert, dass bei jedem Cronlauf (solange das
# Budget weiter erschoepft ist) erneut eine Notiz dazukommt. Wird beim
# naechsten erfolgreichen Versand wieder entfernt.
TAG_BUDGET_WAIT = "sms-budget-warten"


def run(
    zammad: ZammadClient,
    teltonika: TeltonikaClient,
    config: Config,
    dry_run: bool = False,
    budget: SmsBudget | None = None,
) -> None:
    ticket_ids = zammad.search_tickets_by_tag(TAG_OUT)
    logger.info("ticket_to_sms: %d Ticket(s) mit Tag '%s'", len(ticket_ids), TAG_OUT)

    budget = budget or SmsBudget(
        config.ticket_to_sms.stats_db_file,
        config.ticket_to_sms.max_sms_per_hour,
        config.ticket_to_sms.max_sms_per_24h,
    )

    failures = 0
    budget_blocked: list[str] = []
    for ticket_id in ticket_ids:
        try:
            _process_one(ticket_id, zammad, teltonika, config, dry_run, budget, budget_blocked)
        except Exception:
            failures += 1
            logger.exception("ticket_to_sms: Verarbeitung von Ticket %s fehlgeschlagen", ticket_id)

    if budget_blocked and not dry_run:
        _notify_budget_exceeded(budget_blocked, budget, config)

    if failures:
        raise RuntimeError(f"ticket_to_sms: {failures} von {len(ticket_ids)} Tickets fehlgeschlagen")


def _resolve_destination_number(raw_number: str, teltonika_config: TeltonikaConfig) -> str:
    """Kehrseite von sms_to_ticket._resolve_sender_id: erkennt einen per
    unresolved_sender_prefix markierten Absender (Kurzwahl wie "22543" oder
    alphanumerische Absender-ID wie "CALLYA"), entfernt den Praefix wieder
    und liefert den rohen Wert unformatiert als Sendeziel -- ermoeglicht
    auch Antworten an Kurzwahlen/alphanumerische Absender. Sonst normale
    Rufnummer-Formatierung fuer die Teltonika-API.
    """
    prefix = teltonika_config.unresolved_sender_prefix
    if prefix and raw_number.startswith(prefix):
        raw_target = raw_number[len(prefix):]
        logger.info(
            "Unaufgeloester Absender erkannt (%s), sende roh an '%s'",
            raw_number,
            raw_target,
        )
        return raw_target
    return to_teltonika_format(raw_number, teltonika_config.default_country_code)


def _resolve_send_number(customer: dict, config: Config) -> str | None:
    """Ermittelt die SMS-Zielnummer eines Kunden: primaer das konfigurierte
    Mobilfunk-Feld (Default 'mobile'), sonst Fallback auf das normale
    Telefonnummer-Feld (Default 'phone') -- aber nur, wenn phonenumbers den
    dort hinterlegten Wert als Mobilfunknummer erkennt (eine Festnetz-
    nummer kann keine SMS empfangen). None, wenn keines von beidem eine
    nutzbare Mobilfunknummer liefert.
    """
    raw_mobile = customer.get(config.zammad.phone_field)
    if raw_mobile:
        return _resolve_destination_number(raw_mobile, config.teltonika)

    raw_fallback = customer.get(config.zammad.phone_field_fallback)
    default_region = config.teltonika.default_country_code
    if raw_fallback and is_mobile_number(raw_fallback, default_region):
        logger.info(
            "Kunde hat keine Nummer im Feld '%s', nutze Mobilfunknummer aus "
            "Fallback-Feld '%s'",
            config.zammad.phone_field,
            config.zammad.phone_field_fallback,
        )
        return _resolve_destination_number(raw_fallback, config.teltonika)
    return None


def _mark_cannot_send(
    ticket_id: int,
    ticket_number: str,
    current_state_id: int | None,
    current_title: str | None,
    note: str,
    zammad: ZammadClient,
    config: Config,
    extra_tags: tuple[str, ...] = (),
) -> None:
    """Gemeinsame Behandlung fuer alle 'Versand nicht moeglich'-Faelle:
    Tag(s) setzen, Vermerk hinterlegen, Prioritaet hochsetzen und -- falls
    das Ticket gerade NICHT offen ist (z.B. geschlossen oder in einem
    Warten-auf-Rueckmeldung-Zustand) -- wieder auf 'offen' setzen, damit
    ein Versandproblem nicht unbemerkt in einem inaktiven Ticket
    verschwindet.
    """
    zammad.remove_tag(ticket_id, TAG_OUT)
    for tag in extra_tags:
        zammad.add_tag(ticket_id, tag)
    zammad.add_tag(ticket_id, TAG_CANNOT_SEND)
    zammad.add_article(ticket_id, note, internal=True, article_type="note", sender="Agent")

    fields: dict[str, object] = {"priority_id": config.zammad.overflow_priority}
    if current_state_id is not None and current_state_id != config.zammad.open_state_id:
        fields["state_id"] = config.zammad.open_state_id
    if not current_title:
        # Live beobachtet: ein Ticket mit leerem title (Zammad-UI zeigt
        # "-") laesst JEDES Update scheitern (HTTP 422 "Missing required
        # value for field 'title'"), da Zammad bei jedem PUT das gesamte
        # Modell validiert, nicht nur die uebergebenen Felder. Titel-Fix
        # daher zwingend im selben Request wie priority_id/state_id.
        fields["title"] = f"SMS-Ticket {ticket_number}"
    zammad.update_ticket(ticket_id, **fields)

    logger.warning(
        "Ticket %s: SMS-Versand nicht moeglich, Agent wurde per Vermerk informiert",
        ticket_number,
    )


def _handle_no_number(
    ticket_id: int,
    ticket_number: str,
    current_state_id: int | None,
    current_title: str | None,
    zammad: ZammadClient,
    config: Config,
    dry_run: bool,
) -> None:
    if dry_run:
        logger.info(
            "[dry-run] Ticket %s: keine Mobilfunknummer gefunden (Felder '%s'/'%s'), "
            "wuerde Tag '%s' setzen, Prioritaet auf %s setzen, internen Vermerk "
            "hinzufuegen und ggf. wieder oeffnen",
            ticket_number,
            config.zammad.phone_field,
            config.zammad.phone_field_fallback,
            TAG_CANNOT_SEND,
            config.zammad.overflow_priority,
        )
        return

    note = (
        f"SMS-Versand nicht moeglich: \n"
        f"Kunde hat weder im Feld '{config.zammad.phone_field}', noch im Feld "
        f"'{config.zammad.phone_field_fallback}' eine erkennbare Mobilfunknummer "
        f"hinterlegt.\n"
        f"Bitte Nummer im Kundendatensatz ergaenzen/korrigieren, danach Tag "
        f"'{TAG_OUT}' erneut setzen, um einen neuen Versandversuch auszuloesen."
    )
    _mark_cannot_send(ticket_id, ticket_number, current_state_id, current_title, note, zammad, config)


def _process_one(
    ticket_id: int,
    zammad: ZammadClient,
    teltonika: TeltonikaClient,
    config: Config,
    dry_run: bool,
    budget: SmsBudget,
    budget_blocked: list[str],
) -> None:
    ticket = zammad.get_ticket(ticket_id)
    ticket_number = ticket["number"]
    current_state_id = ticket.get("state_id")
    current_title = ticket.get("title")
    customer = zammad.get_user(ticket["customer_id"])
    number = _resolve_send_number(customer, config)
    if number is None:
        _handle_no_number(
            ticket_id, ticket_number, current_state_id, current_title, zammad, config, dry_run
        )
        return

    articles = zammad.get_ticket_articles(ticket_id)
    # Nur oeffentliche Anruf-Artikel VOM AGENTEN sind SMS-Quelle -- NIE
    # einfach den letzten Artikel nehmen. Zwei Faellen, die das verhindern
    # soll: (1) eine interne Notiz (fuer einen Kollegen gedacht) geht
    # versehentlich als SMS raus; (2) bei einem neu angelegten Ticket ist der
    # aeltere/einzige oeffentliche Anruf-Artikel die EINGEHENDE Kundennach-
    # richt selbst (sender="Customer") -- ohne die sender-Pruefung wuerde der
    # Kunde seine eigene Nachricht als "Antwort" zurueckbekommen. Konvention:
    # Agent schreibt im "Anruf"-Tab, markiert oeffentlich, ein Zammad-Trigger
    # setzt darauf den Tag 'sms-out'.
    agent_calls = [
        a
        for a in articles
        if a.get("type") == "phone" and not a.get("internal", True) and a.get("sender") == "Agent"
    ]
    if not agent_calls:
        raise ValueError(
            f"Ticket {ticket_number}: kein oeffentlicher Anruf-Artikel vom Agenten vorhanden "
            f"(SMS-Antwort muss im 'Anruf'-Tab, nicht intern, verfasst werden)"
        )
    text = html_to_text(agent_calls[-1]["body"])
    agent = agent_calls[-1].get("from")
    group_name = zammad.get_group_name(ticket["group_id"]) if ticket.get("group_id") else None

    parts = split_for_sms(text, limit=150)
    max_parts = config.ticket_to_sms.max_sms_parts

    if len(parts) > max_parts:
        if config.ticket_to_sms.on_overflow == "truncate":
            total_parts = len(parts)
            _send(
                ticket_id,
                ticket_number,
                current_state_id,
                current_title,
                number,
                text,
                parts[:max_parts],
                zammad,
                teltonika,
                config,
                dry_run,
                budget,
                budget_blocked,
                group_name,
                agent,
                truncated_from=total_parts,
            )
        else:
            _handle_overflow_reject(
                ticket_id,
                ticket_number,
                current_state_id,
                current_title,
                len(parts),
                max_parts,
                zammad,
                config,
                dry_run,
            )
        return

    _send(
        ticket_id,
        ticket_number,
        current_state_id,
        current_title,
        number,
        text,
        parts,
        zammad,
        teltonika,
        config,
        dry_run,
        budget,
        budget_blocked,
        group_name,
        agent,
    )


def _build_send_note(
    text: str,
    parts: list[str],
    now_str: str,
    truncated_from: int | None,
    alarm_hint: str | None = None,
) -> str:
    parts_label = "1 SMS" if len(parts) == 1 else f"{len(parts)} SMS-Teile"
    header = (
        f"SMS-Versand: {len(text)} Zeichen, {parts_label} am {now_str} an den Router "
        f"uebergeben (keine SMS-Quittung verfuegbar)."
    )
    sent_text = "\n".join(f'"{part}"' for part in parts)
    note = f"{header}\n\nGesendeter Text:\n{sent_text}"
    if truncated_from is not None:
        warning = (
            f"ACHTUNG: Text war zu lang ({truncated_from} SMS-Teile noetig, erlaubt: "
            f"{len(parts)}). Nur die ersten {len(parts)} Teile wurden gesendet, der Rest "
            f"wurde NICHT verschickt."
        )
        note = f"{warning}\n\n{note}"
    if alarm_hint is not None:
        note = f"{note}\n\n{alarm_hint}"
    return note


def _low_balance_hint(config: Config, budget: SmsBudget) -> str | None:
    if config.balance is None:
        return None
    latest = budget.latest_balance()
    if latest is not None and latest[1] < config.balance.alarm_threshold_eur:
        return "SMS-Guthaben ist sehr niedrig, SMS wurde evtl. nicht gesendet"
    return None


def _send(
    ticket_id: int,
    ticket_number: str,
    current_state_id: int | None,
    current_title: str | None,
    number: str,
    text: str,
    parts: list[str],
    zammad: ZammadClient,
    teltonika: TeltonikaClient,
    config: Config,
    dry_run: bool,
    budget: SmsBudget,
    budget_blocked: list[str],
    group_name: str | None = None,
    agent: str | None = None,
    truncated_from: int | None = None,
) -> None:
    if not budget.can_send(len(parts)):
        _handle_budget_blocked(ticket_id, ticket_number, len(parts), zammad, budget, dry_run, budget_blocked)
        return

    if dry_run:
        alarm_hint = _low_balance_hint(config, budget)
        logger.info(
            "[dry-run] wuerde %d SMS-Teil(e) an %s senden (Ticket %s)%s: %r -- danach internen "
            "Vermerk mit Sendezeit hinzufuegen%s",
            len(parts),
            number,
            ticket_number,
            f" (gekuerzt von {truncated_from})" if truncated_from is not None else "",
            [redact_content(p) for p in parts],
            f" (inkl. Hinweis: {alarm_hint!r})" if alarm_hint else "",
        )
        return

    try:
        for part in parts:
            teltonika.send(number, part)
    except TeltonikaError as exc:
        _handle_send_failed(
            ticket_id, ticket_number, current_state_id, current_title, exc, zammad, config
        )
        return
    budget.record_sent(len(parts), group=group_name, agent=agent, ticket_number=ticket_number)

    now_str = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S")
    alarm_hint = _low_balance_hint(config, budget)
    note = _build_send_note(text, parts, now_str, truncated_from, alarm_hint)
    zammad.add_article(ticket_id, note, internal=True, article_type="note", sender="Agent")
    zammad.remove_tag(ticket_id, TAG_OUT)
    zammad.add_tag(ticket_id, TAG_SENT)
    # Falls zuvor ein Budget-Wartehinweis gesetzt wurde: entfernen, damit ein
    # spaeterer erneuter Engpass wieder eine frische Notiz bekommt.
    if TAG_BUDGET_WAIT in zammad.get_tags(ticket_id):
        zammad.remove_tag(ticket_id, TAG_BUDGET_WAIT)

    parts_label = "1 SMS" if len(parts) == 1 else f"{len(parts)} SMS-Teile"
    logger.info("Ticket %s: %s erfolgreich an %s uebergeben", ticket_number, parts_label, number)


def _handle_send_failed(
    ticket_id: int,
    ticket_number: str,
    current_state_id: int | None,
    current_title: str | None,
    error: Exception,
    zammad: ZammadClient,
    config: Config,
) -> None:
    """Der Router hat den Sendeversuch selbst abgelehnt/ist nicht
    erreichbar -- z.B. kein SMS-Guthaben mehr auf der SIM-Karte, oder ein
    Netzwerk-/Auth-Problem. Statt endlos still mit Tag `sms-out` erneut zu
    versuchen (bisheriges Verhalten: nur ein Log-Eintrag, kein Hinweis in
    Zammad), wird das Ticket getaggt und der Agent per Vermerk informiert.
    """
    note = (
        f"SMS-Versand fehlgeschlagen: {error}\n\n"
        "Moegliche Ursache: kein SMS-Guthaben mehr auf der SIM-Karte im "
        "Router, oder ein Netzwerk-/Zugangsproblem zum Router. Bitte "
        "Guthaben/Router pruefen und danach Tag "
        f"'{TAG_OUT}' erneut setzen, um einen neuen Versandversuch auszuloesen."
    )
    logger.error("Ticket %s: SMS-Versand fehlgeschlagen (%s)", ticket_number, error)
    _mark_cannot_send(ticket_id, ticket_number, current_state_id, current_title, note, zammad, config)


def _handle_budget_blocked(
    ticket_id: int,
    ticket_number: str,
    parts_needed: int,
    zammad: ZammadClient,
    budget: SmsBudget,
    dry_run: bool,
    budget_blocked: list[str],
) -> None:
    status = budget.status()
    eta = budget.next_available_at(parts_needed)
    eta_str = eta.astimezone().strftime("%d.%m.%Y %H:%M:%S")
    logger.warning(
        "Ticket %s: SMS-Budget erschoepft (%d/%dh, %d/%d24h), %d Teil(e) zurueckgestellt, "
        "voraussichtlich moeglich ab %s, Tag '%s' bleibt fuer naechsten Lauf stehen",
        ticket_number,
        status.sent_last_hour,
        status.max_per_hour,
        status.sent_last_24h,
        status.max_per_24h,
        parts_needed,
        eta_str,
        TAG_OUT,
    )
    budget_blocked.append(ticket_number)

    if dry_run:
        logger.info(
            "[dry-run] Ticket %s: wuerde ggf. einmaligen Budget-Hinweis mit ETA %s hinzufuegen",
            ticket_number,
            eta_str,
        )
        return

    if TAG_BUDGET_WAIT not in zammad.get_tags(ticket_id):
        note = (
            f"SMS-Versand verzoegert: Stunden-/24h-Sende-Limit erreicht "
            f"({status.sent_last_hour}/{status.max_per_hour} pro Stunde, "
            f"{status.sent_last_24h}/{status.max_per_24h} pro 24h). Voraussichtlich moeglich "
            f"ab {eta_str}. Dieser Hinweis erscheint nur einmalig, bis der Versand "
            f"erfolgreich war."
        )
        zammad.add_article(ticket_id, note, internal=True, article_type="note", sender="Agent")
        zammad.add_tag(ticket_id, TAG_BUDGET_WAIT)


def _handle_overflow_reject(
    ticket_id: int,
    ticket_number: str,
    current_state_id: int | None,
    current_title: str | None,
    part_count: int,
    max_parts: int,
    zammad: ZammadClient,
    config: Config,
    dry_run: bool,
) -> None:
    note = (
        f"SMS-Versand abgebrochen: Nachricht wuerde {part_count} SMS-Teile "
        f"ergeben, erlaubt sind maximal {max_parts}. Bitte eine neue, kuerzere "
        f"Nachricht verfassen (bereits geschriebene Texte lassen sich nicht "
        f"nachtraeglich aendern). Tag '{TAG_OUT}' ggf. neu setzen, falls der "
        f"automatische Trigger dafuer nicht eingerichtet ist."
    )
    if dry_run:
        logger.info(
            "[dry-run] Ticket %s: Ueberlauf (%d > %d Teile), wuerde Tags '%s'/'%s' setzen, "
            "internen Vermerk hinzufuegen, Prioritaet auf %s setzen und ggf. wieder oeffnen",
            ticket_number,
            part_count,
            max_parts,
            TAG_OVERFLOW,
            TAG_CANNOT_SEND,
            config.zammad.overflow_priority,
        )
        return

    _mark_cannot_send(
        ticket_id,
        ticket_number,
        current_state_id,
        current_title,
        note,
        zammad,
        config,
        extra_tags=(TAG_OVERFLOW,),
    )


def _format_breakdown(budget: SmsBudget) -> str:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = budget.summary_by_group_and_agent(since, direction="out")
    if not rows:
        return "Aufschluesselung letzte 24h: (keine Versanddaten)"
    lines = ["Aufschluesselung letzte 24h (Gruppe / Agent / Anzahl):"]
    for row in rows:
        group = row.group_name or "(unbekannt)"
        agent = row.agent or "(unbekannt)"
        lines.append(f"- {group} / {agent}: {row.count}")
    return "\n".join(lines)


def _notify_budget_exceeded(blocked_tickets: list[str], budget: SmsBudget, config: Config) -> None:
    cooldown = config.ticket_to_sms.budget_notify_cooldown_minutes
    if not budget.should_notify(cooldown):
        logger.info(
            "SMS-Budget weiterhin ueberschritten, aber Cooldown (%d min) noch aktiv -- keine Mail",
            cooldown,
        )
        return

    status = budget.status()
    body = (
        f"SMS-Sende-Budget ueberschritten: {status.sent_last_hour}/{status.max_per_hour} "
        f"pro Stunde, {status.sent_last_24h}/{status.max_per_24h} pro 24h.\n\n"
        f"Zurueckgestellte Tickets ({len(blocked_tickets)}): {', '.join(blocked_tickets)}\n\n"
        f"Diese Tickets werden automatisch erneut versucht, sobald wieder Budget frei ist.\n\n"
        f"{_format_breakdown(budget)}"
    )
    try:
        send_mail(config.notification, "SMSammad: SMS-Budget ueberschritten", body)
        budget.mark_notified()
    except Exception:
        logger.exception("Budget-Ueberschreitungs-Mail konnte nicht verschickt werden")
