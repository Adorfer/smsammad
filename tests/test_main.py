import sys

import pytest

import smsammad.main as main_module
from smsammad.main import main

CONFIG_INI = """
[teltonika]
host = 192.168.1.1
username = user1
password = pass
default_country_code = DE

[zammad]
url = https://zammad.example.local
token = tok

[notification]
smtp_host = mail.example.local
smtp_port = 587
smtp_user = bot@example.local
smtp_password = secret
recipient = ops@example.local
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
