from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from smsammad import balance_check
from smsammad.balance_check import _cleanup_ussd_text
from smsammad.config import (
    BalanceConfig,
    Config,
    TeltonikaConfig,
    TicketToSmsConfig,
    ZammadConfig,
)
from smsammad.sms_budget import SmsBudget
from smsammad.teltonika import TeltonikaError
from smsammad.teltonika_api import TeltonikaApiError


class FakeTeltonika:
    def __init__(self, fail=False):
        self.sent = []
        self._fail = fail

    def send(self, number, text):
        if self._fail:
            raise TeltonikaError("Router nicht erreichbar")
        self.sent.append((number, text))


class FakeZammad:
    """Nur fuer den USSD-Ticket-Pfad benoetigt -- deckt find_or_create/
    find_open_ticket_for_customer/create_ticket/add_article/set_*/get_tags
    minimal ab, analog zu den Fakes in test_sms_to_ticket.py."""

    def __init__(self):
        self.created_tickets = []
        self.article_calls = []
        self.states = []
        self.subjects = []
        self.priorities = []

    def find_customer_by_phone(self, e164_number, default_region):
        return None

    def find_or_create_customer_by_phone(self, e164_number, default_region):
        return 1000, True

    def find_open_ticket_for_customer(self, customer_id):
        return None

    def create_ticket(self, customer_id, group, subject, body):
        self.created_tickets.append((customer_id, group, subject, body))
        return 555

    def add_article(self, ticket_id, body, internal=False, article_type="phone", sender="Customer"):
        self.article_calls.append((ticket_id, body, internal, article_type, sender))

    def set_state(self, ticket_id, state_id):
        self.states.append((ticket_id, state_id))

    def set_subject(self, ticket_id, subject):
        self.subjects.append((ticket_id, subject))

    def set_priority(self, ticket_id, priority_id):
        self.priorities.append((ticket_id, priority_id))


def _config(tmp_path, balance=None):
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
            stats_db_file=tmp_path / "stats.db",
            budget_notify_cooldown_minutes=60,
        ),
        notification=None,
        balance=balance,
    )


def _balance_config(**overrides):
    defaults = dict(
        warn_threshold_eur=5.0,
        alarm_threshold_eur=1.0,
        method="ussd",
        query_interval_hours=24,
        closed_state_id=4,
        ussd_code="*100#",
        api_username="",
        api_password="",
        modem_id="1-1",
        query_number="",
        query_text="",
        reply_sender="",
    )
    defaults.update(overrides)
    return BalanceConfig(**defaults)


def _ussd_only():
    return _balance_config(method="ussd", api_username="smsammad", api_password="secret")


def _sms_only():
    return _balance_config(method="sms", query_number="111", query_text="Guthaben", reply_sender="80808")


def _both_configured(method="ussd"):
    return _balance_config(
        method=method,
        api_username="smsammad", api_password="secret",
        query_number="111", query_text="Guthaben", reply_sender="80808",
    )


def test_does_nothing_when_not_configured(tmp_path):
    teltonika = FakeTeltonika()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)

    balance_check.run(teltonika, FakeZammad(), _config(tmp_path, balance=None), dry_run=False, budget=budget)

    assert teltonika.sent == []


def test_dry_run_ussd_sends_nothing_and_does_not_mark_queried(tmp_path):
    teltonika = FakeTeltonika()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_ussd_only())

    balance_check.run(teltonika, FakeZammad(), config, dry_run=True, budget=budget)

    assert teltonika.sent == []
    assert budget.should_query_balance(interval_hours=24)


def test_dry_run_sms_sends_nothing(tmp_path):
    teltonika = FakeTeltonika()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_sms_only())

    balance_check.run(teltonika, FakeZammad(), config, dry_run=True, budget=budget)

    assert teltonika.sent == []


def test_skips_when_already_queried_within_interval(tmp_path):
    teltonika = FakeTeltonika()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    budget.mark_balance_queried()
    config = _config(tmp_path, balance=_sms_only())

    balance_check.run(teltonika, FakeZammad(), config, dry_run=False, budget=budget)

    assert teltonika.sent == []


def test_queries_again_after_interval_elapsed(tmp_path):
    teltonika = FakeTeltonika()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    long_ago = datetime.now(timezone.utc) - timedelta(hours=25)
    budget.mark_balance_queried(now=long_ago)
    config = _config(tmp_path, balance=_sms_only())

    balance_check.run(teltonika, FakeZammad(), config, dry_run=False, budget=budget)

    assert teltonika.sent == [("111", "Guthaben")]


