import codecs
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from smsammad import ticket_to_sms
from smsammad.config import (
    BalanceConfig,
    Config,
    TeltonikaConfig,
    TicketToSmsConfig,
    ZammadConfig,
)
from smsammad.sms_budget import GroupStat
from smsammad.teltonika import TeltonikaError


class FakeZammad:
    def __init__(self, tickets, users, articles, initial_tags=None):
        self._tickets = tickets
        self._users = users
        self._articles = articles
        self._tags = {tid: set(v) for tid, v in (initial_tags or {}).items()}
        self.sent_tags_added = []
        self.sent_tags_removed = []
        self.internal_notes = []
        self.priorities_set = []
        self.states_set = []

    def search_tickets_by_tag(self, tag):
        assert tag == "sms-out"
        return list(self._tickets.keys())

    def get_ticket(self, ticket_id):
        return self._tickets[ticket_id]

    def get_user(self, user_id):
        return self._users[user_id]

    def get_ticket_articles(self, ticket_id):
        return self._articles[ticket_id]

    def add_tag(self, ticket_id, tag):
        self.sent_tags_added.append((ticket_id, tag))
        self._tags.setdefault(ticket_id, set()).add(tag)

    def remove_tag(self, ticket_id, tag):
        self.sent_tags_removed.append((ticket_id, tag))
        self._tags.setdefault(ticket_id, set()).discard(tag)

    def get_tags(self, ticket_id):
        return list(self._tags.get(ticket_id, set()))

    def add_article(self, ticket_id, body, internal=False, article_type="phone", sender="Customer"):
        self.internal_notes.append((ticket_id, body, internal, article_type, sender))

    def set_priority(self, ticket_id, priority_id):
        self.priorities_set.append((ticket_id, priority_id))

    def set_state(self, ticket_id, state_id):
        self.states_set.append((ticket_id, state_id))

    def get_group_name(self, group_id):
        return f"Gruppe-{group_id}"


class FakeTeltonika:
    def __init__(self, fail_with=None):
        self.sent = []
        self._fail_with = fail_with

    def send(self, number, text):
        if self._fail_with is not None:
            raise self._fail_with
        self.sent.append((number, text))


class FakeBudget:
    """Unbegrenztes Budget per Default; ueber has_capacity steuerbar."""

    def __init__(
        self, has_capacity=True, notify=True, next_available=None, breakdown=None, latest_balance=None
    ):
        self._has_capacity = has_capacity
        self._notify = notify
        self._next_available = next_available or datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._breakdown = breakdown or []
        self._latest_balance = latest_balance
        self.recorded = []
        self.record_sent_calls = []
        self.notified = False

    def latest_balance(self):
        return self._latest_balance

    def can_send(self, n):
        return self._has_capacity

    def record_sent(self, n, group=None, agent=None, ticket_number=None, now=None):
        self.recorded.append(n)
        self.record_sent_calls.append((n, group, agent, ticket_number))

    def status(self):
        class S:
            sent_last_hour = 5
            sent_last_24h = 10
            max_per_hour = 5
            max_per_24h = 100

        return S()

    def next_available_at(self, n):
        return self._next_available

    def should_notify(self, cooldown_minutes):
        return self._notify

    def mark_notified(self):
        self.notified = True

    def summary_by_group_and_agent(self, since, direction="out"):
        return self._breakdown


def _public_call(body):
    """Fake-Artikel im Format, das ticket_to_sms als SMS-Quelle akzeptiert:
    oeffentlicher 'Anruf'-Artikel vom Agenten."""
    return {"body": body, "type": "phone", "internal": False, "sender": "Agent"}


def _customer_call(body):
    """Fake-Artikel fuer eine eingehende SMS (Kunde, oeffentlich, Anruf) --
    darf NICHT als SMS-Quelle fuer den Rueckweg akzeptiert werden."""
    return {"body": body, "type": "phone", "internal": False, "sender": "Customer"}


