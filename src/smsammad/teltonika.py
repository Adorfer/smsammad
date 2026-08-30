"""Client fuer die Teltonika-RUT-cgi-bin-SMS-API.

Doku: https://wiki.teltonika-networks.com/view/RUT240_SMS_Gateway
Die Doku spezifiziert nur die Request-Parameter, keine Antwortstruktur.
Gegen ein echtes RUT240 verifiziert: sms_list liefert KEIN JSON, sondern
Klartext-Bloecke im Format

    Index: 4
    Date: Sat Oct 26 19:04:11 2024
    Sender: +491775280961
    Text: irgendein Text
    Status: read
    ------------------------------

durch Trennzeilen aus Bindestrichen getrennt. `read`/`total` sind gegen das
echte Geraet noch nicht verifiziert (werden von unseren Ablaeufen aktuell
nicht benutzt) und folgen hier testweise demselben Format.
"""

import logging
import re
from dataclasses import dataclass

import requests
import urllib3

from .config import TeltonikaConfig

logger = logging.getLogger("smsammad")

_FIELD_RE = re.compile(r"^(Index|Date|Sender|Text|Status):[ \t]?(.*)$")
_SEPARATOR_RE = re.compile(r"\n-{3,}\n?")


class TeltonikaError(Exception):
    pass


@dataclass
class SmsMessage:
    index: int
    sender: str
    text: str
    timestamp: str | None = None


def _parse_blocks(text: str) -> list[dict[str, str]]:
    blocks = _SEPARATOR_RE.split(text.strip("\n"))
    records = []
    for block in blocks:
        block = block.strip("\n")
        if not block:
            continue
        fields: dict[str, str] = {}
        current_key: str | None = None
        for line in block.splitlines():
            match = _FIELD_RE.match(line)
            if match:
                current_key = match.group(1)
                fields[current_key] = match.group(2)
            elif current_key:
                fields[current_key] += "\n" + line
        if fields:
            records.append(fields)
    return records


def _record_to_message(fields: dict[str, str]) -> SmsMessage:
    return SmsMessage(
        index=int(fields["Index"]),
        sender=fields["Sender"].strip(),
        text=fields.get("Text", "").strip(),
        timestamp=fields.get("Date"),
    )


class TeltonikaClient:
    def __init__(self, config: TeltonikaConfig, timeout: float = 15.0) -> None:
        self._config = config
        self._timeout = timeout
        self._base_url = f"{config.scheme}://{config.host}/cgi-bin"
        if config.scheme == "https" and not config.verify_tls:
            # Kein WARNING-Log dafuer -- wer verify_tls=false in der
            # config.ini setzt, hat das bewusst getan (uebliche
            # Notwendigkeit bei selbstsigniertem Teltonika-Zertifikat im
            # LAN), das muss nicht bei jedem Lauf ins Log.
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _auth_params(self) -> dict[str, str]:
        return {"username": self._config.username, "password": self._config.password}

    def _get(self, endpoint: str, **params: str) -> requests.Response:
        # Absichtlich ohne "raise ... from exc": die Original-Exception (und
        # jede Kette bis zu urllib3) enthaelt die volle URL inkl.
        # username/password als Query-Parameter (von der Teltonika-API so
        # vorgegeben) und darf nicht in Log/Traceback/Fehlermail landen.
        url = f"{self._base_url}/{endpoint}"
        try:
            response = requests.get(
                url,
                params={**self._auth_params(), **params},
                timeout=self._timeout,
                verify=self._config.verify_tls,
            )
        except requests.RequestException as exc:
            raise TeltonikaError(
                f"Anfrage an {endpoint} fehlgeschlagen: {type(exc).__name__}"
            ) from None
        if response.status_code != 200:
            raise TeltonikaError(
                f"{endpoint} lieferte HTTP {response.status_code}: {response.text!r}"
            )
        return response

    def _post(self, endpoint: str, timeout: float, **params: str) -> requests.Response:
        # POST statt GET, weil laengerer `text` als Query-String live HTTP
        # 413 "Request Entity Too Large" verursacht hat (URL-Laengenlimit
        # des Router-eigenen Webservers, NICHT der SMS/API selbst -- per
        # POST-Body verifiziert bis mehrere tausend Zeichen moeglich).
        # Gleiche Begruendung wie bei _get fuer "ohne from exc".
        url = f"{self._base_url}/{endpoint}"
        try:
            response = requests.post(
                url,
                data={**self._auth_params(), **params},
                timeout=timeout,
                verify=self._config.verify_tls,
            )
        except requests.RequestException as exc:
            raise TeltonikaError(
                f"Anfrage an {endpoint} fehlgeschlagen: {type(exc).__name__}"
            ) from None
        if response.status_code != 200:
            raise TeltonikaError(
                f"{endpoint} lieferte HTTP {response.status_code}: {response.text!r}"
            )
        return response

    def send(self, number: str, text: str) -> None:
        """`number` muss bereits im Format 00<Laendercode><Nummer> vorliegen.

        Laengere Texte (Multipart-Modus, siehe sms_encoding.py) koennen den
        Router laut Live-Tests ungewoehnlich lange verarbeiten (beobachtet:
        bis über 2 Minuten bei sehr langen UCS-2-Texten) -- daher ein
        deutlich groesseres Timeout als bei den uebrigen, immer schnellen
        Endpunkten (sms_list/sms_read/sms_delete/sms_total).
        """
        send_timeout = max(self._timeout, 60.0)
        self._post("sms_send", send_timeout, number=number, text=text)

    def list_messages(self) -> list[SmsMessage]:
        response = self._get("sms_list")
        text = response.text
        if not text.strip():
            return []
        try:
            records = _parse_blocks(text)
        except (KeyError, ValueError) as exc:
            raise TeltonikaError(f"sms_list: unerwartete Antwort: {text!r}") from exc
        if not records:
            # Nicht-leere Antwort, aber keine geparsten SMS-Bloecke -- z.B.
            # "Disabled" bei deaktiviertem SMS-Gateway. Nicht mit "0 SMS"
            # verwechseln, sonst faellt eine Fehlkonfiguration nie auf.
            raise TeltonikaError(f"sms_list: unerwartete Antwort: {text!r}")
        return [_record_to_message(fields) for fields in records]

    def read(self, index: int) -> SmsMessage:
        response = self._get("sms_read", number=str(index))
        try:
            records = _parse_blocks(response.text)
            return _record_to_message(records[0])
        except (KeyError, ValueError, IndexError) as exc:
            raise TeltonikaError(
                f"sms_read({index}): unerwartete Antwort: {response.text!r}"
            ) from exc

    def delete(self, index: int) -> None:
        self._get("sms_delete", number=str(index))

    def total(self) -> int:
        response = self._get("sms_total")
        match = re.search(r"\d+", response.text)
        if not match:
            raise TeltonikaError(f"sms_total: unerwartete Antwort: {response.text!r}")
        return int(match.group())
