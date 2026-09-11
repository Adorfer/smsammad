"""Schutz gegen Teltonikas fail2ban-Mechanismus (20 Fehlversuche/24h fuehren
zu einer dauerhaften Sperre) bei falschen Zugangsdaten oder fehlenden
Berechtigungen. Zustand pro Zugang (Scope "cgi" fuer das cgi-bin-
SMS-Gateway, "api" fuer die REST-API/USSD) in derselben SQLite-Datei wie
das SMS-Budget (sms_budget.py, access_state-Tabelle).

Verfahren, User-Wunsch:
- Erster Fehlversuch -> EIN Wiederholversuch nach kurzer Wartezeit
  (bewusst NICHT der bestehende retry_*-Mechanismus in teltonika.py --
  der ist fuer transiente Router-Ueberlastung gedacht, hier geht es um
  einen Auth-Fehler; ein dritter, vierter, ... Versuch waere nur
  zusaetzlicher Beitrag zu Teltonikas 24h-Zaehler).
- Scheitert auch der zweite Versuch: Zugang fuer diesen UND alle
  folgenden Laeufe sperren (kein weiterer Router-Kontakt in dieser
  Zeit -- kein weiterer Beitrag zum fail2ban-Zaehler), GENAU EINE Mail.
- Wiederversuch fruehestens nach 4h, bei erneutem Fehlschlag 8h, danach
  gedeckelt bei 24h (konfigurierbar ueber `stages_hours`).
- Erfolg nach vorherigem Fehler: Zustand zuruecksetzen, GENAU EINE
  Entwarnungsmail. Waehrend der Sperre uebersprungene Laeufe bekommen
  KEINE weitere Mail (die ging schon beim Sperren raus) -- das ist der
  Kern gegen Mail-Spam bei jedem Cron-Lauf.
- Wurde die Sperre mit falschen Zugangsdaten ausgeloest und diese danach
  in der config.ini korrigiert, gilt die Sperre nicht mehr (Fingerprint-
  Abgleich in SmsBudget.access_blocked_until) -- die Verarbeitung laeuft
  sofort wieder an, ohne die Restlaufzeit abzuwarten.
- Im Dry-Run wird KEIN Sperr-Zustand persistiert (record_access_failure/
  _success uebersprungen) und keine Mail verschickt -- ein Test mit
  falschen Zugangsdaten darf die produktiven Cron-Laeufe nicht
  stillschweigend aussperren.
"""

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Callable, TypeVar

from .config import NotificationConfig
from .notify import send_mail
from .sms_budget import SmsBudget

logger = logging.getLogger("smsammad")

T = TypeVar("T")

_RETRY_DELAY_SECONDS = 10

SCOPES = ("cgi", "api")

# Der Fingerprint-Abgleich hebt eine Sperre nur auf, wenn sich die
# Zugangsdaten in der config.ini aendern. Wird das Problem stattdessen am
# ROUTER behoben (Gruppenrecht nachgetragen, Post/Get aktiviert, Router-
# Passwort auf den Config-Wert zurueckgesetzt), bliebe die Sperre sonst
# bis zu 24h bestehen -- deshalb der Hinweis auf den manuellen Ausweg.
RESET_HINT = (
    "Wurde das Problem am Router behoben statt in der config.ini, die Sperre "
    "sofort aufheben mit: python3 run.py reset-access"
)


def fingerprint(*parts: str) -> str:
    """Stabiler, nicht umkehrbarer Fingerprint der Zugangsdaten -- landet
    in der SQLite-DB, damit eine Korrektur der Zugangsdaten eine bestehende
    Sperre automatisch aufhebt. Bewusst gehasht (kein Klartext), passend
    zur uebrigen Credential-Sorgfalt des Projekts. '\\0' als Trenner, damit
    ('a','bc') und ('ab','c') verschiedene Fingerprints ergeben."""
    joined = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:16]


class AccessBlocked(Exception):
    """Zugang aktuell gesperrt -- main.py behandelt das NICHT wie einen
    unerwarteten Absturz (kein Traceback in Log/Mail). `just_entered`
    unterscheidet zwei main.py-relevante Faelle: True = die Sperre wurde
    in DIESEM Lauf gerade erst ausgeloest (Mail ging bereits raus, Exit
    mit Fehlercode ist trotzdem angemessen); False = der Lauf wurde nur
    uebersprungen, weil eine vorherige Sperre noch laeuft (voellig
    normaler, stiller Fall -- Exit 0)."""

    def __init__(self, message: str, *, just_entered: bool) -> None:
        super().__init__(message)
        self.just_entered = just_entered


