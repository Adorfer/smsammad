"""Laden der config.ini in typisierte Dataclasses."""

import configparser
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

# Projekt-Wurzel = Verzeichnis, in dem auch run.py liegt (dieses File
# liegt fest unter <Wurzel>/src/smsammad/config.py -- ueber die eigene
# Dateiposition ermittelt statt ueber sys.argv[0]/cwd, damit es
# unabhaengig davon funktioniert, wie/von wo aus run.py aufgerufen wird).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Ohne --config wird config.ini direkt neben run.py gesucht (nicht in
# ~/.config/...) -- bewusste Entscheidung: Sicherheitsimplikationen
# (config.ini im Projektverzeichnis statt in einem dedizierten
# Config-Verzeichnis) sind bekannt und akzeptiert, chmod 600 (aktiv
# geprueft, siehe _check_permissions) muss hier ausreichen.
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.ini"
DEFAULT_STATS_DB_FILE = Path.home() / ".local" / "state" / "smsammad" / "stats.db"


class ConfigError(Exception):
    pass


@dataclass
class TeltonikaConfig:
    host: str
    username: str
    password: str
    default_country_code: str
    scheme: str = "https"
    verify_tls: bool = False
    # Praefix, der vor zu kurze/nicht parsebare Absendernummern (z.B.
    # Kurzrufnummern) gesetzt wird, um einen zweiten Parse-Versuch zu
    # ermoeglichen. Leer = kein Rekonstruktionsversuch.
    short_number_prefix: str = ""
    # Praefix fuer Absender, die auch mit short_number_prefix nicht als
    # gueltige Rufnummer erkannt werden (Kurzwahlen wie "22543",
    # alphanumerische Absender-IDs wie "CALLYA"). Wird beim Empfang vor den
    # rohen Absender-String gesetzt und landet so lesbar in Zammads
    # Telefonfeld (Zammad hat keine Probleme mit Buchstaben dort); beim
    # Versand wird der Praefix erkannt, entfernt und der Rest roh als
    # Sendeziel verwendet -- ermoeglicht auch Antworten an Kurzwahlen/
    # alphanumerische Absender. Leer = roher Absender-String ohne Praefix.
    unresolved_sender_prefix: str = "Kurzwahl:"


@dataclass
class ZammadConfig:
    url: str
    token: str
    group: str
    new_customer_group: str
    phone_field: str
    overflow_priority: int
    # Fallback-Feld, falls phone_field (Default "mobile") beim Kunden leer
    # ist: wird nur verwendet, wenn phonenumbers den dort hinterlegten Wert
    # als Mobilfunknummer erkennt (eine Festnetznummer kann keine SMS
    # empfangen). Sinnvoll, wenn Agenten Mobilfunknummern gelegentlich im
    # normalen Telefonfeld statt im Mobilfunk-Feld erfassen.
    phone_field_fallback: str = "phone"
    # Fuer ein neues Ticket eines BEKANNTEN Kunden ohne offenes Ticket:
    # False (Default) -- immer die feste Gruppe `group`. True -- Gruppe
    # des zuletzt kontaktierten Tickets dieses Kunden wiederverwenden
    # (egal ob offen/geschlossen/zusammengefuehrt), Fallback auf `group`
    # falls der Kunde noch gar kein Ticket hatte. Sinnvoll fuer
    # kundenzentrisch arbeitende Teams ("one face to the customer"), bei
    # denen granulare Einordnung ueber Tags statt Queues laeuft.
    group_from_last_ticket: bool = False


@dataclass
class TicketToSmsConfig:
    max_sms_parts: int
    max_sms_per_hour: int
    max_sms_per_24h: int
    stats_db_file: Path
    budget_notify_cooldown_minutes: int
    # "reject" (Default): bei > max_sms_parts wird NICHT gesendet, nur eine
    # Fehler-Notiz. "truncate": die ersten max_sms_parts Teile werden
    # trotzdem gesendet, Notiz weist auf die Kuerzung hin und nennt den
    # tatsaechlich gesendeten (gekuerzten) Text.
    on_overflow: str = "reject"


