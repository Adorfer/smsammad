from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from smsammad.config import NotificationConfig
from smsammad.sms_budget import SmsBudget
from smsammad.zammad import ZammadUnavailable
from smsammad.zammad_outage import ZammadOutageTracker

T0 = datetime(2026, 9, 12, 20, 0, tzinfo=timezone.utc)
ERROR = ZammadUnavailable("GET tickets/search -> HTTP 502 (Bad Gateway)")


def _notification():
    return NotificationConfig(
        smtp_host="mail.example.local",
        smtp_port=587,
        smtp_user="smsammad@example.local",
        smtp_password="pw",
        recipient="ops@example.local",
    )


def _tracker(tmp_path, minutes=20):
    return ZammadOutageTracker(SmsBudget(tmp_path / "stats.db", 20, 100), _notification(), minutes)


def test_short_outage_sends_no_mail(tmp_path):
    tracker = _tracker(tmp_path)

    with patch("smsammad.zammad_outage.send_mail") as mail:
        assert tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0) is False
        assert tracker.record_unavailable(ERROR, "sms-to-ticket", now=T0 + timedelta(minutes=19)) is False

    mail.assert_not_called()


def test_outage_beyond_threshold_sends_exactly_one_mail(tmp_path):
    tracker = _tracker(tmp_path)

    with patch("smsammad.zammad_outage.send_mail") as mail:
        tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0)
        assert tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0 + timedelta(minutes=20)) is True
        assert tracker.record_unavailable(ERROR, "sms-to-ticket", now=T0 + timedelta(minutes=25)) is False
        assert tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0 + timedelta(hours=3)) is False

    mail.assert_called_once()
    assert "nicht erreichbar" in mail.call_args.kwargs["subject"]
    assert "502" in mail.call_args.kwargs["body"]


def test_duration_counts_from_first_failure_not_last(tmp_path):
    tracker = _tracker(tmp_path)

    with patch("smsammad.zammad_outage.send_mail") as mail:
        for minute in (0, 5, 10, 15):
            tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0 + timedelta(minutes=minute))
        tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0 + timedelta(minutes=21))

    mail.assert_called_once()


def test_two_parallel_tasks_send_only_one_mail(tmp_path):
    """ticket-to-sms und sms-to-ticket laufen parallel (flock nur je Task)
    und teilen sich die DB -- nur einer darf die Mail schicken."""
    a, b = _tracker(tmp_path), _tracker(tmp_path)
    a.record_unavailable(ERROR, "ticket-to-sms", now=T0)

    with patch("smsammad.zammad_outage.send_mail") as mail:
        sent = [
            a.record_unavailable(ERROR, "ticket-to-sms", now=T0 + timedelta(minutes=21)),
            b.record_unavailable(ERROR, "sms-to-ticket", now=T0 + timedelta(minutes=21)),
        ]

    assert sent.count(True) == 1
    mail.assert_called_once()


def test_recovery_after_mail_sends_one_all_clear(tmp_path):
    tracker = _tracker(tmp_path)
    with patch("smsammad.zammad_outage.send_mail"):
        tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0)
        tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0 + timedelta(minutes=30))

    with patch("smsammad.zammad_outage.send_mail") as mail:
        tracker.record_reachable()
        tracker.record_reachable()

    mail.assert_called_once()
    assert "wieder erreichbar" in mail.call_args.kwargs["subject"]


def test_recovery_of_short_outage_is_silent_and_resets_timer(tmp_path):
    """Ein kurzer Aussetzer ohne Mail endet ohne Entwarnung -- und ein
    spaeterer Ausfall beginnt seine 20 min von vorn."""
    tracker = _tracker(tmp_path)

    with patch("smsammad.zammad_outage.send_mail") as mail:
        tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0)
        tracker.record_reachable()
        assert tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0 + timedelta(hours=2)) is False

    mail.assert_not_called()


def test_zero_minutes_mails_immediately(tmp_path):
    tracker = _tracker(tmp_path, minutes=0)

    with patch("smsammad.zammad_outage.send_mail") as mail:
        assert tracker.record_unavailable(ERROR, "ticket-to-sms", now=T0) is True

    mail.assert_called_once()
