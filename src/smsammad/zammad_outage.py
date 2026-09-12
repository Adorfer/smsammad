"""Daempfung der Fehlermail, wenn Zammad voruebergehend nicht erreichbar ist.

Live-Anlass: ein Update des Zammad-Hosts liess nginx "502 Bad Gateway"
liefern, waehrend der Railsserver neu startete. Bei 5-Minuten-Cron haette
jeder Task in dieser Zeit pro Lauf eine Fehlermail mit Traceback erzeugt.

Verfahren (Zustand in der SQLite-DB, siehe SmsBudget.*zammad_outage*):
- Kurze Aussetzer faengt schon ZammadClient mit Wiederholungen ab
  (nur GET, siehe zammad.py).
- Scheitert ein Lauf trotzdem an ZammadUnavailable, wird der Beginn des
  Ausfalls festgehalten. Solange er kuerzer als
  [zammad] outage_notify_after_minutes (Default 20) andauert: nur Log,
  keine Mail, Exit 0 (auch cron bleibt still).
- Danach GENAU EINE Mail pro Ausfall (task-uebergreifend), weitere Laeufe
  waehrend desselben Ausfalls bleiben still.
- Antwortet Zammad wieder: Zustand loeschen und, falls eine Ausfall-Mail
  rausging, GENAU EINE Entwarnungsmail.

Nichts geht dabei verloren: ein abgebrochener Lauf hat nichts veraendert
bzw. laesst Tickets mit Tag 'sms-out' und SMS auf dem Router liegen, der
naechste Lauf holt sie nach.
"""

import logging
from datetime import datetime, timedelta, timezone

from .config import NotificationConfig
from .notify import send_mail
from .sms_budget import SmsBudget

logger = logging.getLogger("smsammad")


class ZammadOutageTracker:
    def __init__(
        self,
        budget: SmsBudget,
        notification: NotificationConfig | None,
        notify_after_minutes: int,
    ) -> None:
        self._budget = budget
        self._notification = notification
        self._notify_after = timedelta(minutes=notify_after_minutes)

    def record_reachable(self) -> None:
        """Als ZammadClient(on_reachable=...) eingehaengt: Zammad hat
        geantwortet -> ein laufender Ausfall ist vorbei."""
        if self._budget.clear_zammad_outage():
            logger.info("Zammad wieder erreichbar -- Entwarnung per Mail")
            self._try_send_mail(
                "SMSammad: Zammad wieder erreichbar",
                "Zammad antwortet wieder normal. Waehrend des Ausfalls liegengebliebene "
                "Tickets mit Tag 'sms-out' und SMS auf dem Router werden in den naechsten "
                "Laeufen automatisch nachgeholt.",
            )

    def record_unavailable(self, error: Exception, command: str, now: datetime | None = None) -> bool:
        """Ein Lauf ist an ZammadUnavailable gescheitert. Liefert True, wenn
        dabei die (einzige) Ausfall-Mail verschickt wurde -- main.py setzt
        dann Exit != 0, sonst Exit 0 (kein cron-Mail-Rauschen)."""
        now = now or datetime.now(timezone.utc)
        since = self._budget.mark_zammad_outage_start(now)
        duration = now - since
        minutes = int(duration.total_seconds() // 60)

        if duration < self._notify_after:
            logger.warning(
                "%s: Zammad nicht erreichbar (%s), seit %d min -- Mail erst ab %d min "
                "Ausfall, Lauf wird im naechsten Durchgang nachgeholt",
                command,
                error,
                minutes,
                int(self._notify_after.total_seconds() // 60),
            )
            return False

        if not self._budget.claim_zammad_outage_notification(now):
            logger.warning(
                "%s: Zammad weiterhin nicht erreichbar (%s), seit %d min -- Mail wurde "
                "fuer diesen Ausfall bereits verschickt",
                command,
                error,
                minutes,
            )
            return False

        logger.error("%s: Zammad seit %d min nicht erreichbar (%s)", command, minutes, error)
        self._try_send_mail(
            "SMSammad: Zammad nicht erreichbar",
            f"Zammad ist seit {since.astimezone().strftime('%d.%m.%Y %H:%M')} "
            f"({minutes} min) nicht erreichbar.\n\n"
            f"Letzter Fehler ({command}): {error}\n\n"
            "Das ist typischerweise ein Neustart/Update des Zammad-Hosts oder ein "
            "Problem hinter dessen Reverse-Proxy. Es geht nichts verloren: Tickets mit "
            "Tag 'sms-out' und eingehende SMS auf dem Router werden nachgeholt, sobald "
            "Zammad wieder antwortet. Fuer diesen Ausfall kommt keine weitere Mail; "
            "bei Wiederherstellung folgt eine Entwarnung.",
        )
        return True

    def _try_send_mail(self, subject: str, body: str) -> None:
        try:
            send_mail(self._notification, subject=subject, body=body)
        except Exception:
            logger.exception("Benachrichtigung per Mail konnte nicht verschickt werden")
