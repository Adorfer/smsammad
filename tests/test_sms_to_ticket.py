import codecs

import pytest

from smsammad import sms_to_ticket
from smsammad.config import (
    BalanceConfig,
    Config,
    TeltonikaConfig,
    TicketToSmsConfig,
    ZammadConfig,
)
from smsammad.sms_to_ticket import _resolve_sender_id, _subject_excerpt
from smsammad.teltonika import SmsMessage
from smsammad.zammad import ZammadError


class FakeTeltonika:
    def __init__(self, messages):
        self._messages = messages
        self.deleted = []

    def list_messages(self):
        return self._messages

    def delete(self, index):
        self.deleted.append(index)


class FakeZammad:
    def __init__(
        self,
        existing_customers=None,
        open_ticket=None,
        last_ticket=None,
        ticket_groups=None,
        raise_group_error=False,
    ):
        self._existing_customers = existing_customers or {}
        self._open_ticket = open_ticket
        self._last_ticket = last_ticket
        self._ticket_groups = ticket_groups or {}
        self._raise_group_error = raise_group_error
        self.created_tickets = []
        self.added_articles = []
        self.article_calls = []
        self.customers_created = []
        self.states = []
        self.subjects = []
        self.priorities = []

    def find_customer_by_phone(self, e164_number, default_region):
        return self._existing_customers.get(e164_number)

    def find_or_create_customer_by_phone(self, e164_number, default_region):
        if e164_number in self._existing_customers:
            return self._existing_customers[e164_number], False
        self.customers_created.append(e164_number)
        return 1000, True

    def find_open_ticket_for_customer(self, customer_id):
        return self._open_ticket

    def find_last_ticket_for_customer(self, customer_id):
        return self._last_ticket

    def get_ticket(self, ticket_id):
        if self._raise_group_error:
            raise ZammadError("GET tickets/{} -> HTTP 403: forbidden".format(ticket_id))
        return {"group_id": self._ticket_groups.get(ticket_id, 42)}

    def get_group_name(self, group_id):
        return f"Gruppe-{group_id}"

    def create_ticket(self, customer_id, group, subject, body):
        self.created_tickets.append((customer_id, group, subject, body))
        return 555

    def add_article(self, ticket_id, body, internal=False, article_type="phone", sender="Customer"):
        self.added_articles.append((ticket_id, body, internal))
        self.article_calls.append(
            {
                "ticket_id": ticket_id,
                "body": body,
                "internal": internal,
                "article_type": article_type,
                "sender": sender,
            }
        )

    def set_state(self, ticket_id, state_id):
        self.states.append((ticket_id, state_id))

    def set_subject(self, ticket_id, subject):
        self.subjects.append((ticket_id, subject))

    def set_priority(self, ticket_id, priority_id):
        self.priorities.append((ticket_id, priority_id))


class FakeBudget:
    def __init__(self):
        self.received = []
        self.balances = []

    def record_received(self, group=None, ticket_number=None, now=None):
        self.received.append((group, ticket_number))

    def record_balance(self, amount_eur, now=None):
        self.balances.append(amount_eur)


