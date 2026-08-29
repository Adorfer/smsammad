from unittest.mock import MagicMock, patch

from smsammad.config import NotificationConfig
from smsammad.notify import send_mail


def test_none_config_does_nothing():
    send_mail(None, "subject", "body")  # muss ohne Fehler durchlaufen


@patch("smsammad.notify.smtplib.SMTP")
def test_disabled_config_does_nothing(smtp_cls):
    config = NotificationConfig(
        smtp_host="mail.example.local",
        smtp_port=587,
        smtp_user="bot@example.local",
        smtp_password="secret",
        recipient="ops@example.local",
        enabled=False,
    )

    send_mail(config, "subject", "body")

    smtp_cls.assert_not_called()


@patch("smsammad.notify.smtplib.SMTP")
def test_sends_mail_via_smtp(smtp_cls):
    smtp = MagicMock()
    smtp_cls.return_value.__enter__.return_value = smtp
    config = NotificationConfig(
        smtp_host="mail.example.local",
        smtp_port=587,
        smtp_user="bot@example.local",
        smtp_password="secret",
        recipient="ops@example.local",
    )

    send_mail(config, "Fehler!", "Details")

    smtp_cls.assert_called_once_with("mail.example.local", 587, timeout=15)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("bot@example.local", "secret")
    smtp.send_message.assert_called_once()
    sent_message = smtp.send_message.call_args[0][0]
    assert sent_message["Subject"] == "Fehler!"
    assert sent_message["To"] == "ops@example.local"
    assert not sent_message.is_multipart()


@patch("smsammad.notify.smtplib.SMTP")
def test_sends_html_alternative_when_given(smtp_cls):
    smtp = MagicMock()
    smtp_cls.return_value.__enter__.return_value = smtp
    config = NotificationConfig(
        smtp_host="mail.example.local",
        smtp_port=587,
        smtp_user="bot@example.local",
        smtp_password="secret",
        recipient="ops@example.local",
    )

    send_mail(config, "Statistik", "Text-Fallback", html_body="<p>HTML-Inhalt</p>")

    sent_message = smtp.send_message.call_args[0][0]
    assert sent_message.is_multipart()
    assert sent_message.get_body(("plain",)).get_content().strip() == "Text-Fallback"
    assert "<p>HTML-Inhalt</p>" in sent_message.get_body(("html",)).get_content()
