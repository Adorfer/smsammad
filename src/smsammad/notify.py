"""E-Mail-Versand (stdlib smtplib) -- fuer Fehlerbenachrichtigungen UND die
periodische Stats-Mail."""

import smtplib
from email.message import EmailMessage

from .config import NotificationConfig


def send_mail(
    config: NotificationConfig | None,
    subject: str,
    body: str,
    html_body: str | None = None,
) -> None:
    if config is None or not config.enabled:
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.smtp_user
    message["To"] = config.recipient
    message.set_content(body)
    if html_body is not None:
        message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(config.smtp_user, config.smtp_password)
        smtp.send_message(message)