def test_sms_method_sends_query_and_marks_queried(tmp_path):
    teltonika = FakeTeltonika()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_sms_only())

    balance_check.run(teltonika, FakeZammad(), config, dry_run=False, budget=budget)

    assert teltonika.sent == [("111", "Guthaben")]
    assert not budget.should_query_balance(interval_hours=24)


def _ussd_response(amount="6,00"):
    return f"2026-08-30 00:07:34 1,Aktuelles Guthaben: {amount} EUR\r\nBonus-Guthaben: 9,52 EUR\r\n0 Weitere Optionen,15\n"


def test_ussd_method_uses_configurable_regex_for_changed_provider_wording(tmp_path):
    """Simuliert ein geaendertes USSD-Menue -- muss allein durch Aendern von
    config.balance.ussd_balance_regex funktionieren, ohne Code-Anpassung."""
    teltonika = FakeTeltonika()
    zammad = FakeZammad()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(
        tmp_path,
        balance=_balance_config(
            method="ussd", api_username="smsammad", api_password="secret",
            ussd_balance_regex=r"Neuer Kontostand:\s*(\d+(?:,\d+)?)\s*EUR",
        ),
    )

    with patch("smsammad.balance_check.TeltonikaApiClient") as api_cls:
        api_cls.return_value.send_ussd.return_value = "Neuer Kontostand: 12,34 EUR. Danke!"
        balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)

    assert budget.latest_balance()[1] == 12.34


def test_cleanup_ussd_text_decodes_html_entities():
    assert _cleanup_ussd_text("Guthaben &amp; Verbrauch") == "Guthaben & Verbrauch"


def test_cleanup_ussd_text_fixes_known_mojibake():
    """Live beobachteter RutOS-Firmware-Bug: 'ae' (UTF-8 0xC3 0xA4) wird
    beim USSD-Decoding zu '?¤' -- rein kosmetischer Fix fuer den
    bisher beobachteten Fall."""
    assert _cleanup_ussd_text("W?¤hl bitte aus:") == "Wähl bitte aus:"


def test_cleanup_ussd_text_leaves_normal_text_unchanged():
    assert _cleanup_ussd_text("Aktuelles Guthaben: 25,77 EUR") == "Aktuelles Guthaben: 25,77 EUR"


def test_ussd_method_cleans_up_real_captured_response(tmp_path):
    """Regression: exakt die live am echten Router beobachtete, kaputte
    Antwort -- Ticket-Body muss lesbar sein, keine '&amp;'/'?¤'-Reste."""
    teltonika = FakeTeltonika()
    zammad = FakeZammad()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_ussd_only())
    real_response = (
        "2026-08-30 00:33:30 1,Aktuelles Guthaben: 25,77 EUR\r\n"
        "Bonus-Guthaben: 9,49 EUR\r\nW?¤hl bitte aus:\r\n1 Aufladen\r\n"
        "2 Guthaben &amp; Verbrauch\r\n3 Tarife &amp; Optionen\r\n"
        "0 Weitere Optionen,15\n"
    )

    with patch("smsammad.balance_check.TeltonikaApiClient") as api_cls:
        api_cls.return_value.send_ussd.return_value = real_response
        balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)

    body = zammad.created_tickets[0][3]
    assert "&amp;" not in body
    assert "?¤" not in body
    assert "Wähl bitte aus" in body
    assert "Guthaben & Verbrauch" in body


def test_ussd_method_creates_ticket_and_records_balance(tmp_path):
    teltonika = FakeTeltonika()
    zammad = FakeZammad()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_ussd_only())

    with patch("smsammad.balance_check.TeltonikaApiClient") as api_cls:
        api_cls.return_value.send_ussd.return_value = _ussd_response("6,00")
        balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)

    api_cls.return_value.send_ussd.assert_called_once_with("*100#")
    assert len(zammad.created_tickets) == 1
    customer_id, group, subject, body = zammad.created_tickets[0]
    assert (customer_id, group, subject) == (1000, "Triage", "SMS-Guthaben")
    assert "Aktuelles Guthaben: 6,00 EUR" in body
    assert zammad.states == [(555, 4)]
    assert budget.latest_balance()[1] == 6.0
    # USSD ist kostenlos/synchron und darf beliebig oft laufen -- das
    # Zeitfenster gilt nur fuer die SMS-Abfrage, siehe sms_budget.py.
    assert budget.should_query_balance(interval_hours=24)