def _config(
    short_number_prefix="", unresolved_sender_prefix="", balance=None, group_from_last_ticket=False
):
    return Config(
        teltonika=TeltonikaConfig(
            host="h",
            username="u",
            password="p",
            default_country_code="DE",
            short_number_prefix=short_number_prefix,
            unresolved_sender_prefix=unresolved_sender_prefix,
        ),
        zammad=ZammadConfig(
            url="https://z", token="t", group="Users", new_customer_group="Triage",
            phone_field="mobile", overflow_priority=3,
            group_from_last_ticket=group_from_last_ticket,
        ),
        ticket_to_sms=TicketToSmsConfig(
            max_sms_parts=3,
            max_sms_per_hour=20,
            max_sms_per_24h=100,
            stats_db_file="/nonexistent/should-not-be-used.json",
            budget_notify_cooldown_minutes=60,
        ),
        notification=None,
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


def test_new_customer_creates_ticket_in_triage_group():
    teltonika = FakeTeltonika([SmsMessage(index=1, sender="0151 12345678", text="Hallo")])
    zammad = FakeZammad()
    budget = FakeBudget()

    sms_to_ticket.run(teltonika, zammad, _config(), dry_run=False, budget=budget)

    assert zammad.created_tickets == [(1000, "Triage", "Neues SMS-Ticket: Hallo", "Hallo")]
    assert teltonika.deleted == [1]
    # neuer Kunde -> Gruppenname direkt bekannt (kein Zammad-Lookup noetig)
    assert budget.received == [("Triage", None)]


def test_receipt_timestamp_gets_appended_to_body_but_not_subject():
    teltonika = FakeTeltonika(
        [
            SmsMessage(
                index=9,
                sender="0151 12345678",
                text="Hallo",
                timestamp="Sat Oct 26 19:04:11 2024",
            )
        ]
    )
    zammad = FakeZammad()

    sms_to_ticket.run(teltonika, zammad, _config(), dry_run=False, budget=FakeBudget())

    customer_id, group, subject, body = zammad.created_tickets[0]
    assert subject == "Neues SMS-Ticket: Hallo"
    assert body == "Hallo\n---\nSMS-Empfang: Sat Oct 26 19:04:11 2024"


def test_subject_excerpt_keeps_short_text_unchanged():
    assert _subject_excerpt("Hallo") == "Hallo"


def test_subject_excerpt_keeps_exactly_fifty_chars_unchanged():
    text = "x" * 50
    assert _subject_excerpt(text) == text


def test_subject_excerpt_truncates_longer_text_to_46_chars_plus_marker():
    text = "x" * 60
    excerpt = _subject_excerpt(text)
    assert excerpt == "x" * 46 + "[..]"
    assert len(excerpt) == 50


def test_new_ticket_subject_gets_truncated_for_long_message():
    long_text = "Dies ist eine sehr lange SMS die definitiv laenger als fuenfzig Zeichen ist"
    teltonika = FakeTeltonika([SmsMessage(index=3, sender="0151 12345678", text=long_text)])
    zammad = FakeZammad()

    sms_to_ticket.run(teltonika, zammad, _config(), dry_run=False, budget=FakeBudget())

    _, _, subject, _ = zammad.created_tickets[0]
    assert subject == f"Neues SMS-Ticket: {long_text[:46]}[..]"


def test_known_customer_without_open_ticket_uses_normal_group():
    teltonika = FakeTeltonika([SmsMessage(index=2, sender="0151 12345678", text="Hallo")])
    zammad = FakeZammad(existing_customers={"+4915112345678": 42})

    sms_to_ticket.run(teltonika, zammad, _config(), dry_run=False, budget=FakeBudget())

    assert zammad.created_tickets == [(42, "Users", "Neues SMS-Ticket: Hallo", "Hallo")]
    assert teltonika.deleted == [2]


def test_known_customer_without_open_ticket_uses_last_ticket_group_when_enabled():
    """Kundenzentrisch arbeitende Teams: neues Ticket landet in der Queue
    des zuletzt kontaktierten (auch laengst geschlossenen) Tickets, nicht
    in der festen Default-Gruppe."""

    class LastTicket:
        id = 9
        number = "1009"

    teltonika = FakeTeltonika([SmsMessage(index=2, sender="0151 12345678", text="Hallo")])
    zammad = FakeZammad(
        existing_customers={"+4915112345678": 42},
        last_ticket=LastTicket(),
        ticket_groups={9: 77},
    )

    sms_to_ticket.run(
        teltonika, zammad, _config(group_from_last_ticket=True), dry_run=False, budget=FakeBudget()
    )

    assert zammad.created_tickets == [(42, "Gruppe-77", "Neues SMS-Ticket: Hallo", "Hallo")]


def test_known_customer_without_any_ticket_falls_back_to_default_group_even_when_enabled():
    teltonika = FakeTeltonika([SmsMessage(index=2, sender="0151 12345678", text="Hallo")])
    zammad = FakeZammad(existing_customers={"+4915112345678": 42}, last_ticket=None)

    sms_to_ticket.run(
        teltonika, zammad, _config(group_from_last_ticket=True), dry_run=False, budget=FakeBudget()
    )

    assert zammad.created_tickets == [(42, "Users", "Neues SMS-Ticket: Hallo", "Hallo")]


def test_inaccessible_last_ticket_group_falls_back_to_default_group():
    """Regression: hat der SMSammad-API-User keinen Zugriff auf die Gruppe
    des letzten Tickets (HTTP 403 -- z.B. weil ein Agent das Ticket manuell
    in eine dem API-User nie freigeschaltete Gruppe verschoben hat), darf
    das NICHT crashen, sondern muss auf die feste Default-Gruppe
    zurueckfallen."""

    class LastTicket:
        id = 9
        number = "1009"

    teltonika = FakeTeltonika([SmsMessage(index=2, sender="0151 12345678", text="Hallo")])
    zammad = FakeZammad(
        existing_customers={"+4915112345678": 42},
        last_ticket=LastTicket(),
        raise_group_error=True,
    )

    sms_to_ticket.run(
        teltonika, zammad, _config(group_from_last_ticket=True), dry_run=False, budget=FakeBudget()
    )

    assert zammad.created_tickets == [(42, "Users", "Neues SMS-Ticket: Hallo", "Hallo")]


def test_new_customer_ignores_group_from_last_ticket_setting():
    teltonika = FakeTeltonika([SmsMessage(index=1, sender="0151 12345678", text="Hallo")])
    zammad = FakeZammad()

    sms_to_ticket.run(
        teltonika, zammad, _config(group_from_last_ticket=True), dry_run=False, budget=FakeBudget()
    )

    assert zammad.created_tickets == [(1000, "Triage", "Neues SMS-Ticket: Hallo", "Hallo")]


def test_open_ticket_gets_article_instead_of_new_ticket():
    class Ticket:
        id = 9
        number = "1009"

    teltonika = FakeTeltonika([SmsMessage(index=3, sender="0151 12345678", text="Hallo")])
    zammad = FakeZammad(existing_customers={"+4915112345678": 42}, open_ticket=Ticket())
    budget = FakeBudget()

    sms_to_ticket.run(teltonika, zammad, _config(), dry_run=False, budget=budget)

    assert zammad.created_tickets == []
    assert zammad.added_articles == [(9, "Hallo", False)]
    assert teltonika.deleted == [3]
    # offenes Ticket -> Gruppenname per get_ticket/get_group_name aufgeloest
    # (FakeZammad.get_ticket liefert group_id=42, get_group_name "Gruppe-42")
    assert budget.received == [("Gruppe-42", "1009")]


def test_dry_run_deletes_nothing():
    teltonika = FakeTeltonika([SmsMessage(index=4, sender="0151 12345678", text="Hallo")])
    zammad = FakeZammad()

    sms_to_ticket.run(teltonika, zammad, _config(), dry_run=True, budget=FakeBudget())

    assert zammad.created_tickets == []
    assert teltonika.deleted == []


def test_dry_run_logs_last_ticket_group_when_enabled(caplog):
    class LastTicket:
        id = 9
        number = "1009"

    teltonika = FakeTeltonika([SmsMessage(index=2, sender="0151 12345678", text="Hallo")])
    zammad = FakeZammad(
        existing_customers={"+4915112345678": 42},
        last_ticket=LastTicket(),
        ticket_groups={9: 77},
    )

    with caplog.at_level("INFO"):
        sms_to_ticket.run(
            teltonika, zammad, _config(group_from_last_ticket=True), dry_run=True, budget=FakeBudget()
        )

    assert "Gruppe-77" in caplog.text
    assert zammad.created_tickets == []


def test_dry_run_log_does_not_leak_sms_content(caplog):
    """Nur Rufnummern duerfen im Klartext geloggt werden, der SMS-Inhalt
    muss maskiert sein (Log landet in /var/log, siehe cron_run.sh)."""
    secret_text = "Geheimer Nachrichteninhalt den niemand sehen soll"
    teltonika = FakeTeltonika([SmsMessage(index=10, sender="0151 12345678", text=secret_text)])
    zammad = FakeZammad()

    with caplog.at_level("INFO"):
        sms_to_ticket.run(teltonika, zammad, _config(), dry_run=True, budget=FakeBudget())

    log_text = caplog.text
    assert secret_text not in log_text
    # die sichtbaren ersten 5 Zeichen sind ROT13-verschluesselt, nicht
    # im Klartext -- sonst waeren sie beim Ueberfliegen des Logs lesbar.
    assert secret_text[:5] not in log_text
    assert codecs.encode(secret_text[:5], "rot_13") in log_text


def test_dry_run_does_not_create_a_real_customer():
    """Regression: find_or_create_customer_by_phone legt bei unbekanntem
    Absender einen echten Zammad-Kunden an -- im Dry-Run darf nur die
    rein lesende find_customer_by_phone-Suche aufgerufen werden."""
    teltonika = FakeTeltonika([SmsMessage(index=8, sender="0151 12345678", text="Hallo")])
    zammad = FakeZammad()

    sms_to_ticket.run(teltonika, zammad, _config(), dry_run=True, budget=FakeBudget())

    assert zammad.customers_created == []


def test_short_code_sender_falls_back_to_raw_string():
    teltonika = FakeTeltonika([SmsMessage(index=5, sender="22543", text="Systemnachricht")])
    zammad = FakeZammad()

    sms_to_ticket.run(teltonika, zammad, _config(), dry_run=False, budget=FakeBudget())

    assert zammad.created_tickets == [
        (1000, "Triage", "Neues SMS-Ticket: Systemnachricht", "Systemnachricht")
    ]
    assert teltonika.deleted == [5]


def test_short_number_gets_reconstructed_with_configured_prefix():
    teltonika = FakeTeltonika([SmsMessage(index=6, sender="1234567", text="Hallo")])
    zammad = FakeZammad()

    sms_to_ticket.run(teltonika, zammad, _config(short_number_prefix="0172"), dry_run=False, budget=FakeBudget())

    assert zammad.created_tickets == [(1000, "Triage", "Neues SMS-Ticket: Hallo", "Hallo")]
    assert teltonika.deleted == [6]


def test_resolve_sender_id_direct_match():
    sender_id, is_valid = _resolve_sender_id("0151 12345678", "DE", "", "")
    assert (sender_id, is_valid) == ("+4915112345678", True)


def test_resolve_sender_id_plus49_direct_match():
    sender_id, is_valid = _resolve_sender_id("+4915112345678", "DE", "", "")
    assert (sender_id, is_valid) == ("+4915112345678", True)


def test_resolve_sender_id_rejects_short_code_phonenumbers_would_accept():
    """Regression: phonenumbers haelt '224466' faelschlich fuer eine
    gueltige 6-stellige deutsche Rufnummer -- ohne '+49'/'01'-Praefix darf
    das nicht als echte Nummer durchgehen, sondern muss als Kurzwahl
    behandelt werden."""
    sender_id, is_valid = _resolve_sender_id("224466", "DE", "0172", "Kurzwahl:")
    assert (sender_id, is_valid) == ("Kurzwahl:224466", False)


def test_resolve_sender_id_uses_prefix_when_direct_fails():
    sender_id, is_valid = _resolve_sender_id("1234567", "DE", "0172", "")
    assert (sender_id, is_valid) == ("+491721234567", True)


def test_resolve_sender_id_falls_back_to_raw_when_nothing_works():
    sender_id, is_valid = _resolve_sender_id("22543", "DE", "0172", "")
    assert (sender_id, is_valid) == ("22543", False)


def test_resolve_sender_id_uses_unresolved_sender_prefix_when_configured():
    sender_id, is_valid = _resolve_sender_id("22543", "DE", "0172", "Kurzwahl:")
    assert (sender_id, is_valid) == ("Kurzwahl:22543", False)


def test_resolve_sender_id_handles_alphanumeric_sender_id():
    """Alphanumerische Absender-IDs wie 'CALLYA' sind gar keine Ziffernfolge
    -- muessen trotzdem sauber in 'Kurzwahl:CALLYA' resultieren."""
    sender_id, is_valid = _resolve_sender_id("CALLYA", "DE", "0172", "Kurzwahl:")
    assert (sender_id, is_valid) == ("Kurzwahl:CALLYA", False)


def test_short_code_sender_gets_prefixed_when_configured():
    teltonika = FakeTeltonika([SmsMessage(index=7, sender="22543", text="Systemnachricht")])
    zammad = FakeZammad()

    sms_to_ticket.run(
        teltonika, zammad, _config(unresolved_sender_prefix="Kurzwahl:"), dry_run=False, budget=FakeBudget()
    )

    assert zammad.created_tickets == [
        (1000, "Triage", "Neues SMS-Ticket: Systemnachricht", "Systemnachricht")
    ]
    assert teltonika.deleted == [7]


_BALANCE_TEXT_OK = (
    "Hallo, Dein Guthaben beträgt 6,00 Euro. Lad es bitte wieder in Deiner App "
    "oder unter 22922 auf. Freundliche Grüße, Dein Vodafone-Team"
)
_BALANCE_TEXT_WARN = _BALANCE_TEXT_OK.replace("6,00", "3,00")
_BALANCE_TEXT_ALARM = _BALANCE_TEXT_OK.replace("6,00", "0,50")


def test_balance_reply_ok_closes_ticket():
    teltonika = FakeTeltonika([SmsMessage(index=1, sender="80808", text=_BALANCE_TEXT_OK)])
    zammad = FakeZammad()
    budget = FakeBudget()

    sms_to_ticket.run(
        teltonika, zammad, _config(balance=_balance_config()), dry_run=False, budget=budget
    )

    assert zammad.created_tickets == [(1000, "Triage", "SMS-Guthaben", _BALANCE_TEXT_OK)]
    assert zammad.subjects == [(555, "SMS-Guthaben")]
    assert zammad.states == [(555, 4)]
    assert zammad.priorities == []
    note = [c for c in zammad.article_calls if c["article_type"] == "note"][0]
    assert "6.00 Euro" in note["body"]
    assert "noch ausreichend" in note["body"]
    assert note["internal"] is True
    assert note["sender"] == "Agent"
    assert budget.balances == [6.0]
    assert budget.received == []  # kein Kundenkontakt-Zaehler fuer System-Traffic
    assert teltonika.deleted == [1]


def test_balance_reply_warn_keeps_open_with_priority_2():
    teltonika = FakeTeltonika([SmsMessage(index=1, sender="80808", text=_BALANCE_TEXT_WARN)])
    zammad = FakeZammad()
    budget = FakeBudget()

    sms_to_ticket.run(
        teltonika, zammad, _config(balance=_balance_config()), dry_run=False, budget=budget
    )

    assert zammad.subjects == [(555, "SMS Guthaben sollte aufgeladen werden")]
    assert zammad.priorities == [(555, 2)]
    assert zammad.states == []
    note = [c for c in zammad.article_calls if c["article_type"] == "note"][0]
    assert "3.00 Euro" in note["body"]
    assert budget.balances == [3.0]


def test_balance_reply_alarm_keeps_open_with_priority_3():
    teltonika = FakeTeltonika([SmsMessage(index=1, sender="80808", text=_BALANCE_TEXT_ALARM)])
    zammad = FakeZammad()
    budget = FakeBudget()

    sms_to_ticket.run(
        teltonika, zammad, _config(balance=_balance_config()), dry_run=False, budget=budget
    )

    assert zammad.subjects == [(555, "SMS-Guthaben KRITISCH niedrig - SMS-Versand gefaehrdet")]
    assert zammad.priorities == [(555, 3)]
    assert zammad.states == []
    assert budget.balances == [0.5]


def test_balance_reply_on_existing_open_ticket_appends_and_updates_fields():
    class Ticket:
        id = 9
        number = "1009"

    teltonika = FakeTeltonika([SmsMessage(index=1, sender="80808", text=_BALANCE_TEXT_WARN)])
    zammad = FakeZammad(open_ticket=Ticket())
    budget = FakeBudget()

    sms_to_ticket.run(
        teltonika, zammad, _config(balance=_balance_config()), dry_run=False, budget=budget
    )

    assert zammad.created_tickets == []
    assert (9, _BALANCE_TEXT_WARN, False) in zammad.added_articles
    assert zammad.subjects == [(9, "SMS Guthaben sollte aufgeladen werden")]
    assert zammad.priorities == [(9, 2)]


def test_balance_reply_parse_failure_falls_through_to_normal_ticket():
    """Regression: die gleiche Kurzwahl (z.B. Vodafones '80808') verschickt
    auch andere automatische Nachrichten (Vertragsaenderungen etc.), keine
    Guthaben-Antworten. Fehlt ein erkennbarer Betrag, darf das NICHT
    crashen (liess die SMS sonst dauerhaft auf dem Router stehen, jeder
    folgende Lauf crashte erneut) -- stattdessen normale
    Ticket-Erstellung wie bei jeder anderen SMS."""
    unparseable = "Hallo, aktuell keine Information zu Ihrem Vertrag verfuegbar."
    teltonika = FakeTeltonika([SmsMessage(index=1, sender="80808", text=unparseable)])
    zammad = FakeZammad()
    budget = FakeBudget()

    sms_to_ticket.run(
        teltonika, zammad, _config(balance=_balance_config()), dry_run=False, budget=budget
    )

    assert teltonika.deleted == [1]
    assert zammad.created_tickets
    assert zammad.created_tickets[0][2].startswith("Neues SMS-Ticket:")
    assert budget.balances == []


def test_balance_not_configured_message_runs_through_generic_flow():
    """Ohne [balance]-Sektion muss eine SMS vom potenziellen Guthaben-Absender
    ganz normal wie jede andere SMS behandelt werden."""
    teltonika = FakeTeltonika([SmsMessage(index=1, sender="80808", text=_BALANCE_TEXT_OK)])
    zammad = FakeZammad()
    budget = FakeBudget()

    sms_to_ticket.run(teltonika, zammad, _config(balance=None), dry_run=False, budget=budget)

    assert zammad.created_tickets
    assert zammad.created_tickets[0][2].startswith("Neues SMS-Ticket:")
    assert budget.balances == []


def test_balance_reply_uses_configurable_regex_for_changed_provider_wording():
    """Simuliert einen geaenderten Provider-Wortlaut -- muss allein durch
    Aendern von config.balance.sms_balance_regex funktionieren, ohne
    Code-Anpassung."""
    text = "Ihr Saldo: 7,25 Euro. Vielen Dank fuer Ihre Treue!"
    teltonika = FakeTeltonika([SmsMessage(index=1, sender="80808", text=text)])
    zammad = FakeZammad()
    budget = FakeBudget()
    balance_config = _balance_config(sms_balance_regex=r"Saldo:\s*(\d+(?:,\d+)?)\s*Euro")

    sms_to_ticket.run(teltonika, zammad, _config(balance=balance_config), dry_run=False, budget=budget)

    assert budget.balances == [7.25]


def test_balance_reply_dry_run_makes_no_changes():
    teltonika = FakeTeltonika([SmsMessage(index=1, sender="80808", text=_BALANCE_TEXT_OK)])
    zammad = FakeZammad()
    budget = FakeBudget()

    sms_to_ticket.run(
        teltonika, zammad, _config(balance=_balance_config()), dry_run=True, budget=budget
    )

    assert zammad.created_tickets == []
    assert zammad.customers_created == []
    assert budget.balances == []
    assert teltonika.deleted == []
