"""Subcommand 'check-setup': prueft (und -- nur mit --fix -- repariert)
die Zammad-seitige Installation, die SMSammad fuer den Betrieb braucht:

1. Einen aktiven Trigger, der bei einem oeffentlichen Anruf-Artikel vom
   Agenten den Tag 'sms-out' setzt (siehe ticket_to_sms.py).
2. Vollen Gruppenzugriff des eigenen API-Users auf die konfigurierten
   Default-Gruppen ([zammad] group/new_customer_group).

Beides ist HART abgeschaltet, solange [zammad] self_manage_setup nicht
explizit auf true steht (Default False) -- dieses Feature erfordert einen
API-Token mit erweiterten Rechten (Trigger-Verwaltung, Nutzerverwaltung),
die nicht jede Installation vergeben moechte/sollte, und laesst sich so
jederzeit per config.ini wieder hart abschalten (z.B. falls sich Zammads
Trigger-/Berechtigungs-API in einem Update aendert). Selbst wenn
aktiviert, werden tatsaechliche AENDERUNGEN (Trigger anlegen,
Gruppenzugriff gewaehren) nur mit dem zusaetzlichen --fix-Flag
vorgenommen -- ohne --fix wird nur geprueft und berichtet.

IDs fuer Artikel-Typ "phone" (5) und Artikel-Absender "Agent" (1) sind
Standard-Zammad-Seed-Daten (db/seeds/ticket_article_types.rb bzw.
ticket_article_senders.rb, live gegen die echte Instanz verifiziert) --
bei stark abweichenden Installationen muesste das manuell angepasst
werden.
"""

import logging

from .config import Config
from .zammad import ZammadClient, ZammadError

logger = logging.getLogger("smsammad")

TRIGGER_TAG = "sms-out"

_ARTICLE_TYPE_PHONE_ID = "5"
_ARTICLE_SENDER_AGENT_ID = "1"

_TRIGGER_PAYLOAD = {
    "name": "SMS-Out (SMSammad)",
    "condition": {
        "article.action": {"operator": "is", "value": "create"},
        "article.type_id": {"operator": "is", "value": [_ARTICLE_TYPE_PHONE_ID]},
        "article.internal": {"operator": "is", "value": ["false"]},
        "article.sender_id": {"operator": "is", "value": [_ARTICLE_SENDER_AGENT_ID]},
    },
    "perform": {
        "ticket.tags": {"operator": "add", "value": TRIGGER_TAG},
    },
    "disable_notification": True,
    "note": (
        "Automatisch von SMSammad angelegt (check-setup --fix): oeffentlicher "
        "Anruf-Artikel vom Agenten bekommt Tag 'sms-out', damit ticket-to-sms ihn findet."
    ),
    "active": True,
}


def _trigger_matches(trigger: dict) -> bool:
    """True, wenn dieser aktive Trigger bei Erstellung eines oeffentlichen
    Anruf-Artikels vom Agenten den Tag 'sms-out' setzt -- unabhaengig vom
    Namen (der ist frei waehlbar, z.B. individuell umbenannt)."""
    if not trigger.get("active"):
        return False
    perform = trigger.get("perform") or {}
    tags_action = perform.get("ticket.tags") or {}
    if tags_action.get("operator") != "add":
        return False
    tags = {t.strip() for t in (tags_action.get("value") or "").split(",")}
    if TRIGGER_TAG not in tags:
        return False
    condition = trigger.get("condition") or {}
    type_ok = _ARTICLE_TYPE_PHONE_ID in (condition.get("article.type_id", {}).get("value") or [])
    sender_ok = _ARTICLE_SENDER_AGENT_ID in (
        condition.get("article.sender_id", {}).get("value") or []
    )
    internal_ok = "false" in (condition.get("article.internal", {}).get("value") or [])
    return type_ok and sender_ok and internal_ok


def _check_trigger(zammad: ZammadClient) -> tuple[bool | None, str]:
    """Liefert (Status, Meldung). Status None bedeutet "nicht pruefbar"
    (z.B. fehlende Berechtigung fuer die Trigger-Liste) -- WICHTIG: das
    ist bewusst NICHT dasselbe wie False ("bestaetigt fehlend"), sonst
    wuerde run() bei --fix versuchen, einen moeglicherweise bereits
    existierenden Trigger ein zweites Mal anzulegen, nur weil die Liste
    gerade nicht abrufbar war."""
    try:
        triggers = zammad.list_triggers()
    except ZammadError as exc:
        return None, f"Trigger-Liste nicht abrufbar (fehlende Berechtigung?): {exc}"
    matching = [t for t in triggers if _trigger_matches(t)]
    if matching:
        names = ", ".join(f"{t['name']!r} (id={t['id']})" for t in matching)
        return True, f"Trigger gefunden: {names}"
    return (
        False,
        "Kein passender Trigger gefunden (aktiv, setzt Tag 'sms-out' bei oeffentlichem "
        "Anruf-Artikel vom Agenten)",
    )


