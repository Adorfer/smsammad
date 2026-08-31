import sys

import pytest

import smsammad.main as main_module
from smsammad.main import main

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
        lambda name, config, dry_run, fix=False: calls.append((name, dry_run)),
    )

    main()

    assert calls == [("sms-to-ticket", True)]
