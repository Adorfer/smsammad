"""Client fuer die moderne RutOS-REST-API (/api/...) -- ausschliesslich fuer
USSD-Abfragen genutzt. Bewusst getrennt vom cgi-bin-basierten
TeltonikaClient (teltonika.py): anderes Protokoll (Bearer-Token per
/api/login statt Query-Param-Auth) UND ein eigener, dafuer berechtigter
Account -- live verifiziert, dass der cgi-bin-SMS-Account auf /api/...
KEINEN Zugriff hat (401 beim Login bzw. 403 bei der Aktion selbst, je
nachdem ob nur der Login- oder auch der Aktions-Scope fehlt).

Request/Response-Format live gegen ein echtes RUT240 (RutOS
RUT2_R_00.07.06.21) ermittelt, da Teltonikas Entwickler-Doku
(developers.teltonika-networks.com) eine JS-Single-Page-App ohne
statisch abrufbaren Inhalt ist:

    POST /api/login
    {"username": "...", "password": "..."}
    -> {"success": true, "data": {"username": "...", "token": "...", "expires": 299}}

    POST /api/modems/<modem_id>/actions/send_ussd
    Authorization: Bearer <token>
    {"data": {"ussd": "*100#"}}
    -> {"success": true, "data": {"response": "<Zeitstempel> 1,<USSD-Menuetext>,15\\n"}}

Token ist mit `expires: 299` sehr kurzlebig (~5 Minuten) -- bei taeglich
max. 1 Aufruf wird deshalb bewusst NICHT gecacht, sondern bei jedem
`send_ussd()` frisch eingeloggt.
"""

import logging

import requests
import urllib3

from .config import BalanceConfig

logger = logging.getLogger("smsammad")


class TeltonikaApiError(Exception):
    pass


class TeltonikaApiClient:
    def __init__(
        self,
        host: str,
        config: BalanceConfig,
        verify_tls: bool = False,
        timeout: float = 20.0,
    ) -> None:
        self._host = host
        self._config = config
        self._verify_tls = verify_tls
        self._timeout = timeout
        self._base_url = f"https://{host}/api"
        if not verify_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _login(self) -> str:
        # Wie beim cgi-bin-Client bewusst ohne Exception-Chaining: die
        # Original-Exception koennte URL/Details enthalten, die nicht in
        # Log/Fehlermail landen sollen.
        try:
            response = requests.post(
                f"{self._base_url}/login",
                json={"username": self._config.api_username, "password": self._config.api_password},
                timeout=self._timeout,
                verify=self._verify_tls,
            )
        except requests.RequestException as exc:
            raise TeltonikaApiError(f"Login fehlgeschlagen: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise TeltonikaApiError(f"Login fehlgeschlagen: HTTP {response.status_code}")
        try:
            token = response.json()["data"]["token"]
        except (ValueError, KeyError) as exc:
            raise TeltonikaApiError(f"Login-Antwort ohne Token: {response.text!r}") from exc
        return token

    def send_ussd(self, code: str) -> str:
        """Liefert den rohen USSD-Antworttext (Balance-Extraktion daraus
        siehe balance_check._parse_ussd_balance)."""
        token = self._login()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            response = requests.post(
                f"{self._base_url}/modems/{self._config.modem_id}/actions/send_ussd",
                headers=headers,
                json={"data": {"ussd": code}},
                timeout=self._timeout,
                verify=self._verify_tls,
            )
        except requests.RequestException as exc:
            raise TeltonikaApiError(f"USSD-Versand fehlgeschlagen: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise TeltonikaApiError(f"USSD-Versand fehlgeschlagen: HTTP {response.status_code}")
        try:
            text = response.json()["data"]["response"]
        except (ValueError, KeyError) as exc:
            raise TeltonikaApiError(f"USSD-Antwort unerwartet: {response.text!r}") from exc
        if not text:
            raise TeltonikaApiError("USSD-Antwort leer")
        return text
