#!/usr/bin/env python3
"""Startpunkt fuer Cronjobs: laeuft direkt mit dem System-Python, ohne
vorherige Installation des Pakets (siehe pyproject.toml fuer die
benoetigten Ubuntu-Pakete)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

# Python-Modulname -> apt-Paketname, fuer die Laufzeit-Abhaengigkeiten aus
# pyproject.toml/README (nur Laufzeit, nicht die Test-Pakete).
_APT_PACKAGES = {"requests": "python3-requests", "phonenumbers": "python3-phonenumbers"}


def _missing_dependency_message(module_name: str | None) -> str | None:
    """Fertige, kopierbare Fehlermeldung fuer ein fehlendes BEKANNTES
    Laufzeit-Paket. None fuer alles andere -- dann soll der
    Original-Traceback normal durchgereicht werden, statt ein
    unbekanntes Problem faelschlich als "Paket fehlt" zu kaschieren."""
    if module_name not in _APT_PACKAGES:
        return None
    return (
        f"Fehlendes Python-Paket: {module_name}\n\n"
        "Bitte installieren (kein pip/venv noetig):\n"
        f"  sudo apt-get install {' '.join(sorted(_APT_PACKAGES.values()))}\n"
    )


try:
    from smsammad.main import main  # noqa: E402
except ModuleNotFoundError as exc:
    message = _missing_dependency_message(exc.name)
    if message is None:
        raise
    sys.stderr.write(message)
    sys.exit(1)

if __name__ == "__main__":
    main()
