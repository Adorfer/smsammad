from pathlib import Path

import pytest

from smsammad.config import ConfigError, load_config


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.ini"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


CONFIG_TEMPLATE = """
[teltonika]
host = 192.168.1.1
username = {username}
password = {password}
default_country_code = DE

[zammad]
url = https://zammad.example.local
token = tok
group = {group}
new_customer_group = {new_customer_group}

[ticket_to_sms]
"""


def test_quoted_values_with_spaces_get_unquoted(tmp_path):
    config_path = _write_config(
        tmp_path,
        CONFIG_TEMPLATE.format(
            username='"user one"',
            password="'pass word'",
            group='"SMS Support Team"',
            new_customer_group="'Neue Kunden SMS'",
        ),
    )

    config = load_config(config_path)

    assert config.teltonika.username == "user one"
    assert config.teltonika.password == "pass word"
    assert config.zammad.group == "SMS Support Team"
    assert config.zammad.new_customer_group == "Neue Kunden SMS"


def test_unquoted_values_still_work_as_before(tmp_path):
    config_path = _write_config(
        tmp_path,
        CONFIG_TEMPLATE.format(
            username="user1",
            password="user_pass",
            group="Users",
            new_customer_group="Triage",
        ),
    )

    config = load_config(config_path)

    assert config.teltonika.username == "user1"
    assert config.teltonika.password == "user_pass"
    assert config.zammad.group == "Users"
    assert config.zammad.new_customer_group == "Triage"


def test_mismatched_quotes_are_left_untouched(tmp_path):
    config_path = _write_config(
        tmp_path,
        CONFIG_TEMPLATE.format(
            username="\"user1'",
            password="user_pass",
            group="Users",
            new_customer_group="Triage",
        ),
    )

    config = load_config(config_path)

    assert config.teltonika.username == "\"user1'"


def test_numeric_and_boolean_fields_unaffected_by_unquoting(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username="u", password="p", group="Users", new_customer_group="Triage"
    ) + "max_sms_parts = 3\n"
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.max_sms_parts == 3