def _create_trigger(zammad: ZammadClient) -> tuple[bool, str]:
    try:
        created = zammad.create_trigger(_TRIGGER_PAYLOAD)
    except ZammadError as exc:
        return False, f"Trigger-Anlage fehlgeschlagen (fehlende Berechtigung?): {exc}"
    return True, f"Trigger angelegt: {created.get('name')!r} (id={created.get('id')})"


def _resolve_group_id(
    zammad: ZammadClient, group_name: str
) -> tuple[int | None, str | None, str | None]:
    """Liefert (group_id, Vorschlag, Fehlermeldung). Kein exakter Treffer,
    aber GENAU EINE verschachtelte Gruppe, deren letztes '::'-Segment dem
    konfigurierten Kurznamen entspricht ("Eltern::Kind" vs. "Kind") ->
    Vorschlag gesetzt. Live beobachtet: das ist ein haeufiger echter
    Konfigurationsfehler (Zammad-Untergruppen brauchen den vollen Pfad),
    der sonst nur als kryptischer HTTP 422 bei der Ticket-Anlage auffaellt.
    """
    try:
        groups = zammad.list_groups()
    except ZammadError as exc:
        return None, None, f"Gruppen-Liste nicht abrufbar (fehlende Berechtigung?): {exc}"
    for group in groups:
        if group.get("name") == group_name:
            return group.get("id"), None, None
    suffix = f"::{group_name}"
    candidates = [g["name"] for g in groups if g.get("name", "").endswith(suffix)]
    suggestion = candidates[0] if len(candidates) == 1 else None
    return None, suggestion, None


def _has_full_access(my_user: dict, group_id: int) -> bool:
    group_ids = my_user.get("group_ids") or {}
    return "full" in (group_ids.get(str(group_id)) or [])


def _check_group_access(
    zammad: ZammadClient, group_name: str
) -> tuple[bool | None, str, int | None]:
    group_id, suggestion, list_error = _resolve_group_id(zammad, group_name)
    if list_error is not None:
        return None, list_error, None
    if group_id is None:
        hint = f" -- meintest du {suggestion!r} (verschachtelte Gruppe)?" if suggestion else ""
        return False, f"Gruppe {group_name!r} existiert nicht in Zammad{hint}", None
    try:
        my_user = zammad.get_my_user()
    except ZammadError as exc:
        return None, f"Eigener Zugriff nicht pruefbar (fehlende Berechtigung?): {exc}", group_id
    if _has_full_access(my_user, group_id):
        return True, f"Voller Zugriff auf Gruppe {group_name!r} (id={group_id}) vorhanden", group_id
    return False, f"KEIN voller Zugriff auf Gruppe {group_name!r} (id={group_id})", group_id


def _grant_group_access(zammad: ZammadClient, group_id: int, group_name: str) -> tuple[bool, str]:
    try:
        my_user = zammad.get_my_user()
        group_ids = dict(my_user.get("group_ids") or {})
        group_ids[str(group_id)] = ["full"]
        zammad.update_user(my_user["id"], group_ids=group_ids)
    except ZammadError as exc:
        return False, f"Zugriff auf {group_name!r} konnte nicht gewaehrt werden (fehlende Berechtigung?): {exc}"
    return True, f"Vollen Zugriff auf Gruppe {group_name!r} (id={group_id}) gewaehrt"


# Standard-Zammad-Seed-Daten (db/seeds/ticket_state_types.rb, live gegen
# die echte Instanz verifiziert): 2="open", 5="closed".
_STATE_TYPE_OPEN_ID = 2
_STATE_TYPE_CLOSED_ID = 5


