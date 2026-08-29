"""POSIX-artiges Logging mit Prozess-ID, Level per --verbose steuerbar."""

import logging
import os


def setup_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format=f"%(asctime)s SMSammad[{os.getpid()}] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    # urllib3/requests loggen bei DEBUG die volle Request-URL inkl.
    # Query-Parametern -- die Teltonika-API verlangt username/password dort.
    # Auch bei --verbose nicht mitschleifen, sonst landen Zugangsdaten im Log.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    return logging.getLogger("smsammad")


def redact_content(text: str, keep: int = 5) -> str:
    """Fuer Logs: SMS-/Ticket-INHALTE (nicht Rufnummern) nach den ersten
    `keep` Zeichen mit '#' ueberschreiben, damit volle Nachrichtentexte
    nicht im Klartext in /var/log landen (Laenge bleibt sichtbar, hilft
    beim Debuggen ohne den Inhalt preiszugeben)."""
    if len(text) <= keep:
        return text
    return text[:keep] + "#" * (len(text) - keep)
