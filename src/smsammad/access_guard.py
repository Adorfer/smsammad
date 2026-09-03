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
"""

import logging
import time
from typing import Callable, TypeVar

from .config import NotificationConfig
from .notify import send_mail
from .sms_budget import SmsBudget

logger = logging.getLogger("smsammad")

T = TypeVar("T")

_RETRY_DELAY_SECONDS = 10


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
) -> T:
    """Fuehrt `fn()` aus, geschuetzt gegen wiederholte Auth-Fehlversuche
    fuer `scope`. Wirft AccessBlocked statt `fn()` je gemaess obigem
    Verfahren aufzurufen, wenn der Zugang gerade gesperrt ist bzw. gerade
    erst gesperrt wurde. Andere Fehler (nicht in `auth_error_types`)
    werden unveraendert durchgereicht -- kein Einfluss auf den
    Sperr-Zustand."""
    blocked_until = budget.access_blocked_until(scope)
    if blocked_until is not None:
        raise AccessBlocked(
            f"Zugang '{scope}' ({action_label}) weiterhin gesperrt bis "
            f"{blocked_until.isoformat()} -- ueberspringe diesen Lauf ohne Router-Kontakt.",
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
            new_blocked_until = budget.record_access_failure(scope)
            message = (
                f"Zugang '{scope}' ({action_label}) meldet wiederholt Zugriffsfehler:\n\n"
                f"{exc2}\n\n"
                "Zum Schutz vor Teltonikas fail2ban-Sperrmechanismus (20 Fehlversuche "
                f"innerhalb 24h fuehren zu einer dauerhaften Sperre) wird dieser Zugang "
                f"jetzt bis {new_blocked_until.isoformat()} nicht mehr kontaktiert. "
                "Weitere Cron-Laeufe werden bis dahin ohne weitere Mail uebersprungen; "
                "bei Erfolg des naechsten Versuchs kommt automatisch eine Entwarnungsmail. "
                "Bitte Zugangsdaten/Berechtigungen pruefen."
            )
            _try_send_mail(notification, f"SMSammad: Zugriff '{scope}' gesperrt", message)
            raise AccessBlocked(message, just_entered=True) from None
        else:
            _maybe_notify_recovered(budget, scope, action_label, notification)
            return result
    else:
        _maybe_notify_recovered(budget, scope, action_label, notification)
        return result


def _maybe_notify_recovered(
    budget: SmsBudget, scope: str, action_label: str, notification: NotificationConfig | None
) -> None:
    if budget.record_access_success(scope):
        _try_send_mail(
            notification,
            f"SMSammad: Zugriff '{scope}' wieder ok",
            f"Zugang '{scope}' ({action_label}) funktioniert wieder normal.",
        )


def _try_send_mail(notification: NotificationConfig | None, subject: str, body: str) -> None:
    try:
        send_mail(notification, subject=subject, body=body)
    except Exception:
        logger.exception("Benachrichtigung per Mail konnte nicht verschickt werden")
