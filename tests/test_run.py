"""Testet run.py als eigenstaendigen Prozess (liegt ausserhalb von
src/, daher kein normaler Import moeglich -- Subprocess-Aufruf ist hier
die richtige Ebene)."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_PY = REPO_ROOT / "run.py"


def _run_with_blocked_import(module_name: str) -> subprocess.CompletedProcess:
    """Simuliert ein fehlendes Modul, ohne es tatsaechlich deinstallieren
    zu muessen: ein sys.meta_path-Finder wirft fuer genau diesen
    Modulnamen einen echten ModuleNotFoundError, exakt wie es bei einem
    wirklich fehlenden apt-Paket passieren wuerde."""
    preamble = f"""
import runpy
import sys


class _Blocker:
    def find_spec(self, name, path, target=None):
        if name == {module_name!r}:
            raise ModuleNotFoundError(
                "kein Modul namens " + {module_name!r}, name={module_name!r}
            )
        return None


sys.meta_path.insert(0, _Blocker())
runpy.run_path({str(RUN_PY)!r}, run_name="__main__")
"""
    return subprocess.run(
        [sys.executable, "-c", preamble],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_missing_known_dependency_shows_apt_hint():
    result = _run_with_blocked_import("phonenumbers")

    assert result.returncode == 1
    assert "sudo apt-get install" in result.stderr
    assert "python3-phonenumbers" in result.stderr
    assert "python3-requests" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_unknown_module_shows_real_traceback():
    # "dataclasses" wird von smsammad.main tatsaechlich importiert, ist
    # aber kein bekanntes apt-Paket -- muss also den echten Traceback
    # zeigen statt faelschlich als "fehlendes Paket" kaschiert zu werden.
    result = _run_with_blocked_import("dataclasses")

    assert result.returncode != 0
    assert "Traceback" in result.stderr
    assert "sudo apt-get install" not in result.stderr
