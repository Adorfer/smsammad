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


def _create_trigger(zammad: ZammadClient) -> str:
    try:
        created = zammad.create_trigger(_TRIGGER_PAYLOAD)
    except ZammadError as exc:
        return f"Trigger-Anlage fehlgeschlagen (fehlende Berechtigung?): {exc}"
    return f"Trigger angelegt: {created.get('name')!r} (id={created.get('id')})"


def _resolve_group_id(zammad: ZammadClient, group_name: str) -> int | None:
    for group in zammad.list_groups():
        if group.get("name") == group_name:
            return group.get("id")
    return None


def _has_full_access(my_user: dict, group_id: int) -> bool:
    group_ids = my_user.get("group_ids") or {}
    return "full" in (group_ids.get(str(group_id)) or [])


def _check_group_access(zammad: ZammadClient, group_name: str) -> tuple[bool, str, int | None]:
    group_id = _resolve_group_id(zammad, group_name)
    if group_id is None:
        return False, f"Gruppe {group_name!r} existiert nicht in Zammad", None
    try:
        my_user = zammad.get_my_user()
    except ZammadError as exc:
        return False, f"Eigener Zugriff nicht pruefbar (fehlende Berechtigung?): {exc}", group_id
    if _has_full_access(my_user, group_id):
        return True, f"Voller Zugriff auf Gruppe {group_name!r} (id={group_id}) vorhanden", group_id
    return False, f"KEIN voller Zugriff auf Gruppe {group_name!r} (id={group_id})", group_id


def _grant_group_access(zammad: ZammadClient, group_id: int, group_name: str) -> str:
    try:
        my_user = zammad.get_my_user()
        group_ids = dict(my_user.get("group_ids") or {})
        group_ids[str(group_id)] = ["full"]
        zammad.update_user(my_user["id"], group_ids=group_ids)
    except ZammadError as exc:
        return f"Zugriff auf {group_name!r} konnte nicht gewaehrt werden (fehlende Berechtigung?): {exc}"
    return f"Vollen Zugriff auf Gruppe {group_name!r} (id={group_id}) gewaehrt"


def run(zammad: ZammadClient, config: Config, fix: bool, dry_run: bool) -> None:
    if not config.zammad.self_manage_setup:
        logger.info(
            "check-setup: [zammad] self_manage_setup ist deaktiviert (Default) -- keine Aktion. "
            "In der config.ini auf true setzen, um diesen Check zu aktivieren."
        )
        return

    logger.info("check-setup: pruefe Trigger fuer Tag '%s' ...", TRIGGER_TAG)
    trigger_ok, trigger_msg = _check_trigger(zammad)
    logger.info("check-setup: %s", trigger_msg)
    # trigger_ok == False (bestaetigt fehlend) noetig fuer --fix -- bei
    # None (nicht pruefbar, z.B. fehlende Berechtigung) NICHT versuchen,
    # sonst koennte ein eventuell schon vorhandener Trigger doppelt
    # angelegt werden.
    if trigger_ok is False:
        if not fix:
            logger.warning(
                "check-setup: Trigger fehlt -- mit --fix erneut aufrufen, um ihn automatisch "
                "anzulegen (erfordert Trigger-Verwaltungsrechte)."
            )
        elif dry_run:
            logger.info("[dry-run] check-setup: wuerde Trigger anlegen")
        else:
            logger.info("check-setup: --fix gesetzt, lege Trigger an ...")
            logger.info("check-setup: %s", _create_trigger(zammad))

    # dict.fromkeys() statt set(): dedupliziert (group == new_customer_group
    # ist ein gueltiges Setup), aber deterministische Reihenfolge im Log.
    group_names = dict.fromkeys([config.zammad.group, config.zammad.new_customer_group])
    for group_name in group_names:
        logger.info("check-setup: pruefe Gruppenzugriff auf '%s' ...", group_name)
        has_access, group_msg, group_id = _check_group_access(zammad, group_name)
        logger.info("check-setup: %s", group_msg)
        if has_access or group_id is None:
            continue
        if not fix:
            logger.warning(
                "check-setup: kein Zugriff auf Gruppe '%s' -- mit --fix erneut aufrufen, um "
                "ihn automatisch zu gewaehren (erfordert Admin-Rechte).",
                group_name,
            )
        elif dry_run:
            logger.info("[dry-run] check-setup: wuerde vollen Zugriff auf '%s' gewaehren", group_name)
        else:
            logger.info("check-setup: --fix gesetzt, gewaehre Zugriff auf '%s' ...", group_name)
            logger.info("check-setup: %s", _grant_group_access(zammad, group_id, group_name))