def test_on_overflow_defaults_to_reject(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username="u", password="p", group="Users", new_customer_group="Triage"
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.on_overflow == "reject"


def test_on_overflow_accepts_truncate(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username="u", password="p", group="Users", new_customer_group="Triage"
    ) + 'on_overflow = "truncate"\n'
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.on_overflow == "truncate"


def test_on_overflow_rejects_invalid_value(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username="u", password="p", group="Users", new_customer_group="Triage"
    ) + 'on_overflow = "delete"\n'
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_stats_db_file_has_sensible_default(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username="u", password="p", group="Users", new_customer_group="Triage"
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.stats_db_file.name == "stats.db"


def test_stats_db_file_accepts_explicit_path(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username="u", password="p", group="Users", new_customer_group="Triage"
    ) + 'stats_db_file = "~/custom/stats.db"\n'
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert str(config.ticket_to_sms.stats_db_file).endswith("custom/stats.db")
    assert "~" not in str(config.ticket_to_sms.stats_db_file)


def test_balance_config_is_none_when_section_missing(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username="u", password="p", group="Users", new_customer_group="Triage"
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.balance is None


def test_balance_config_defaults_to_ussd_method(tmp_path):
    content = (
        CONFIG_TEMPLATE.format(
            username="u", password="p", group="Users", new_customer_group="Triage"
        )
        + '\n[balance]\n'
        'api_username = "smsammad"\n'
        'api_password = "secret"\n'
        "warn_threshold_eur = 5.00\n"
        "alarm_threshold_eur = 1.00\n"
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.balance is not None
    assert config.balance.method == "ussd"
    assert config.balance.ussd_code == "*100#"
    assert config.balance.api_username == "smsammad"
    assert config.balance.api_password == "secret"
    assert config.balance.modem_id == "1-1"
    assert config.balance.warn_threshold_eur == 5.00
    assert config.balance.alarm_threshold_eur == 1.00
    assert config.balance.query_interval_hours == 24
    assert config.balance.closed_state_id == 4
    assert config.balance.ussd_balance_regex == r"Aktuelles Guthaben:\s*(\d+(?:,\d+)?)\s*EUR"
    assert config.balance.sms_balance_regex == r"Guthaben\s+betr[äa]gt\s+(\d+(?:,\d+)?)\s*Euro"


def test_balance_config_sms_method_loaded_when_section_present(tmp_path):
    content = (
        CONFIG_TEMPLATE.format(
            username="u", password="p", group="Users", new_customer_group="Triage"
        )
        + '\n[balance]\n'
        'method = "sms"\n'
        'query_number = "111"\n'
        'query_text = "Guthaben"\n'
        'reply_sender = "80808"\n'
        "warn_threshold_eur = 5.00\n"
        "alarm_threshold_eur = 1.00\n"
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.balance is not None
    assert config.balance.method == "sms"
    assert config.balance.query_number == "111"
    assert config.balance.query_text == "Guthaben"
    assert config.balance.reply_sender == "80808"
    assert config.balance.warn_threshold_eur == 5.00
    assert config.balance.alarm_threshold_eur == 1.00
    assert config.balance.query_interval_hours == 24
    assert config.balance.closed_state_id == 4


def test_balance_config_rejects_invalid_method(tmp_path):
    content = (
        CONFIG_TEMPLATE.format(
            username="u", password="p", group="Users", new_customer_group="Triage"
        )
        + '\n[balance]\n'
        'method = "carrier-pigeon"\n'
        "warn_threshold_eur = 5.00\n"
        "alarm_threshold_eur = 1.00\n"
    )
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_balance_config_ussd_requires_api_credentials(tmp_path):
    content = (
        CONFIG_TEMPLATE.format(
            username="u", password="p", group="Users", new_customer_group="Triage"
        )
        + '\n[balance]\n'
        "warn_threshold_eur = 5.00\n"
        "alarm_threshold_eur = 1.00\n"
    )
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_balance_config_sms_requires_query_number_and_reply_sender(tmp_path):
    content = (
        CONFIG_TEMPLATE.format(
            username="u", password="p", group="Users", new_customer_group="Triage"
        )
        + '\n[balance]\n'
        'method = "sms"\n'
        "warn_threshold_eur = 5.00\n"
        "alarm_threshold_eur = 1.00\n"
    )
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_balance_config_accepts_custom_regex_overrides(tmp_path):
    content = (
        CONFIG_TEMPLATE.format(
            username="u", password="p", group="Users", new_customer_group="Triage"
        )
        + '\n[balance]\n'
        'api_username = "smsammad"\n'
        'api_password = "secret"\n'
        "warn_threshold_eur = 5.00\n"
        "alarm_threshold_eur = 1.00\n"
        r'ussd_balance_regex = "Neuer Kontostand:\s*(\d+(?:,\d+)?)\s*EUR"' "\n"
        r'sms_balance_regex = "Ihr Saldo:\s*(\d+(?:,\d+)?)\s*Euro"' "\n"
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.balance.ussd_balance_regex == r"Neuer Kontostand:\s*(\d+(?:,\d+)?)\s*EUR"
    assert config.balance.sms_balance_regex == r"Ihr Saldo:\s*(\d+(?:,\d+)?)\s*Euro"


def test_balance_config_rejects_invalid_regex(tmp_path):
    content = (
        CONFIG_TEMPLATE.format(
            username="u", password="p", group="Users", new_customer_group="Triage"
        )
        + '\n[balance]\n'
        'api_username = "smsammad"\n'
        'api_password = "secret"\n'
        "warn_threshold_eur = 5.00\n"
        "alarm_threshold_eur = 1.00\n"
        'ussd_balance_regex = "(unclosed"\n'
    )
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_balance_config_rejects_regex_without_capture_group(tmp_path):
    content = (
        CONFIG_TEMPLATE.format(
            username="u", password="p", group="Users", new_customer_group="Triage"
        )
        + '\n[balance]\n'
        'api_username = "smsammad"\n'
        'api_password = "secret"\n'
        "warn_threshold_eur = 5.00\n"
        "alarm_threshold_eur = 1.00\n"
        'sms_balance_regex = "Guthaben ohne Erfassungsgruppe"\n'
    )
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(config_path)