def _config(max_sms_parts=3, unresolved_sender_prefix="", on_overflow="reject", balance=None):
    return Config(
        teltonika=TeltonikaConfig(
            host="h",
            username="u",
            password="p",
            default_country_code="DE",
            unresolved_sender_prefix=unresolved_sender_prefix,
        ),
        zammad=ZammadConfig(
            url="https://z", token="t", group="Users", new_customer_group="Triage",
            phone_field="mobile", overflow_priority=3,
        ),
        ticket_to_sms=TicketToSmsConfig(
            max_sms_parts=max_sms_parts,
            max_sms_per_hour=20,
            max_sms_per_24h=100,
            stats_db_file="/nonexistent/should-not-be-used.json",
            budget_notify_cooldown_minutes=60,
            on_overflow=on_overflow,
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


def test_sends_and_retags_on_success():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("<p>Kurzer Text</p>")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert teltonika.sent == [("004915112345678", "Kurzer Text")]
    assert zammad.sent_tags_removed == [(1, "sms-out")]
    assert zammad.sent_tags_added == [(1, "sms-sent")]
    assert budget.recorded == [1]
    assert len(zammad.internal_notes) == 1
    note_ticket_id, note_body, note_internal, note_type, note_sender = zammad.internal_notes[0]
    assert note_ticket_id == 1
    assert note_internal is True
    assert "1 SMS" in note_body
    assert "an den Router uebergeben" in note_body
    # exakter gesendeter Text muss in der Notiz stehen (Sanity-Check bei
    # Zeichenkonvertierung/Emoji), plus Zeichenzahl des Originaltexts.
    assert "11 Zeichen" in note_body  # "Kurzer Text" hat 11 Zeichen
    assert 'Gesendeter Text:\n"Kurzer Text"' in note_body
    # type/sender bewusst NICHT "phone"/"Customer": sonst koennte ein
    # Zammad-Trigger auf unsere eigene Quittungs-Notiz erneut feuern.
    assert note_type == "note"
    assert note_sender == "Agent"


def test_record_sent_includes_resolved_group_and_agent():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7, "group_id": 16}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [{**_public_call("Antwort"), "from": "Andreas Dorfer"}]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert budget.record_sent_calls == [(1, "Gruppe-16", "Andreas Dorfer", "1001")]


def test_success_removes_stray_budget_wait_tag():
    """Nach einem endlich erfolgreichen Versand muss ein vorher gesetzter
    Budget-Wartehinweis-Tag entfernt werden, damit ein spaeterer erneuter
    Engpass wieder eine frische Notiz bekommt."""
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
        initial_tags={1: {"sms-out", "sms-budget-warten"}},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert (1, "sms-budget-warten") in zammad.sent_tags_removed
    assert zammad.get_tags(1) == ["sms-sent"]


def test_dry_run_adds_no_internal_note():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=True, budget=budget)

    assert zammad.internal_notes == []


def test_multi_part_send_note_mentions_part_count():
    text = " ".join(["wort"] * 60)
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call(text)]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(max_sms_parts=10), dry_run=False, budget=budget)

    assert len(teltonika.sent) > 1
    note_body = zammad.internal_notes[0][1]
    assert f"{len(teltonika.sent)} SMS-Teile" in note_body
    for _, sent_part in teltonika.sent:
        assert f'"{sent_part}"' in note_body


def test_ignores_trailing_internal_note():
    """Regression: ein interner Kommentar NACH der oeffentlichen Anruf-Notiz
    (z.B. an einen Kollegen gerichtet) darf nicht als SMS-Inhalt genommen
    werden, nur weil er zeitlich der letzte Artikel ist."""
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={
            1: [
                _public_call("Oeffentliche Antwort"),
                {"body": "Interner Kommentar fuer Kollegen", "type": "note", "internal": True},
            ]
        },
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert teltonika.sent == [("004915112345678", "Oeffentliche Antwort")]


def test_fails_when_no_public_call_article_exists():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [{"body": "Nur intern", "type": "note", "internal": True}]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    with pytest.raises(RuntimeError):
        ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert teltonika.sent == []


