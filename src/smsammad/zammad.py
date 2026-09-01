"""Client fuer die Zammad-REST-API (nur die fuer diese Kopplung noetigen Teile)."""

import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from .config import ZammadConfig
from .phone import PhoneNumberError, to_e164, to_human_readable


class ZammadError(Exception):
    pass


def _normalize_for_dedup(value: str) -> str:
    """Entfernt alle nicht-alphanumerischen Zeichen und normalisiert auf
    Kleinschreibung -- letzte, grosszuegigste Vergleichsstufe in
    _phone_matches(). Faengt z.B. "Kurzwahl-224466" vs. "Kurzwahl:224466"
    ab (unterschiedliches Trennzeichen nach einer Aenderung an
    unresolved_sender_prefix in der config.ini): beides sind keine echten
    Rufnummern, to_e164() kann sie nie normalisieren, waeren also sonst
    dauerhaft als "verschiedene" Kunden erkannt, obwohl dieselbe Kurzwahl
    gemeint ist."""
    return re.sub(r"[^0-9A-Za-z]", "", value).lower()


_PHONE_PUNCTUATION_RE = re.compile(r"[ \-.]")


def _phone_matches(raw_value: str, target_e164: str, default_region: str) -> bool:
    """Vergleicht eine roh in Zammad gespeicherte Rufnummer (beliebig
    formatiert, z.B. "+49 172 1234567") mit einer bereits normalisierten
    Ziel-Nummer. Exakter Treffer zuerst (deckt unsere eigenen "Kurzwahl:..."-
    Werte ab, die immer gleich formatiert sind), dann Vergleich ueber
    phonenumbers-Normalisierung -- dafuer bewusst zuerst Leerzeichen/-/.
    entfernt statt phonenumbers' eigene Toleranz dafuer vorauszusetzen,
    zuletzt satzzeichen-unabhaengiger Vergleich (siehe _normalize_for_dedup)
    fuer Pseudo-Identifikatoren."""
    if raw_value == target_e164:
        return True
    try:
        if to_e164(_PHONE_PUNCTUATION_RE.sub("", raw_value), default_region) == target_e164:
            return True
    except PhoneNumberError:
        pass
    normalized_target = _normalize_for_dedup(target_e164)
    return bool(normalized_target) and _normalize_for_dedup(raw_value) == normalized_target


def _search_token(value: str) -> str:
    """Letztes alphanumerisches Zammad-Suchtoken eines Werts (Zammads
    Volltextsuche tokenisiert live verifiziert nur an Leerzeichen -- NICHT
    an '-' oder '.', die bleiben Teil desselben Tokens -- und matcht nur
    ganze Tokens). Funktioniert sowohl fuer Rufnummern
    ("+49 172 1234567" -> "1234567") als auch fuer "Kurzwahl:CALLYA" ->
    "CALLYA"."""
    tokens = re.findall(r"[0-9A-Za-z]+", value)
    if not tokens:
        return value
    last = tokens[-1]
    return last[-7:] if len(last) >= 7 else last


@dataclass
class Ticket:
    id: int
    number: str
    state: str
    last_contact_at: str | None