def guarded_call(
    budget: SmsBudget,
    scope: str,
    auth_error_types: tuple[type[Exception], ...],
    notification: NotificationConfig | None,
    action_label: str,
    fn: Callable[[], T],
    credential_fingerprint: str | None = None,
    dry_run: bool = False,
) -> T:
    """Fuehrt `fn()` aus, geschuetzt gegen wiederholte Auth-Fehlversuche
    fuer `scope`. Wirft AccessBlocked statt `fn()` je gemaess obigem
    Verfahren aufzurufen, wenn der Zugang gerade gesperrt ist bzw. gerade
    erst gesperrt wurde. Andere Fehler (nicht in `auth_error_types`)
    werden unveraendert durchgereicht -- kein Einfluss auf den
    Sperr-Zustand.

    `credential_fingerprint` (siehe access_guard.fingerprint): eine mit
    ANDEREN Zugangsdaten gesetzte Sperre gilt nicht -> korrigierte
    Zugangsdaten heben sie sofort auf.

    `dry_run`: kein Schreiben von Sperr-Zustand, keine Mail -- ein Test mit
    falschen Zugangsdaten darf die produktiven Laeufe nicht aussperren.
    Eine BEREITS bestehende (produktive) Sperre wird trotzdem beachtet, der
    Dry-Run zeigt damit korrekt, dass der echte Lauf uebersprungen wuerde.
    """
    blocked_until = budget.access_blocked_until(scope, credential_fingerprint)
    if blocked_until is not None:
        raise AccessBlocked(
            f"Zugang '{scope}' ({action_label}) weiterhin gesperrt bis "
            f"{blocked_until.isoformat()} -- ueberspringe diesen Lauf ohne Router-Kontakt. "
            f"{RESET_HINT}",
            just_entered=False,
        )

    try:
        result = fn()
    except auth_error_types:
        logger.warning(
            "Zugang '%s' (%s): Zugriffsfehler, ein Wiederholversuch in %ds",
            scope,
            action_label,
            _RETRY_DELAY_SECONDS,
        )
        time.sleep(_RETRY_DELAY_SECONDS)
        try:
            result = fn()
        except auth_error_types as exc2:
            if dry_run:
                # Kein Sperr-Zustand persistieren, keine Mail -- den echten
                # Auth-Fehler stattdessen unveraendert nach oben geben,
                # damit er im Dry-Run-Konsolen-Output klar sichtbar wird.
                raise
            new_blocked_until = budget.record_access_failure(scope, credential_fingerprint)
            message = (
                f"Zugang '{scope}' ({action_label}) meldet wiederholt Zugriffsfehler:\n\n"
                f"{exc2}\n\n"
                "Zum Schutz vor Teltonikas fail2ban-Sperrmechanismus (20 Fehlversuche "
                f"innerhalb 24h fuehren zu einer dauerhaften Sperre) wird dieser Zugang "
                f"jetzt bis {new_blocked_until.isoformat()} nicht mehr kontaktiert. "
                "Weitere Cron-Laeufe werden bis dahin ohne weitere Mail uebersprungen; "
                "bei Erfolg des naechsten Versuchs kommt automatisch eine Entwarnungsmail. "
                "Bitte Zugangsdaten/Berechtigungen pruefen.\n\n"
                "Korrigierte Zugangsdaten in der config.ini heben die Sperre automatisch "
                f"auf. {RESET_HINT}"
            )
            _try_send_mail(notification, f"SMSammad: Zugriff '{scope}' gesperrt", message)
            raise AccessBlocked(message, just_entered=True) from None
        else:
            _maybe_notify_recovered(budget, scope, action_label, notification, dry_run)
            return result
    else:
        _maybe_notify_recovered(budget, scope, action_label, notification, dry_run)
        return result


def _maybe_notify_recovered(
    budget: SmsBudget,
    scope: str,
    action_label: str,
    notification: NotificationConfig | None,
    dry_run: bool,
) -> None:
    if dry_run:
        return  # Dry-Run aendert keinen persistenten Zustand
    if budget.record_access_success(scope):
        _try_send_mail(
            notification,
            f"SMSammad: Zugriff '{scope}' wieder ok",
            f"Zugang '{scope}' ({action_label}) funktioniert wieder normal.",
        )


def run_reset(budget: SmsBudget, scope: str | None, dry_run: bool) -> None:
    """Subcommand reset-access: Sperr-Zustand manuell aufheben -- rein
    lokal in der SQLite-DB, KEIN Router-Kontakt (also selbst kein Beitrag
    zum fail2ban-Zaehler). Der naechste regulaere Lauf versucht es dann
    wieder, weiterhin mit hoechstens einem Wiederholversuch."""
    scopes = (scope,) if scope else SCOPES
    known = {s: (level, until) for s, level, until in budget.list_access_blocks()}
    now = datetime.now(timezone.utc)

    for s in scopes:
        if s not in known:
            logger.info("reset-access: Zugang '%s' hat keinen Sperr-Zustand, nichts zu tun", s)
            continue
        level, until = known[s]
        state = (
            f"gesperrt bis {until.isoformat()}"
            if until is not None and until > now
            else "Sperre abgelaufen, Eskalationsstufe noch gespeichert"
        )
        if dry_run:
            logger.info(
                "[dry-run] reset-access: wuerde Zugang '%s' freigeben (%s, Stufe %d)", s, state, level
            )
            continue
        budget.clear_access_block(s)
        logger.info("reset-access: Zugang '%s' freigegeben (war: %s, Stufe %d)", s, state, level)


def _try_send_mail(notification: NotificationConfig | None, subject: str, body: str) -> None:
    try:
        send_mail(notification, subject=subject, body=body)
    except Exception:
        logger.exception("Benachrichtigung per Mail konnte nicht verschickt werden")