def test_does_not_echo_customers_own_inbound_message_back():
    """Regression: bei einem neu angelegten Ticket ist der einzige
    oeffentliche Anruf-Artikel oft die eingehende Kundennachricht selbst
    (sender='Customer'). Auch wenn ein (zu weit gefasster) Zammad-Trigger
    darauf faelschlich 'sms-out' setzt, darf diese Nachricht NICHT an den
    Kunden zurueckgeschickt werden."""
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_customer_call("Hallo, das ist meine SMS")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    with pytest.raises(RuntimeError):
        ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert teltonika.sent == []


def test_agent_reply_after_customer_message_still_gets_sent():
    """Normalfall: eingehende Kundennachricht, danach echte oeffentliche
    Agenten-Antwort im Anruf-Tab -- die Agenten-Antwort muss trotz der
    vorangehenden Kundennachricht gefunden und gesendet werden."""
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={
            1: [
                _customer_call("Hallo, das ist meine SMS"),
                _public_call("Antwort vom Agenten"),
            ]
        },
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert teltonika.sent == [("004915112345678", "Antwort vom Agenten")]


def test_dry_run_sends_nothing():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=True, budget=budget)

    assert teltonika.sent == []
    assert zammad.sent_tags_removed == []
    assert zammad.sent_tags_added == []
    assert budget.recorded == []


def test_dry_run_log_does_not_leak_sms_content(caplog):
    secret_text = "Geheimer Antworttext den niemand sehen soll"
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call(secret_text)]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    with caplog.at_level("INFO"):
        ticket_to_sms.run(zammad, teltonika, _config(), dry_run=True, budget=budget)

    log_text = caplog.text
    assert secret_text not in log_text
    # die sichtbaren ersten 5 Zeichen sind ROT13-verschluesselt, nicht
    # im Klartext -- sonst waeren sie beim Ueberfliegen des Logs lesbar.
    assert secret_text[:5] not in log_text
    assert codecs.encode(secret_text[:5], "rot_13") in log_text


def test_overflow_sets_tag_and_note_instead_of_sending():
    long_text = " ".join(["wort"] * 200)
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call(long_text)]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(max_sms_parts=1), dry_run=False, budget=budget)

    assert teltonika.sent == []
    assert zammad.sent_tags_removed == [(1, "sms-out")]
    assert zammad.sent_tags_added == [(1, "sms-overflow"), (1, "sms-cannotsend")]
    assert len(zammad.internal_notes) == 1
    assert zammad.internal_notes[0][2] is True
    assert zammad.internal_notes[0][3] == "note"
    assert zammad.internal_notes[0][4] == "Agent"
    assert zammad.priorities_set == [(1, 3)]
    assert budget.recorded == []


def test_overflow_truncate_mode_sends_first_max_parts():
    long_text = " ".join(["wort"] * 200)
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call(long_text)]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(
        zammad, teltonika, _config(max_sms_parts=2, on_overflow="truncate"), dry_run=False, budget=budget
    )

    assert len(teltonika.sent) == 2
    assert zammad.sent_tags_removed == [(1, "sms-out")]
    assert zammad.sent_tags_added == [(1, "sms-sent")]
    assert zammad.priorities_set == []
    assert budget.recorded == [2]
    note_body = zammad.internal_notes[0][1]
    assert "ACHTUNG: Text war zu lang" in note_body
    assert "Nur die ersten 2 Teile wurden gesendet" in note_body
    assert "2 SMS-Teile" in note_body
    # der tatsaechlich gesendete (gekuerzte) Text muss in der Notiz stehen
    sent_texts = [call[1] for call in teltonika.sent]
    for part in sent_texts:
        assert part in note_body


def test_overflow_reject_mode_is_default():
    long_text = " ".join(["wort"] * 200)
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call(long_text)]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(max_sms_parts=2), dry_run=False, budget=budget)

    assert teltonika.sent == []
    assert zammad.sent_tags_added == [(1, "sms-overflow"), (1, "sms-cannotsend")]


