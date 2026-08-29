from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from smsammad import stats_report
from smsammad.config import (
    BalanceConfig,
    Config,
    NotificationConfig,
    TeltonikaConfig,
    TicketToSmsConfig,
    ZammadConfig,
)
from smsammad.sms_budget import SmsBudget
from smsammad.stats_report import (
    _avg_daily_consumption,
    _collect_balance,
    _format_balance_html,
    _format_balance_text,
    _format_html_table,
    _format_text_table,
)


def test_format_text_table_empty():
    assert _format_text_table({}) == "(keine Daten)"


def test_format_text_table_shows_all_periods_per_group():
    pivot = {
        "Users": {
            "24 Stunden": {"in": 1, "out": 2},
            "7 Tage": {"in": 3, "out": 4},
            "30 Tage": {"in": 5, "out": 6},
        },
    }
    table = _format_text_table(pivot)
    assert "Users" in table
    assert "24 Stunden" in table
    assert "7 Tage" in table
    assert "30 Tage" in table
    line = [ln for ln in table.splitlines() if ln.startswith("Users")][0]
    assert [int(x) for x in line.split()[1:]] == [1, 2, 3, 4, 5, 6]


def test_format_html_table_empty():
    assert _format_html_table({}) == "<p>(keine Daten)</p>"


def test_format_html_table_contains_group_and_period_headers_and_colors():
    pivot = {
        "SMS-unbekannt": {
            "24 Stunden": {"in": 1, "out": 0},
            "7 Tage": {"in": 1, "out": 0},
            "30 Tage": {"in": 1, "out": 0},
        },
    }
    html = _format_html_table(pivot)
    assert "<table" in html
    assert "SMS-unbekannt" in html
    assert "24 Stunden" in html
    assert "7 Tage" in html
    assert "30 Tage" in html
    assert stats_report._COLOR_IN in html
    assert stats_report._COLOR_OUT in html


def _config(notification=None, balance=None):
    return Config(
        teltonika=TeltonikaConfig(host="h", username="u", password="p", default_country_code="DE"),
        zammad=ZammadConfig(
            url="https://z", token="t", group="Users", new_customer_group="Triage",
            phone_field="mobile", overflow_priority=3,
        ),
        ticket_to_sms=TicketToSmsConfig(
            max_sms_parts=3,
            max_sms_per_hour=20,
            max_sms_per_24h=100,
            stats_db_file="/nonexistent/should-not-be-used.json",
            budget_notify_cooldown_minutes=60,
        ),
        notification=notification,
        balance=balance,
    )


def _balance_config(**overrides):
    defaults = dict(
        query_number="111",
        query_text="Guthaben",
        reply_sender="80808",
        warn_threshold_eur=5.0,
        alarm_threshold_eur=1.0,
        query_interval_hours=24,
        closed_state_id=4,
    )
    defaults.update(overrides)
    return BalanceConfig(**defaults)


def test_avg_daily_consumption_none_with_fewer_than_two_samples():
    assert _avg_daily_consumption([]) is None
    assert _avg_daily_consumption([(datetime.now(timezone.utc), 5.0)]) is None


def test_avg_daily_consumption_computes_rate_per_day():
    now = datetime.now(timezone.utc)
    history = [(now - timedelta(days=2), 10.0), (now, 6.0)]
    assert _avg_daily_consumption(history) == 2.0


def test_avg_daily_consumption_none_when_balance_increased():
    """Aufladung zwischen den Messpunkten -- kein sinnvoller 'Verbrauch'
    berechenbar, aber auch kein negativer Wert, der die
    Reichweiten-Schaetzung verfaelschen wuerde."""
    now = datetime.now(timezone.utc)
    history = [(now - timedelta(days=1), 2.0), (now, 10.0)]
    rate = _avg_daily_consumption(history)
    assert rate is not None and rate < 0


def test_collect_balance_no_data(tmp_path):
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    data = _collect_balance(budget, datetime.now(timezone.utc))
    assert data["latest"] is None
    assert data["runway_days"] is None