def _check_state(
    zammad: ZammadClient, option_label: str, state_id: int, expected_type_id: int, type_label: str
) -> tuple[bool | None, str]:
    """Rein lesende Diagnose (kein --fix dafuer -- ein falscher Status/eine
    falsche Prioritaet ist ein Tippfehler in der config.ini, den ein
    Mensch beheben muss, kein automatisch korrigierbarer Zustand)."""
    try:
        states = zammad.list_ticket_states()
    except ZammadError as exc:
        return None, f"{option_label}: Ticket-Status-Liste nicht abrufbar (fehlende Berechtigung?): {exc}"
    match = next((s for s in states if s.get("id") == state_id), None)
    if match is None:
        return False, f"{option_label}={state_id} existiert nicht als Zammad-Ticketstatus"
    if match.get("state_type_id") != expected_type_id:
        return False, (
            f"{option_label}={state_id} ({match.get('name')!r}) existiert, ist aber semantisch "
            f"NICHT '{type_label}' (state_type_id={match.get('state_type_id')})"
        )
    return True, f"{option_label}={state_id} ({match.get('name')!r}) korrekt als '{type_label}' erkannt"


def _check_priority(zammad: ZammadClient, option_label: str, priority_id: int) -> tuple[bool | None, str]:
    try:
        priorities = zammad.list_ticket_priorities()
    except ZammadError as exc:
        return None, f"{option_label}: Prioritaeten-Liste nicht abrufbar (fehlende Berechtigung?): {exc}"
    match = next((p for p in priorities if p.get("id") == priority_id), None)
    if match is None:
        return False, f"{option_label}={priority_id} existiert nicht als Zammad-Prioritaet"
    return True, f"{option_label}={priority_id} ({match.get('name')!r}) existiert"


def _check_user_attribute(zammad: ZammadClient, option_label: str, field_name: str) -> tuple[bool | None, str]:
    try:
        attrs = zammad.list_user_attributes()
    except ZammadError as exc:
        return None, f"{option_label}: User-Attribut-Liste nicht abrufbar (fehlende Berechtigung?): {exc}"
    match = next((a for a in attrs if a.get("object") == "User" and a.get("name") == field_name), None)
    if match is None:
        return False, f"{option_label}={field_name!r} existiert nicht als User-Attribut in Zammad"
    if not match.get("active", True):
        return False, f"{option_label}={field_name!r} existiert, ist aber in Zammad deaktiviert"
    return True, f"{option_label}={field_name!r} existiert und ist aktiv"


def _log_check(problems: list[str], label: str, ok: bool | None, msg: str) -> None:
    """Loggt das Ergebnis EINES Checks und sammelt es in `problems`, falls
    es kein bestaetigtes 'True' ist (False = bestaetigt kaputt, None =
    nicht pruefbar -- BEIDES ist fuer den Cron-Betrieb ein Grund, sich zu
    melden: bei None hat der Token vermutlich nicht die erwarteten
    Rechte)."""
    if ok is False:
        logger.warning("check-setup: %s", msg)
        problems.append(f"{label}: {msg}")
    elif ok is None:
        logger.warning("check-setup: %s", msg)
        problems.append(f"{label} (nicht pruefbar): {msg}")
    else:
        logger.info("check-setup: %s", msg)


