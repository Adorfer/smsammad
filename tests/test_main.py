import sys

import pytest

import smsammad.main as main_module
from smsammad.main import main
from smsammad.setup_check import SetupProblem

CONFIG_INI = """
[teltonika]
host = "192.168.1.1"
username = "user1"
password = "pass"
default_country_code = "DE"

[zammad]
url = "https://zammad.example.local"
token = "tok"

[notification]
smtp_host = "mail.example.local"
smtp_port = 587
smtp_user = "bot@example.local"
smtp_password = "secret"
recipient = "ops@example.local"
enabled = true
"""


def test_requires_subcommand(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["smsammad"])
    with pytest.raises(SystemExit):
        main()


def test_unknown_config_path_exits(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["smsammad", "--config", "/nonexistent.ini", "sms-to-ticket"]
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


def test_dry_run_forces_notification_disabled(monkeypatch, tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(CONFIG_INI, encoding="utf-8")
    config_path.chmod(0o600)

    monkeypatch.setattr(
        sys,
        "argv",
        ["smsammad", "--config", str(config_path), "--dry-run", "sms-to-ticket"],
    )

    def boom(*args, **kwargs):
        raise RuntimeError("simulierter Fehler")

    monkeypatch.setattr(main_module, "_run_direction", boom)

    sent = []
    monkeypatch.setattr(
        main_module,
        "send_mail",
        lambda config, subject, body: sent.append(config),
    )

    with pytest.raises(SystemExit):
        main()

    assert len(sent) == 1
    assert sent[0].enabled is False


def test_setup_problem_is_reported_without_traceback(monkeypatch, tmp_path, caplog):
    """SetupProblem ist ein bereits fertig formatierter Diagnosebericht
    (siehe setup_check.py), kein unerwarteter Absturz -- Log und Mail
    duerfen deshalb KEINEN Python-Traceback enthalten, nur die Meldung
    selbst."""
    config_path = tmp_path / "config.ini"
    config_path.write_text(CONFIG_INI, encoding="utf-8")
    config_path.chmod(0o600)

    monkeypatch.setattr(sys, "argv", ["smsammad", "--config", str(config_path), "check-setup"])

    message = "Was Du als Zammad-Admin evtl. ändern solltest:\n- phone_field: nicht pruefbar"

    def boom(*args, **kwargs):
        raise SetupProblem(message)

    monkeypatch.setattr(main_module, "_run_direction", boom)

    sent = []
    monkeypatch.setattr(
        main_module,
        "send_mail",
        lambda config, subject, body: sent.append((subject, body)),
    )

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "Traceback" not in caplog.text
    assert message in caplog.text

    assert len(sent) == 1
    subject, body = sent[0]
    assert "check-setup" in subject
    assert body == message
    assert "Traceback" not in body


def test_other_exceptions_still_include_traceback(monkeypatch, tmp_path, caplog):
    """Gegenprobe: eine ECHTE unerwartete Exception (kein SetupProblem)
    muss weiterhin mit vollem Traceback geloggt/gemailt werden."""
    config_path = tmp_path / "config.ini"
    config_path.write_text(CONFIG_INI, encoding="utf-8")
    config_path.chmod(0o600)

    monkeypatch.setattr(sys, "argv", ["smsammad", "--config", str(config_path), "sms-to-ticket"])

    def boom(*args, **kwargs):
        raise RuntimeError("echter unerwarteter Fehler")

    monkeypatch.setattr(main_module, "_run_direction", boom)

    sent = []
    monkeypatch.setattr(
        main_module,
        "send_mail",
        lambda config, subject, body: sent.append((subject, body)),
    )

    with pytest.raises(SystemExit):
        main()

    assert len(sent) == 1
    _, body = sent[0]
    assert "Traceback" in body


@pytest.mark.parametrize(
    "argv_tail",
    [
        # Flags VOR dem Subcommand (bisherige, einzig funktionierende Reihenfolge)
        ["--config", "{config}", "--dry-run", "sms-to-ticket"],
        # Flags NACH dem Subcommand (der eigentliche Fix)
        ["sms-to-ticket", "--config", "{config}", "--dry-run"],
        # gemischt
        ["--config", "{config}", "sms-to-ticket", "--dry-run"],
    ],
    ids=["davor", "danach", "gemischt"],
)
def test_dry_run_flag_works_regardless_of_position(monkeypatch, tmp_path, argv_tail):
    config_path = tmp_path / "config.ini"
    config_path.write_text(CONFIG_INI, encoding="utf-8")
    config_path.chmod(0o600)

    argv = ["smsammad"] + [
        token.format(config=str(config_path)) for token in argv_tail
    ]
    monkeypatch.setattr(sys, "argv", argv)

    calls = []
    monkeypatch.setattr(
        main_module,
        "_run_direction",
        lambda name, config, dry_run, **kwargs: calls.append((name, dry_run)),
    )

    main()

    assert calls == [("sms-to-ticket", True)]


def test_reset_access_clears_block_without_touching_router_or_zammad(monkeypatch, tmp_path):
    """reset-access ist rein lokal: es darf weder einen Teltonika- noch
    einen Zammad-Client anlegen (sonst koennte ausgerechnet der Reset einen
    Router-Zugriff und damit einen fail2ban-Fehlversuch ausloesen)."""
    from smsammad.sms_budget import SmsBudget

    db_file = tmp_path / "stats.db"
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        CONFIG_INI + f'\n[ticket_to_sms]\nstats_db_file = "{db_file}"\n', encoding="utf-8"
    )
    config_path.chmod(0o600)
    SmsBudget(db_file, 20, 100).record_access_failure("cgi")

    def forbidden(*args, **kwargs):
        raise AssertionError("reset-access darf keinen Router-/Zammad-Client anlegen")

    monkeypatch.setattr(main_module, "TeltonikaClient", forbidden)
    monkeypatch.setattr(main_module, "ZammadClient", forbidden)
    monkeypatch.setattr(sys, "argv", ["smsammad", "--config", str(config_path), "reset-access"])

    main()

    assert SmsBudget(db_file, 20, 100).list_access_blocks() == []