def test_ussd_method_unparseable_response_raises_and_falls_back_when_configured(tmp_path):
    teltonika = FakeTeltonika()
    zammad = FakeZammad()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_both_configured(method="ussd"))

    with patch("smsammad.balance_check.TeltonikaApiClient") as api_cls:
        api_cls.return_value.send_ussd.return_value = "Werbetext ohne erkennbaren Betrag"
        balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)

    # USSD lieferte keinen parsebaren Betrag -> Fallback auf SMS.
    assert teltonika.sent == [("111", "Guthaben")]


def test_ussd_method_access_denied_falls_back_when_configured(tmp_path):
    teltonika = FakeTeltonika()
    zammad = FakeZammad()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_both_configured(method="ussd"))

    with patch("smsammad.balance_check.TeltonikaApiClient") as api_cls:
        api_cls.return_value.send_ussd.side_effect = TeltonikaApiError("Login fehlgeschlagen: HTTP 401")
        balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)

    assert teltonika.sent == [("111", "Guthaben")]


def test_repeated_ussd_queries_are_never_throttled(tmp_path):
    """USSD ist synchron/kostenlos -- anders als SMS darf es beliebig oft
    hintereinander abgefragt werden, auch weit innerhalb von
    query_interval_hours."""
    teltonika = FakeTeltonika()
    zammad = FakeZammad()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_ussd_only())

    with patch("smsammad.balance_check.TeltonikaApiClient") as api_cls:
        api_cls.return_value.send_ussd.return_value = _ussd_response("6,00")
        balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)
        balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)
        balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)

    assert api_cls.return_value.send_ussd.call_count == 3


def test_ussd_fallback_to_sms_skipped_and_raises_when_sms_recently_queried(tmp_path):
    """USSD schlaegt fehl, SMS-Fallback ist konfiguriert -- aber eine
    SMS-Abfrage wurde bereits vor Kurzem geschickt (Zeitfenster noch
    aktiv). Der Fallback darf dann NICHT trotzdem eine weitere SMS
    schicken, UND das urspruengliche USSD-Problem darf nicht
    stillschweigend verschwinden (muss weiterhin durchschlagen, damit es
    z.B. per Fehlermail auffaellt)."""
    teltonika = FakeTeltonika()
    zammad = FakeZammad()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    budget.mark_balance_queried()
    config = _config(tmp_path, balance=_both_configured(method="ussd"))

    with patch("smsammad.balance_check.TeltonikaApiClient") as api_cls:
        api_cls.return_value.send_ussd.side_effect = TeltonikaApiError("Login fehlgeschlagen: HTTP 401")
        with pytest.raises(TeltonikaApiError):
            balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)

    assert teltonika.sent == []


def test_ussd_method_failure_without_fallback_configured_raises(tmp_path):
    teltonika = FakeTeltonika()
    zammad = FakeZammad()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_ussd_only())  # keine SMS-Zugangsdaten

    with patch("smsammad.balance_check.TeltonikaApiClient") as api_cls:
        api_cls.return_value.send_ussd.side_effect = TeltonikaApiError("Login fehlgeschlagen: HTTP 401")
        with pytest.raises(TeltonikaApiError):
            balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)

    assert teltonika.sent == []


def test_sms_method_send_failure_falls_back_to_ussd_when_configured(tmp_path):
    teltonika = FakeTeltonika(fail=True)
    zammad = FakeZammad()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_both_configured(method="sms"))

    with patch("smsammad.balance_check.TeltonikaApiClient") as api_cls:
        api_cls.return_value.send_ussd.return_value = _ussd_response("6,00")
        balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)

    api_cls.return_value.send_ussd.assert_called_once_with("*100#")
    assert budget.latest_balance()[1] == 6.0


def test_sms_method_send_failure_without_fallback_configured_raises(tmp_path):
    teltonika = FakeTeltonika(fail=True)
    zammad = FakeZammad()
    budget = SmsBudget(tmp_path / "stats.db", 20, 100)
    config = _config(tmp_path, balance=_sms_only())

    with pytest.raises(TeltonikaError):
        balance_check.run(teltonika, zammad, config, dry_run=False, budget=budget)