@dataclass
class BalanceConfig:
    # Prepaid-Guthaben-Ueberwachung der SIM-Karte im Router. Optional --
    # ohne [balance]-Sektion in der config.ini bleibt dieses Feature
    # komplett inaktiv (Config.balance bleibt None).
    warn_threshold_eur: float
    alarm_threshold_eur: float
    # "ussd" (Default): synchrone Abfrage per RutOS-REST-API (/api/...),
    # sofortige Antwort, i.d.R. kostenlos. "sms": Abfrage-SMS + asynchrone
    # Auswertung der Antwort-SMS durch sms_to_ticket.py.
    method: str = "ussd"
    query_interval_hours: int = 24
    # Zammad state_id fuer "closed" -- Default passt zu einem Standard-
    # Zammad (per API gegen die echte Instanz verifiziert), kann bei
    # abweichender Installation ueberschrieben werden.
    closed_state_id: int = 4
    # -- method = "ussd" --
    ussd_code: str = "*100#"
    # Eigener, fuer die RutOS-REST-API berechtigter Account (NICHT der
    # cgi-bin-SMS-Account -- live verifiziert: der hat auf /api/... keinen
    # Zugriff, 401/403).
    api_username: str = ""
    api_password: str = ""
    # Modem-ID wie in der Router-WebUI-URL/API sichtbar (i.d.R. "1-1").
    modem_id: str = "1-1"
    # -- method = "sms" --
    query_number: str = ""  # Kurzwahl fuer die Guthaben-Abfrage, z.B. "111"
    query_text: str = ""  # SMS-Text der Abfrage, z.B. "Guthaben"
    reply_sender: str = ""  # erwarteter Absender der Antwort-SMS, z.B. "80808"
    # -- Betrags-Erkennung (Provider-Wortlaut-abhaengig, siehe README) --
    # Regex mit GENAU EINER Erfassungsgruppe fuer den Betrag (deutsches
    # Komma als Dezimaltrenner, z.B. "25,77"); re.IGNORECASE wird immer
    # angewendet. In der config.ini aenderbar, ohne den Code anzufassen,
    # falls der Provider den Antworttext aendert.
    ussd_balance_regex: str = r"Aktuelles Guthaben:\s*(\d+(?:,\d+)?)\s*EUR"
    sms_balance_regex: str = r"Guthaben\s+betr[äa]gt\s+(\d+(?:,\d+)?)\s*Euro"


@dataclass
class NotificationConfig:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    recipient: str
    enabled: bool = True


@dataclass
class Config:
    teltonika: TeltonikaConfig
    zammad: ZammadConfig
    ticket_to_sms: TicketToSmsConfig
    notification: NotificationConfig | None
    balance: BalanceConfig | None = None


_NO_FALLBACK = object()


def _unquote(value: str, section: str, option: str) -> str:
    """Entfernt das umschliessende Anfuehrungszeichenpaar ("..." oder
    '...') und einen optionalen '# ...'-Inline-Kommentar dahinter --
    quote-bewusst: ein '#' INNERHALB der Anfuehrungszeichen ist niemals
    ein Kommentaranfang. Das ist bewusst nicht ueber configparsers eigenes
    inline_comment_prefixes geloest, weil das Anfuehrungszeichen nicht
    versteht und z.B. '"pass # wort"' faelschlich mitten im Wert
    abgeschnitten haette. String-Werte MUESSEN gequotet sein (kein
    Fallback auf unquotierte Werte mehr), damit genau das eindeutig
    entscheidbar ist."""
    if len(value) >= 2 and value[0] in "\"'":
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
    raise ConfigError(
        f"[{section}] {option} muss in Anfuehrungszeichen stehen (\"...\" oder '...'), "
        f"ist aber {value!r}"
    )


def _strip_inline_comment(value: str) -> str:
    """Fuer unquotierte Zahlen-/Wahrheitswerte: entfernt einen
    '# ...'-Kommentar am Ende. Sicher ohne Quote-Ruecksicht moeglich, weil
    Zahlen/true/false laut Konvention nie ein '#' enthalten (anders als
    Passwoerter/Tokens, die deshalb IMMER gequotet sein muessen, siehe
    _unquote)."""
    match = re.search(r"\s#", value)
    if match:
        return value[: match.start()].rstrip()
    return value.strip()