class ZammadClient:
    def __init__(self, config: ZammadConfig, timeout: float = 15.0) -> None:
        self._config = config
        self._timeout = timeout
        self._base_url = config.url.rstrip("/") + "/api/v1"
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Token token={config.token}"
        self._group_name_cache: dict[int, str] = {}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            response = self._session.request(method, url, timeout=self._timeout, **kwargs)
        except requests.RequestException as exc:
            raise ZammadError(f"{method} {path} fehlgeschlagen: {exc}") from exc
        if not response.ok:
            raise ZammadError(f"{method} {path} -> HTTP {response.status_code}: {response.text}")
        if response.text:
            return response.json()
        return None

    def find_customer_by_phone(self, e164_number: str, default_region: str) -> int | None:
        """Rein lesende Suche, ohne Anlage -- fuer --dry-run.

        Zammads Volltextsuche tokenisiert Werte an Leerzeichen (NICHT an
        '-' oder '.', live verifiziert) und matcht nur ganze Tokens --
        eine durchgehende Ziffernfolge wie "491721234567" trifft daher
        NICHT auf einen als "+49 172 1234567" gespeicherten Wert (dort ist
        "1234567" das relevante Token). Deshalb mit beidseitigem Wildcard
        auf das letzte Token suchen (breiter, ggf. auch falsche Treffer),
        und die Kandidaten anschliessend clientseitig eindeutig
        verifizieren.

        Deshalb formatiert to_human_readable() (genutzt beim Neuanlegen in
        find_or_create_customer_by_phone) mit '-' statt Leerzeichen als
        Gruppentrenner -- ein mit Leerzeichen gruppierter, selbst
        angelegter Kunde waere sonst bei der naechsten SMS ueber diese
        Suche nicht mehr wiederauffindbar, sobald die gesuchten letzten
        Ziffern eine Gruppengrenze ueberqueren (live an einem
        Test-Kunden verifiziert).

        Mehrere Kunden mit derselben Nummer (z.B. Familie am selben
        Handy): das Ticket geht an den mit dem juengsten Kundenkontakt
        (aktuellstes Ticket ueberhaupt, offen oder nicht) -- siehe
        _pick_most_recently_contacted().
        """
        field = self._config.phone_field
        token = _search_token(e164_number)
        results = self._request("GET", "users/search", params={"query": f"*{token}*"}) or []
        matches = [
            candidate["id"]
            for candidate in results
            if candidate.get(field) and _phone_matches(candidate[field], e164_number, default_region)
        ]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        return self._pick_most_recently_contacted(matches)

    def _pick_most_recently_contacted(self, customer_ids: list[int]) -> int:
        """Bei mehreren gleichzeitig passenden Kunden (gleiche Rufnummer):
        pro Kandidat das insgesamt letzte Ticket (find_last_ticket_for_customer)
        holen und den mit dem juengsten last_contact_at nehmen. Kandidaten
        ganz ohne Ticket zaehlen als "nie kontaktiert" ("") und landen ans
        Ende, bleiben aber waehlbar, falls KEIN Kandidat je ein Ticket
        hatte. Bei Gleichstand (inkl. "alle ohne Ticket") gewinnt die
        urspruengliche Reihenfolge aus Zammads Suchergebnis -- max() ist
        stabil und liefert bei gleichem Schluessel den ersten Treffer."""

        def _last_contact_at(customer_id: int) -> str:
            ticket = self.find_last_ticket_for_customer(customer_id)
            return (ticket.last_contact_at or "") if ticket else ""

        return max(customer_ids, key=_last_contact_at)

    def find_or_create_customer_by_phone(
        self, e164_number: str, default_region: str
    ) -> tuple[int, bool]:
        """Liefert (customer_id, war_neu_angelegt)."""
        existing = self.find_customer_by_phone(e164_number, default_region)
        if existing is not None:
            return existing, False

        field = self._config.phone_field
        # Fuer echte Rufnummern menschenlesbar formatiert (z.B.
        # "0172-1234-4567"); "Kurzwahl:..."-Werte sind bereits lesbar und
        # bleiben unveraendert (keine gueltige Rufnummer, to_human_readable
        # wuerde PhoneNumberError werfen).
        try:
            display_number = to_human_readable(e164_number, default_region)
        except PhoneNumberError:
            display_number = e164_number
        try:
            created = self._request(
                "POST",
                "users",
                json={
                    "login": e164_number,
                    "firstname": "SMS",
                    "lastname": display_number,
                    field: display_number,
                    "role_ids": [],
                },
            )
            return created["id"], True
        except ZammadError as exc:
            if "Login has already been taken" not in str(exc):
                raise
            # Zammad kennt den Login bereits, unsere Suche hat ihn aber nicht
            # gefunden. Zwei bekannte Gruende: (1) Race -- derselbe Kunde
            # wurde gerade erst angelegt (z.B. durch eine vorherige SMS im
            # selben Lauf) und Zammads Suchindex (near-realtime, nicht
            # sofort konsistent) hat das noch nicht erfasst -- deshalb hier
            # mehrere Versuche mit kurzer Wartezeit. (2) Der Login ist ein
            # Pseudo-Identifikator (z.B. "Kurzwahl:..."), der nicht (mehr)
            # exakt so gesucht wird wie gespeichert -- dafuer sorgt die
            # zusaetzliche satzzeichen-unabhaengige Vergleichsstufe in
            # _phone_matches().
            existing = self._find_customer_with_retry(e164_number, default_region)
            if existing is not None:
                return existing, False
            raise

    def _find_customer_with_retry(
        self, e164_number: str, default_region: str, attempts: int = 3, delay_seconds: float = 2.0
    ) -> int | None:
        for attempt in range(attempts):
            if attempt:
                time.sleep(delay_seconds)
            existing = self.find_customer_by_phone(e164_number, default_region)
            if existing is not None:
                return existing
        return None

    def find_open_ticket_for_customer(self, customer_id: int) -> Ticket | None:
        results = (
            self._request(
                "GET",
                "tickets/search",
                params={"query": f"customer_id:{customer_id}", "expand": "true"},
            )
            or []
        )
        open_tickets = [t for t in results if t.get("state") not in ("closed", "merged")]
        if not open_tickets:
            return None
        newest = max(open_tickets, key=lambda t: t.get("last_contact_at") or "")
        return Ticket(
            id=newest["id"],
            number=newest["number"],
            state=newest["state"],
            last_contact_at=newest.get("last_contact_at"),
        )

    def find_last_ticket_for_customer(self, customer_id: int) -> Ticket | None:
        """Wie find_open_ticket_for_customer, aber OHNE den Offen-Filter --
        liefert das zuletzt kontaktierte Ticket ueberhaupt (auch
        geschlossen/zusammengefuehrt). Fuer `group_from_last_ticket`:
        kundenzentrisch arbeitende Teams wollen ein neues Ticket eines
        bekannten Kunden in dessen gewohnter Queue landen sehen, nicht in
        einer festen Default-Gruppe."""
        results = (
            self._request(
                "GET",
                "tickets/search",
                params={"query": f"customer_id:{customer_id}", "expand": "true"},
            )
            or []
        )
        if not results:
            return None
        newest = max(results, key=lambda t: t.get("last_contact_at") or "")
        return Ticket(
            id=newest["id"],
            number=newest["number"],
            state=newest["state"],
            last_contact_at=newest.get("last_contact_at"),
        )

    def create_ticket(self, customer_id: int, group: str, subject: str, body: str) -> int:
        created = self._request(
            "POST",
            "tickets",
            json={
                "title": subject,
                "group": group,
                "customer_id": customer_id,
                "article": {
                    "subject": subject,
                    "body": body,
                    "type": "phone",
                    "sender": "Customer",
                    "internal": False,
                },
            },
        )
        return created["id"]

    def add_article(
        self,
        ticket_id: int,
        body: str,
        internal: bool = False,
        article_type: str = "phone",
        sender: str = "Customer",
    ) -> None:
        # article_type/sender wichtig: eigene System-/Audit-Vermerke (z.B.
        # "SMS-Versand" nach dem Senden) duerfen NICHT als type="phone"
        # laufen, sonst koennte ein Zammad-Trigger, der auf neue oeffentliche
        # Anruf-Artikel reagiert, versehentlich auf unsere eigene Notiz
        # erneut feuern (Endlosschleife: senden -> Notiz -> Trigger ->
        # sms-out erneut -> senden ...).
        self._request(
            "POST",
            "ticket_articles",
            json={
                "ticket_id": ticket_id,
                "body": body,
                "type": article_type,
                "sender": sender,
                "internal": internal,
                "content_type": "text/plain",
            },
        )

    def search_tickets_by_tag(self, tag: str) -> list[int]:
        results = (
            self._request("GET", "tickets/search", params={"query": f"tags:{tag}"}) or []
        )
        return [t["id"] for t in results]

    def get_tags(self, ticket_id: int) -> list[str]:
        result = self._request("GET", "tags", params={"object": "Ticket", "o_id": ticket_id})
        return (result or {}).get("tags", [])

    def add_tag(self, ticket_id: int, tag: str) -> None:
        self._request(
            "POST", "tags/add", json={"object": "Ticket", "o_id": ticket_id, "item": tag}
        )

    def remove_tag(self, ticket_id: int, tag: str) -> None:
        # Anders als tags/add erwartet tags/remove laut Zammad-API DELETE,
        # nicht POST.
        self._request(
            "DELETE", "tags/remove", json={"object": "Ticket", "o_id": ticket_id, "item": tag}
        )

    def set_priority(self, ticket_id: int, priority_id: int) -> None:
        self._request("PUT", f"tickets/{ticket_id}", json={"priority_id": priority_id})

    def set_state(self, ticket_id: int, state_id: int) -> None:
        self._request("PUT", f"tickets/{ticket_id}", json={"state_id": state_id})

    def update_ticket(self, ticket_id: int, **fields: Any) -> None:
        """Wie set_priority/set_state, aber mehrere Felder in EINEM PUT --
        Zammad validiert bei jedem Ticket-Update das GESAMTE Modell (Rails-
        Verhalten), nicht nur die uebergebenen Felder. Live beobachtet: ein
        Ticket mit leerem `title` (in der Zammad-UI als "-" angezeigt)
        ließ selbst ein reines priority_id-Update mit HTTP 422 "Missing
        required value for field 'title'" scheitern -- daher muss ein
        Titel-Fix im selben Request wie die eigentliche Aenderung erfolgen.
        """
        self._request("PUT", f"tickets/{ticket_id}", json=fields)

    def set_subject(self, ticket_id: int, subject: str) -> None:
        self._request("PUT", f"tickets/{ticket_id}", json={"title": subject})

    def get_ticket(self, ticket_id: int) -> dict[str, Any]:
        return self._request("GET", f"tickets/{ticket_id}")

    def get_user(self, user_id: int) -> dict[str, Any]:
        return self._request("GET", f"users/{user_id}")

    def get_ticket_articles(self, ticket_id: int) -> list[dict[str, Any]]:
        return self._request("GET", f"ticket_articles/by_ticket/{ticket_id}") or []

    def get_group_name(self, group_id: int) -> str:
        """Fuer Statistik-Zwecke (Gruppen-ID -> Klartextname). Gecacht pro
        Client-Instanz, da dieselbe Gruppe innerhalb eines Laufs oefter
        vorkommen kann."""
        if group_id not in self._group_name_cache:
            result = self._request("GET", f"groups/{group_id}") or {}
            self._group_name_cache[group_id] = result.get("name", str(group_id))
        return self._group_name_cache[group_id]

    def list_groups(self) -> list[dict[str, Any]]:
        """Fuer setup_check.py -- Gruppen-Name -> ID aufloesen (Config
        speichert Namen, die Berechtigungs-API braucht IDs)."""
        return self._request("GET", "groups") or []

    def get_my_user(self) -> dict[str, Any]:
        """Fuer setup_check.py -- eigene User-ID/Rollen/Gruppenzugriffe."""
        return self._request("GET", "users/me")

    def update_user(self, user_id: int, **fields: Any) -> None:
        """Fuer setup_check.py -- eigenen Gruppenzugriff erweitern
        (group_ids). Nur wirksam, wenn der API-User selbst Admin-Rechte
        hat, sonst liefert Zammad HTTP 403."""
        self._request("PUT", f"users/{user_id}", json=fields)

    def list_triggers(self) -> list[dict[str, Any]]:
        """Fuer setup_check.py -- prueft, ob ein Trigger existiert, der bei
        oeffentlichen Anruf-Artikeln vom Agenten den Tag 'sms-out' setzt.
        Erfordert eine Rolle mit Trigger-Verwaltungsrechten (z.B. Admin),
        sonst liefert Zammad HTTP 403."""
        return self._request("GET", "triggers") or []

    def list_ticket_states(self) -> list[dict[str, Any]]:
        """Fuer setup_check.py -- prueft open_state_id/closed_state_id
        gegen echte, existierende Zammad-States (inkl. state_type_id, um
        auch die SEMANTIK zu pruefen, nicht nur ob die ID existiert)."""
        return self._request("GET", "ticket_states") or []

    def list_ticket_priorities(self) -> list[dict[str, Any]]:
        """Fuer setup_check.py -- prueft overflow_priority gegen echte,
        existierende Zammad-Prioritaeten."""
        return self._request("GET", "ticket_priorities") or []

    def list_user_attributes(self) -> list[dict[str, Any]]:
        """Fuer setup_check.py -- prueft phone_field/phone_field_fallback
        gegen echte, existierende Attribute des User-Objekts (faengt einen
        Tippfehler im Feldnamen frueh ab, statt erst zur Laufzeit an
        unerwarteten leeren Werten zu scheitern)."""
        return self._request("GET", "object_manager_attributes") or []

    def list_my_tokens(self) -> list[dict[str, Any]]:
        """Fuer setup_check.py -- eigene API-Tokens samt ihrem eigenen,
        vom zugehoerigen User UNABHAENGIGEN Berechtigungs-Scope
        (`preferences.permission`). Live entdeckt: ein Zammad-API-Token
        traegt eine EIGENE, engere Rechte-Liste als der User, dem er
        gehoert -- ein Admin-User kann einen Token OHNE 'ticket.agent'
        haben, dann scheitert z.B. Ticket-Anlage mit HTTP 403 "Token
        authorization failed", obwohl der User selbst vollen Gruppen-
        zugriff hat. Liefert NIE das Token-Secret selbst (Zammad zeigt
        das nach der Erstellung nirgends mehr an)."""
        return (self._request("GET", "user_access_token") or {}).get("tokens", [])

    def create_trigger(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Fuer setup_check.py -- legt den 'sms-out'-Trigger an. Erfordert
        eine Rolle mit Trigger-Verwaltungsrechten, sonst HTTP 403."""
        return self._request("POST", "triggers", json=payload)