def test_collect_balance_estimates_runway_from_7d_rate(tmp_path):
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    now = datetime.now(timezone.utc)
    budget.record_balance(10.0, now=now - timedelta(days=5))
    budget.record_balance(5.0, now=now)  # 1 Euro/Tag ueber 5 Tage

    data = _collect_balance(budget, now)
    assert data["latest"] == (now, 5.0)
    assert data["consumption"]["7 Tage"] == 1.0
    assert data["runway_days"] == 5.0


def test_format_balance_text_no_data():
    assert _format_balance_text({"latest": None, "consumption": {}, "runway_days": None}) == (
        "(keine Guthaben-Daten)"
    )


def test_format_balance_text_unbestimmbar_when_no_positive_rate():
    data = {
        "latest": (datetime.now(timezone.utc), 5.0),
        "consumption": {"24 Stunden": None, "7 Tage": None, "30 Tage": None},
        "runway_days": None,
    }
    text = _format_balance_text(data)
    assert "5.00 Euro" in text
    assert "unbestimmbar" in text


def test_format_balance_html_contains_table_and_runway():
    data = {
        "latest": (datetime.now(timezone.utc), 5.0),
        "consumption": {"24 Stunden": 0.1, "7 Tage": 0.2, "30 Tage": 0.15},
        "runway_days": 25.0,
    }
    html = _format_balance_html(data)
    assert "<table" in html
    assert "5.00 Euro" in html
    assert "25 Tage" in html


def test_dry_run_sends_no_mail(tmp_path):
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)

    with patch("smsammad.notify.smtplib.SMTP") as smtp_cls:
        stats_report.run(budget, _config(), dry_run=True)
        smtp_cls.assert_not_called()


def test_real_run_sends_html_mail_with_all_periods(tmp_path):
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    budget.record_sent(1, group="Users", agent="Alice")
    budget.record_received(group="Users")

    notification = NotificationConfig(
        smtp_host="mail.example.local",
        smtp_port=587,
        smtp_user="bot@example.local",
        smtp_password="secret",
        recipient="ops@example.local",
    )

    smtp = MagicMock()
    with patch("smsammad.notify.smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = smtp
        stats_report.run(budget, _config(notification), dry_run=False)

    smtp.send_message.assert_called_once()
    sent_message = smtp.send_message.call_args[0][0]
    assert sent_message["Subject"] == "SMSammad: SMS-Statistik"
    assert sent_message.is_multipart()

    text_body = sent_message.get_body(("plain",)).get_content()
    assert "24 Stunden" in text_body
    assert "7 Tage" in text_body
    assert "30 Tage" in text_body
    assert "Users" in text_body

    html_body = sent_message.get_body(("html",)).get_content()
    assert "<table" in html_body
    assert "Users" in html_body


def test_run_omits_balance_section_when_not_configured(tmp_path):
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    budget.record_balance(5.0)

    notification = NotificationConfig(
        smtp_host="mail.example.local", smtp_port=587, smtp_user="bot@example.local",
        smtp_password="secret", recipient="ops@example.local",
    )
    smtp = MagicMock()
    with patch("smsammad.notify.smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = smtp
        stats_report.run(budget, _config(notification, balance=None), dry_run=False)

    text_body = smtp.send_message.call_args[0][0].get_body(("plain",)).get_content()
    assert "Guthaben" not in text_body


def test_run_includes_balance_section_when_configured(tmp_path):
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    budget.record_balance(5.0)

    notification = NotificationConfig(
        smtp_host="mail.example.local", smtp_port=587, smtp_user="bot@example.local",
        smtp_password="secret", recipient="ops@example.local",
    )
    smtp = MagicMock()
    with patch("smsammad.notify.smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = smtp
        stats_report.run(
            budget, _config(notification, balance=_balance_config()), dry_run=False
        )

    sent_message = smtp.send_message.call_args[0][0]
    text_body = sent_message.get_body(("plain",)).get_content()
    html_body = sent_message.get_body(("html",)).get_content()
    assert "Guthaben" in text_body
    assert "5.00 Euro" in text_body
    assert "Guthaben" in html_body
    assert "5.00 Euro" in html_body