def _get(
    parser: configparser.ConfigParser, section: str, option: str, fallback: object = _NO_FALLBACK
) -> str:
    if fallback is not _NO_FALLBACK and not parser.has_option(section, option):
        return fallback
    return _unquote(parser.get(section, option), section, option)


def _get_int(
    parser: configparser.ConfigParser, section: str, option: str, fallback: object = _NO_FALLBACK
) -> int:
    if fallback is not _NO_FALLBACK and not parser.has_option(section, option):
        return fallback
    return int(_strip_inline_comment(parser.get(section, option)))


def _get_float(
    parser: configparser.ConfigParser, section: str, option: str, fallback: object = _NO_FALLBACK
) -> float:
    if fallback is not _NO_FALLBACK and not parser.has_option(section, option):
        return fallback
    return float(_strip_inline_comment(parser.get(section, option)))


def _get_bool(
    parser: configparser.ConfigParser, section: str, option: str, fallback: object = _NO_FALLBACK
) -> bool:
    if fallback is not _NO_FALLBACK and not parser.has_option(section, option):
        return fallback
    cleaned = _strip_inline_comment(parser.get(section, option)).lower()
    if cleaned not in configparser.ConfigParser.BOOLEAN_STATES:
        raise ConfigError(f"[{section}] {option} ist kein gueltiger Wahrheitswert: {cleaned!r}")
    return configparser.ConfigParser.BOOLEAN_STATES[cleaned]


def _validate_balance_regex(pattern: str, path: Path, field_name: str) -> None:
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ConfigError(
            f"Fehlerhafte Config {path}: balance.{field_name} ist kein gueltiger regulaerer "
            f"Ausdruck: {exc}"
        ) from exc
    if compiled.groups < 1:
        raise ConfigError(
            f"Fehlerhafte Config {path}: balance.{field_name} braucht mindestens eine "
            f"Erfassungsgruppe '(...)' fuer den Betrag"
        )