def test_missing_mobile_falls_back_to_phone_field_if_it_is_mobile():
    """Feld 'mobile' leer, aber 'phone' enthaelt tatsaechlich eine
    Mobilfunknummer -- soll trotzdem gesendet werden."""
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "", "phone": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert teltonika.sent == [("004915112345678", "Text")]
    assert zammad.sent_tags_added == [(1, "sms-sent")]


def test_missing_mobile_and_phone_field_is_landline_marks_cannot_send():
    """Feld 'mobile' leer, 'phone' enthaelt eine echte Festnetznummer --
    kann keine SMS empfangen, darf also NICHT als Sendeziel genutzt werden."""
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "", "phone": "030 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert teltonika.sent == []
    assert zammad.sent_tags_removed == [(1, "sms-out")]
    assert zammad.sent_tags_added == [(1, "sms-cannotsend")]
    assert len(zammad.internal_notes) == 1
    note_body = zammad.internal_notes[0][1]
    assert "SMS-Versand nicht moeglich: \n" in note_body
    assert "weder im Feld 'mobile', noch im Feld 'phone'" in note_body
    assert "eine erkennbare" in note_body
    assert zammad.priorities_set == [(1, 3)]


def test_missing_mobile_and_no_phone_field_marks_cannot_send():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": ""}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert teltonika.sent == []
    assert zammad.sent_tags_added == [(1, "sms-cannotsend")]
    assert zammad.priorities_set == [(1, 3)]


