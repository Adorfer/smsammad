from pathlib import Path

import pytest

from smsammad.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
import smsammad.config as config_module


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.ini"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


CONFIG_TEMPLATE = """
[teltonika]
host = "192.168.1.1"
username = {username}
password = {password}
default_country_code = "DE"

[zammad]
url = "https://zammad.example.local"
token = "tok"
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


def test_unquoted_string_value_is_rejected(tmp_path):
    """Strings MUESSEN gequotet sein -- kein stiller Fallback mehr, siehe
    config._unquote. Sonst waere z.B. bei einem Wert mit Inline-Kommentar
    (key = wert # kommentar) nicht eindeutig entscheidbar, wo der Wert
    aufhoert und der Kommentar anfaengt."""
    config_path = _write_config(
        tmp_path,
        CONFIG_TEMPLATE.format(
            username="user1", password='"p"', group='"Users"', new_customer_group='"Triage"'
        ),
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_mismatched_quotes_are_rejected(tmp_path):
    config_path = _write_config(
        tmp_path,
        CONFIG_TEMPLATE.format(
            username="\"user1'", password='"p"', group='"Users"', new_customer_group='"Triage"'
        ),
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_value_with_trailing_inline_comment_is_parsed_correctly(tmp_path):
    """Regression: 'key = \"value\" # comment' liess frueher den gesamten
    Rest inkl. Anfuehrungszeichen und Kommentartext im Wert stehen
    (configparser kennt Inline-Kommentare nicht automatisch) -- live als
    korrupter Zammad-Token beobachtet (401 'Cant find User for Token')."""
    config_path = _write_config(
        tmp_path,
        CONFIG_TEMPLATE.format(
            username='"user1"  # das ist der Benutzername',
            password='"p"',
            group='"Users"',
            new_customer_group='"Triage"',
        ),
    )

    config = load_config(config_path)

    assert config.teltonika.username == "user1"


def test_hash_inside_quotes_is_not_treated_as_comment(tmp_path):
    """Ein '#' INNERHALB der Anfuehrungszeichen (z.B. Teil eines echten
    Passworts) darf nicht als Kommentaranfang missverstanden werden --
    auch nicht, wenn ihm ein Leerzeichen vorausgeht."""
    config_path = _write_config(
        tmp_path,
        CONFIG_TEMPLATE.format(
            username='"user1"',
            password='"pass # word"',
            group='"Users"',
            new_customer_group='"Triage"',
        ),
    )

    config = load_config(config_path)

    assert config.teltonika.password == "pass # word"


def test_hash_without_surrounding_space_inside_quotes_stays_intact(tmp_path):
    config_path = _write_config(
        tmp_path,
        CONFIG_TEMPLATE.format(
            username='"user1"',
            password='"4784Umzwi#3+"',
            group='"Users"',
            new_customer_group='"Triage"',
        ),
    )

    config = load_config(config_path)

    assert config.teltonika.password == "4784Umzwi#3+"


def test_numeric_and_boolean_fields_unaffected_by_unquoting(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    ) + "max_sms_parts = 3\n"
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.max_sms_parts == 3


def test_numeric_field_with_trailing_inline_comment(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    ) + "max_sms_parts = 3  # maximal drei SMS-Teile\n"
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.max_sms_parts == 3


def test_boolean_field_with_trailing_inline_comment(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    ) + "verify_tls = false  # selbstsigniertes Zertifikat\n"
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.teltonika.verify_tls is False


def test_on_overflow_defaults_to_reject(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.on_overflow == "reject"


def test_on_overflow_accepts_truncate(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    ) + 'on_overflow = "truncate"\n'
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.on_overflow == "truncate"


def test_on_overflow_rejects_invalid_value(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    ) + 'on_overflow = "delete"\n'
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_send_mode_defaults_to_multipart(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.send_mode == "multipart"


def test_send_mode_accepts_classic(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    ) + 'send_mode = "classic"\n'
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.send_mode == "classic"


def test_send_mode_rejects_invalid_value(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    ) + 'send_mode = "yolo"\n'
    config_path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_stats_db_file_has_sensible_default(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.ticket_to_sms.stats_db_file.name == "stats.db"


def test_stats_db_file_accepts_explicit_path(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    ) + 'stats_db_file = "~/custom/stats.db"\n'
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert str(config.ticket_to_sms.stats_db_file).endswith("custom/stats.db")
    assert "~" not in str(config.ticket_to_sms.stats_db_file)


def test_balance_config_is_none_when_section_missing(tmp_path):
    content = CONFIG_TEMPLATE.format(
        username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
    )
    config_path = _write_config(tmp_path, content)

    config = load_config(config_path)

    assert config.balance is None


def test_balance_config_defaults_to_ussd_method(tmp_path):
    content = (
        CONFIG_TEMPLATE.format(
            username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
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
            username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
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
            username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
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
            username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
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
            username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
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
            username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
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
            username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
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
            username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
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


def test_default_config_path_is_next_to_run_py():
    """Ohne --config wird config.ini nicht in ~/.config/smsammad/
    gesucht, sondern direkt neben run.py -- unabhaengig vom
    Arbeitsverzeichnis, ueber die eigene Dateiposition von config.py
    ermittelt (src/smsammad/config.py -> zwei Ebenen hoch)."""
    project_root = Path(config_module.__file__).resolve().parents[2]

    assert DEFAULT_CONFIG_PATH == project_root / "config.ini"
    assert (project_root / "run.py").exists()


def test_load_config_without_explicit_path_uses_default(monkeypatch, tmp_path):
    fake_default = tmp_path / "config.ini"
    fake_default.write_text(
        CONFIG_TEMPLATE.format(
            username='"u"', password='"p"', group='"Users"', new_customer_group='"Triage"'
        ),
        encoding="utf-8",
    )
    fake_default.chmod(0o600)
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", fake_default)

    config = load_config(None)

    assert config.teltonika.host == "192.168.1.1"