def _check_permissions(path: Path) -> None:
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            f"{path} ist fuer Gruppe/Andere lesbar (Modus {oct(mode)}) — "
            f"enthaelt Zugangsdaten, bitte 'chmod 600 {path}' ausfuehren."
        )


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"Config-Datei nicht gefunden: {path}")
    _check_permissions(path)

    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")

    try:
        teltonika = TeltonikaConfig(
            host=_get(parser, "teltonika", "host"),
            username=_get(parser, "teltonika", "username"),
            password=_get(parser, "teltonika", "password"),
            default_country_code=_get(parser, "teltonika", "default_country_code"),
            scheme=_get(parser, "teltonika", "scheme", fallback="https"),
            verify_tls=_get_bool(parser, "teltonika", "verify_tls", fallback=False),
            short_number_prefix=_get(parser, "teltonika", "short_number_prefix", fallback=""),
            unresolved_sender_prefix=_get(
                parser, "teltonika", "unresolved_sender_prefix", fallback="Kurzwahl:"
            ),
        )
        zammad = ZammadConfig(
            url=_get(parser, "zammad", "url"),
            token=_get(parser, "zammad", "token"),
            group=_get(parser, "zammad", "group", fallback="Users"),
            new_customer_group=_get(parser, "zammad", "new_customer_group", fallback="Users"),
            phone_field=_get(parser, "zammad", "phone_field", fallback="mobile"),
            overflow_priority=_get_int(parser, "zammad", "overflow_priority", fallback=3),
            group_from_last_ticket=_get_bool(
                parser, "zammad", "group_from_last_ticket", fallback=False
            ),
        )
        stats_db_file_raw = _get(
            parser, "ticket_to_sms", "stats_db_file", fallback=str(DEFAULT_STATS_DB_FILE)
        )
        on_overflow = _get(parser, "ticket_to_sms", "on_overflow", fallback="reject")
        ticket_to_sms = TicketToSmsConfig(
            max_sms_parts=_get_int(parser, "ticket_to_sms", "max_sms_parts", fallback=3),
            max_sms_per_hour=_get_int(parser, "ticket_to_sms", "max_sms_per_hour", fallback=20),
            max_sms_per_24h=_get_int(parser, "ticket_to_sms", "max_sms_per_24h", fallback=100),
            stats_db_file=Path(stats_db_file_raw).expanduser(),
            budget_notify_cooldown_minutes=_get_int(
                parser, "ticket_to_sms", "budget_notify_cooldown_minutes", fallback=60
            ),
            on_overflow=on_overflow,
        )
    except (configparser.NoSectionError, configparser.NoOptionError) as exc:
        raise ConfigError(f"Fehlerhafte Config {path}: {exc}") from exc

    if ticket_to_sms.on_overflow not in ("reject", "truncate"):
        raise ConfigError(
            f"Fehlerhafte Config {path}: ticket_to_sms.on_overflow muss 'reject' oder "
            f"'truncate' sein, nicht {ticket_to_sms.on_overflow!r}"
        )

    notification = None
    if parser.has_section("notification"):
        notification = NotificationConfig(
            smtp_host=_get(parser, "notification", "smtp_host"),
            smtp_port=_get_int(parser, "notification", "smtp_port", fallback=587),
            smtp_user=_get(parser, "notification", "smtp_user"),
            smtp_password=_get(parser, "notification", "smtp_password"),
            recipient=_get(parser, "notification", "recipient"),
            enabled=_get_bool(parser, "notification", "enabled", fallback=True),
        )

    balance = None
    if parser.has_section("balance"):
        balance_method = _get(parser, "balance", "method", fallback="ussd")
        if balance_method not in ("ussd", "sms"):
            raise ConfigError(
                f"Fehlerhafte Config {path}: balance.method muss 'ussd' oder 'sms' sein, "
                f"nicht {balance_method!r}"
            )
        balance = BalanceConfig(
            warn_threshold_eur=_get_float(parser, "balance", "warn_threshold_eur"),
            alarm_threshold_eur=_get_float(parser, "balance", "alarm_threshold_eur"),
            method=balance_method,
            query_interval_hours=_get_int(parser, "balance", "query_interval_hours", fallback=24),
            closed_state_id=_get_int(parser, "balance", "closed_state_id", fallback=4),
            ussd_code=_get(parser, "balance", "ussd_code", fallback="*100#"),
            api_username=_get(parser, "balance", "api_username", fallback=""),
            api_password=_get(parser, "balance", "api_password", fallback=""),
            modem_id=_get(parser, "balance", "modem_id", fallback="1-1"),
            query_number=_get(parser, "balance", "query_number", fallback=""),
            query_text=_get(parser, "balance", "query_text", fallback=""),
            reply_sender=_get(parser, "balance", "reply_sender", fallback=""),
            ussd_balance_regex=_get(
                parser, "balance", "ussd_balance_regex",
                fallback=r"Aktuelles Guthaben:\s*(\d+(?:,\d+)?)\s*EUR",
            ),
            sms_balance_regex=_get(
                parser, "balance", "sms_balance_regex",
                fallback=r"Guthaben\s+betr[äa]gt\s+(\d+(?:,\d+)?)\s*Euro",
            ),
        )
        _validate_balance_regex(balance.ussd_balance_regex, path, "ussd_balance_regex")
        _validate_balance_regex(balance.sms_balance_regex, path, "sms_balance_regex")
        if balance_method == "ussd" and not (balance.api_username and balance.api_password):
            raise ConfigError(
                f"Fehlerhafte Config {path}: balance.method='ussd' braucht api_username "
                f"und api_password"
            )
        if balance_method == "sms" and not (balance.query_number and balance.reply_sender):
            raise ConfigError(
                f"Fehlerhafte Config {path}: balance.method='sms' braucht query_number "
                f"und reply_sender"
            )

    return Config(
        teltonika=teltonika,
        zammad=zammad,
        ticket_to_sms=ticket_to_sms,
        notification=notification,
        balance=balance,
    )