def test_cannot_send_reopens_closed_ticket():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7, "state_id": 4}},
        users={7: {"id": 7, "mobile": ""}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert zammad.states_set == [(1, 2)]


def test_cannot_send_reopens_pending_ticket():
    """'Warten auf Rueckmeldung'/'pending close' o.ae. -- jeder Zustand
    ausser 'offen' muss reaktiviert werden, nicht nur 'geschlossen'."""
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7, "state_id": 8}},
        users={7: {"id": 7, "mobile": ""}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert zammad.states_set == [(1, 2)]


def test_cannot_send_does_not_touch_state_when_already_open():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7, "state_id": 2}},
        users={7: {"id": 7, "mobile": ""}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert zammad.states_set == []


def test_missing_mobile_dry_run_adds_no_tag_or_note():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": ""}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=True, budget=budget)

    assert teltonika.sent == []
    assert zammad.sent_tags_added == []
    assert zammad.internal_notes == []


def test_send_failure_marks_cannot_send_instead_of_crashing():
    """Regression: ein Router-/Guthaben-Fehler beim tatsaechlichen Versand
    (z.B. kein SMS-Guthaben mehr) durfte bisher nur still im Log landen,
    Ticket blieb unveraendert mit Tag 'sms-out' fuer endlose stille
    Wiederholversuche stehen. Jetzt: Tag + Vermerk fuer den Agenten,
    kein Crash/RuntimeError."""
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika(fail_with=TeltonikaError("sms_send lieferte HTTP 500: 'no credit'"))
    budget = FakeBudget()

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert zammad.sent_tags_removed == [(1, "sms-out")]
    assert zammad.sent_tags_added == [(1, "sms-cannotsend")]
    assert budget.recorded == []
    assert len(zammad.internal_notes) == 1
    assert "no credit" in zammad.internal_notes[0][1]
    assert zammad.priorities_set == [(1, 3)]


def test_budget_exceeded_leaves_tag_for_retry_and_adds_note_once():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget(has_capacity=False)

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert teltonika.sent == []
    assert zammad.sent_tags_removed == []
    assert zammad.sent_tags_added == [(1, "sms-budget-warten")]
    assert budget.recorded == []
    assert budget.notified is True
    assert len(zammad.internal_notes) == 1
    note_body = zammad.internal_notes[0][1]
    # Zeitzonen-unabhaengig erwarten: die Produktionslogik wandelt den (UTC-)
    # ETA-Zeitpunkt per astimezone() in die lokale Zeitzone der Maschine um
    # -- ein hartkodierter String waere nur in EINER Zeitzone korrekt
    # (frueher: CET/CEST-hartkodiert, brach auf dem UTC-CI-Runner).
    expected_eta = datetime(2026, 1, 1, tzinfo=timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M:%S")
    assert expected_eta in note_body


def test_budget_exceeded_mail_includes_group_agent_breakdown():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget(
        has_capacity=False,
        breakdown=[GroupStat("out", "Users", "Andreas Dorfer", 12)],
    )

    with patch("smsammad.ticket_to_sms.send_mail") as send_mail_mock:
        ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    send_mail_mock.assert_called_once()
    body = send_mail_mock.call_args[0][2]
    assert "Users" in body
    assert "Andreas Dorfer" in body
    assert "12" in body


def test_budget_exceeded_does_not_duplicate_note_on_repeated_polls():
    """Regression: solange das Budget erschoepft bleibt, darf nur beim
    ERSTEN Mal eine Hinweis-Notiz entstehen, nicht bei jedem Cronlauf."""
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
        initial_tags={1: {"sms-out", "sms-budget-warten"}},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget(has_capacity=False)

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert zammad.internal_notes == []
    assert zammad.sent_tags_added == []


def test_budget_exceeded_respects_notify_cooldown():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget(has_capacity=False, notify=False)

    ticket_to_sms.run(zammad, teltonika, _config(), dry_run=False, budget=budget)

    assert budget.notified is False


def test_unresolved_sender_prefix_gets_unwrapped_to_raw_short_number():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "Kurzwahl:22543"}},
        articles={1: [_public_call("Antwort")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(
        zammad,
        teltonika,
        _config(unresolved_sender_prefix="Kurzwahl:"),
        dry_run=False,
        budget=budget,
    )

    assert teltonika.sent == [("22543", "Antwort")]
    assert zammad.sent_tags_added == [(1, "sms-sent")]


def test_unresolved_sender_prefix_unwraps_alphanumeric_sender_id():
    """Antwort an eine alphanumerische Absender-ID wie 'CALLYA' (z.B. der
    Netzbetreiber-Systemabsender) muss ebenfalls unverfaelscht roh
    rausgehen."""
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "Kurzwahl:CALLYA"}},
        articles={1: [_public_call("Antwort")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget()

    ticket_to_sms.run(
        zammad,
        teltonika,
        _config(unresolved_sender_prefix="Kurzwahl:"),
        dry_run=False,
        budget=budget,
    )

    assert teltonika.sent == [("CALLYA", "Antwort")]


def test_send_note_gets_alarm_hint_when_balance_critically_low():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget(latest_balance=(datetime.now(timezone.utc), 0.5))

    ticket_to_sms.run(
        zammad, teltonika, _config(balance=_balance_config()), dry_run=False, budget=budget
    )

    note_body = zammad.internal_notes[0][1]
    assert "SMS-Guthaben ist sehr niedrig, SMS wurde evtl. nicht gesendet" in note_body


def test_send_note_has_no_alarm_hint_when_balance_sufficient():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget(latest_balance=(datetime.now(timezone.utc), 6.0))

    ticket_to_sms.run(
        zammad, teltonika, _config(balance=_balance_config()), dry_run=False, budget=budget
    )

    note_body = zammad.internal_notes[0][1]
    assert "SMS-Guthaben ist sehr niedrig" not in note_body


def test_send_note_has_no_alarm_hint_when_balance_not_configured():
    zammad = FakeZammad(
        tickets={1: {"id": 1, "number": "1001", "customer_id": 7}},
        users={7: {"id": 7, "mobile": "0151 12345678"}},
        articles={1: [_public_call("Text")]},
    )
    teltonika = FakeTeltonika()
    budget = FakeBudget(latest_balance=(datetime.now(timezone.utc), 0.1))

    ticket_to_sms.run(zammad, teltonika, _config(balance=None), dry_run=False, budget=budget)

    note_body = zammad.internal_notes[0][1]
    assert "SMS-Guthaben ist sehr niedrig" not in note_body