def run(zammad: ZammadClient, config: Config, fix: bool, dry_run: bool) -> None:
    if not config.zammad.self_manage_setup:
        logger.info(
            "check-setup: [zammad] self_manage_setup ist deaktiviert (Default) -- keine Aktion. "
            "In der config.ini auf true setzen, um diesen Check zu aktivieren."
        )
        return

    problems: list[str] = []
    changes: list[str] = []

    logger.info("check-setup: pruefe Trigger fuer Tag '%s' ...", TRIGGER_TAG)
    trigger_ok, trigger_msg = _check_trigger(zammad)
    # trigger_ok == False (bestaetigt fehlend) noetig fuer --fix -- bei
    # None (nicht pruefbar, z.B. fehlende Berechtigung) NICHT versuchen,
    # sonst koennte ein eventuell schon vorhandener Trigger doppelt
    # angelegt werden.
    if trigger_ok is False and fix and not dry_run:
        logger.info("check-setup: --fix gesetzt, lege Trigger an ...")
        created, create_msg = _create_trigger(zammad)
        logger.info("check-setup: %s", create_msg)
        if created:
            changes.append(create_msg)
        else:
            problems.append(f"Trigger anlegen fehlgeschlagen: {create_msg}")
    elif trigger_ok is False and fix and dry_run:
        logger.info("[dry-run] check-setup: wuerde Trigger anlegen")
        _log_check(problems, "Trigger", trigger_ok, trigger_msg)
    elif trigger_ok is False:
        logger.warning(
            "check-setup: Trigger fehlt -- mit --fix erneut aufrufen, um ihn automatisch "
            "anzulegen (erfordert Trigger-Verwaltungsrechte)."
        )
        _log_check(problems, "Trigger", trigger_ok, trigger_msg)
    else:
        _log_check(problems, "Trigger", trigger_ok, trigger_msg)

    # dict.fromkeys() statt set(): dedupliziert (group == new_customer_group
    # ist ein gueltiges Setup), aber deterministische Reihenfolge im Log.
    group_names = dict.fromkeys([config.zammad.group, config.zammad.new_customer_group])
    for group_name in group_names:
        logger.info("check-setup: pruefe Gruppenzugriff auf '%s' ...", group_name)
        has_access, group_msg, group_id = _check_group_access(zammad, group_name)
        label = f"Gruppenzugriff '{group_name}'"
        # has_access is False (bestaetigt fehlend) noetig fuer --fix -- bei
        # None (nicht pruefbar) oder True (schon vorhanden) nichts tun.
        if has_access is False and group_id is not None and fix and not dry_run:
            logger.info("check-setup: --fix gesetzt, gewaehre Zugriff auf '%s' ...", group_name)
            granted, grant_msg = _grant_group_access(zammad, group_id, group_name)
            logger.info("check-setup: %s", grant_msg)
            if granted:
                changes.append(grant_msg)
            else:
                problems.append(f"{label}: Gewaehren fehlgeschlagen: {grant_msg}")
        elif has_access is False and group_id is not None and fix and dry_run:
            logger.info("[dry-run] check-setup: wuerde vollen Zugriff auf '%s' gewaehren", group_name)
            _log_check(problems, label, has_access, group_msg)
        elif has_access is False and group_id is not None:
            logger.warning(
                "check-setup: kein Zugriff auf Gruppe '%s' -- mit --fix erneut aufrufen, um "
                "ihn automatisch zu gewaehren (erfordert Admin-Rechte).",
                group_name,
            )
            _log_check(problems, label, has_access, group_msg)
        else:
            _log_check(problems, label, has_access, group_msg)

    # Ab hier rein lesende Diagnose-Checks -- kein --fix dafuer, ein
    # falscher State/eine falsche Prioritaet/ein falscher Feldname ist ein
    # Tippfehler in der config.ini, den ein Mensch beheben muss.
    logger.info("check-setup: pruefe [zammad] open_state_id ...")
    ok, msg = _check_state(
        zammad, "open_state_id", config.zammad.open_state_id, _STATE_TYPE_OPEN_ID, "open"
    )
    _log_check(problems, "open_state_id", ok, msg)

    if config.balance is not None:
        logger.info("check-setup: pruefe [balance] closed_state_id ...")
        ok, msg = _check_state(
            zammad,
            "closed_state_id",
            config.balance.closed_state_id,
            _STATE_TYPE_CLOSED_ID,
            "closed",
        )
        _log_check(problems, "closed_state_id", ok, msg)

    logger.info("check-setup: pruefe [zammad] overflow_priority ...")
    ok, msg = _check_priority(zammad, "overflow_priority", config.zammad.overflow_priority)
    _log_check(problems, "overflow_priority", ok, msg)

    logger.info("check-setup: pruefe [zammad] phone_field ...")
    ok, msg = _check_user_attribute(zammad, "phone_field", config.zammad.phone_field)
    _log_check(problems, "phone_field", ok, msg)

    logger.info("check-setup: pruefe [zammad] phone_field_fallback ...")
    ok, msg = _check_user_attribute(
        zammad, "phone_field_fallback", config.zammad.phone_field_fallback
    )
    _log_check(problems, "phone_field_fallback", ok, msg)

    if changes:
        logger.info(
            "check-setup: Änderungen am Zammad-Setup durchgeführt:\n"
            + "\n".join(f"- {c}" for c in changes)
        )

    if problems and not dry_run:
        # Kein eigener Mail-Versand hier -- main.py verschickt bei jeder
        # unbehandelten Exception ohnehin bereits eine Fehlermail (siehe
        # [notification] in der config.ini), also einfach denselben Weg
        # nutzen statt einen zweiten Mail-Pfad zu pflegen.
        sections = []
        if changes:
            sections.append(
                "Änderungen am Zammad-Setup durchgeführt:\n"
                + "\n".join(f"- {c}" for c in changes)
            )
        sections.append(
            "Was Du als Zammad-Admin evtl. ändern solltest:\n"
            + "\n".join(f"- {p}" for p in problems)
        )
        raise RuntimeError("\n\n".join(sections))
