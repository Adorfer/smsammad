import json
from unittest.mock import patch

import pytest
import responses

from smsammad.config import ZammadConfig
from smsammad.zammad import ZammadClient, ZammadError

BASE = "https://zammad.example.local/api/v1"


@pytest.fixture(autouse=True)
def no_real_sleep():
    """Der Retry-Fallback in find_or_create_customer_by_phone wartet
    zwischen Versuchen echt (Indexierungs-Verzoegerung) -- in Tests nicht
    noetig/gewuenscht."""
    with patch("smsammad.zammad.time.sleep"):
        yield


@pytest.fixture
def config():
    return ZammadConfig(
        url="https://zammad.example.local",
        token="tok",
        group="Users",
        new_customer_group="Triage",
        phone_field="mobile",
        overflow_priority=3,
    )


@pytest.fixture
def client(config):
    return ZammadClient(config)


@responses.activate
def test_find_existing_customer(client):
    responses.add(
        responses.GET, f"{BASE}/users/search", json=[{"id": 42, "mobile": "+4915112345678"}]
    )
    customer_id, was_created = client.find_or_create_customer_by_phone("+4915112345678", "DE")
    assert (customer_id, was_created) == (42, False)


@responses.activate
def test_find_existing_customer_with_differently_formatted_number(client):
    """Regression: Zammad speichert, was der Mensch eingetippt hat
    ("+49 172 1234567" mit Leerzeichen) -- muss trotzdem gegen die
    normalisierte Zielnummer matchen."""
    responses.add(
        responses.GET, f"{BASE}/users/search", json=[{"id": 55, "mobile": "+49 172 1234567"}]
    )
    customer_id = client.find_customer_by_phone("+491721234567", "DE")
    assert customer_id == 55


@responses.activate
def test_candidate_with_unrelated_number_is_not_matched(client):
    responses.add(
        responses.GET, f"{BASE}/users/search", json=[{"id": 55, "mobile": "+4915199999999"}]
    )
    assert client.find_customer_by_phone("+491721234567", "DE") is None


@responses.activate
def test_create_customer_when_not_found(client):
    responses.add(responses.GET, f"{BASE}/users/search", json=[])
    responses.add(responses.POST, f"{BASE}/users", json={"id": 99})
    customer_id, was_created = client.find_or_create_customer_by_phone("+4915112345678", "DE")
    assert (customer_id, was_created) == (99, True)


@responses.activate
def test_create_customer_uses_human_readable_number_and_sms_names(client):
    responses.add(responses.GET, f"{BASE}/users/search", json=[])
    responses.add(responses.POST, f"{BASE}/users", json={"id": 99})

    client.find_or_create_customer_by_phone("+4917212344567", "DE")

    payload = json.loads(responses.calls[-1].request.body)
    assert payload["firstname"] == "SMS"
    assert payload["lastname"] == "0172 1234 4567"
    assert payload["mobile"] == "0172 1234 4567"
    assert payload["login"] == "+4917212344567"


@responses.activate
def test_create_customer_keeps_unresolved_sender_id_verbatim(client):
    """Fuer 'Kurzwahl:CALLYA' (keine echte Rufnummer) darf keine
    Human-Readable-Formatierung versucht werden -- der Wert bleibt roh."""
    responses.add(responses.GET, f"{BASE}/users/search", json=[])
    responses.add(responses.POST, f"{BASE}/users", json={"id": 100})

    client.find_or_create_customer_by_phone("Kurzwahl:CALLYA", "DE")

    payload = json.loads(responses.calls[-1].request.body)
    assert payload["lastname"] == "Kurzwahl:CALLYA"
    assert payload["mobile"] == "Kurzwahl:CALLYA"
    assert payload["login"] == "Kurzwahl:CALLYA"


@responses.activate
def test_find_existing_customer_with_alphanumeric_sender_id(client):
    responses.add(
        responses.GET, f"{BASE}/users/search", json=[{"id": 66, "mobile": "Kurzwahl:CALLYA"}]
    )
    assert client.find_customer_by_phone("Kurzwahl:CALLYA", "DE") == 66


@responses.activate
def test_find_existing_customer_with_different_pseudo_id_separator(client):
    """Regression: unresolved_sender_prefix wurde von 'Kurzwahl-' auf
    'Kurzwahl:' umgestellt -- ein Kunde aus der Zeit davor
    ('Kurzwahl-224466') muss trotzdem als Treffer fuer die neue Suche
    ('Kurzwahl:224466') erkannt werden, auch wenn beides keine echte
    Rufnummer ist (to_e164() kann hier nicht normalisieren)."""
    responses.add(
        responses.GET, f"{BASE}/users/search", json=[{"id": 61, "mobile": "Kurzwahl-224466"}]
    )
    assert client.find_customer_by_phone("Kurzwahl:224466", "DE") == 61


@responses.activate
def test_create_race_finds_customer_with_different_pseudo_id_separator(client):
    """Ende-zu-Ende: Anlage kollidiert (Login von einem Kurzwahl-Kunden
    mit altem Trennzeichen existiert schon), erste Suche findet ihn nicht
    (z.B. weil die naive Token-Suche ihn nicht als Kandidaten liefert),
    der Retry-Fallback muss ihn trotzdem finden."""
    responses.add(responses.GET, f"{BASE}/users/search", json=[])
    responses.add(
        responses.POST,
        f"{BASE}/users",
        json={"error": "Login has already been taken", "error_human": "Login has already been taken"},
        status=422,
    )
    responses.add(
        responses.GET, f"{BASE}/users/search", json=[{"id": 61, "mobile": "Kurzwahl-224466"}]
    )

    customer_id, was_created = client.find_or_create_customer_by_phone("Kurzwahl:224466", "DE")

    assert (customer_id, was_created) == (61, False)


@responses.activate
def test_create_race_retry_waits_between_attempts_for_index_lag(client):
    """Erste zwei Nach-Suchen leer (Indexierungs-Verzoegerung), dritte
    findet den Kunden -- muss trotzdem noch als Erfolg durchgehen, nicht
    schon nach dem ersten erfolglosen Retry aufgeben."""
    responses.add(responses.GET, f"{BASE}/users/search", json=[])
    responses.add(
        responses.POST,
        f"{BASE}/users",
        json={"error": "Login has already been taken", "error_human": "Login has already been taken"},
        status=422,
    )
    responses.add(responses.GET, f"{BASE}/users/search", json=[])
    responses.add(responses.GET, f"{BASE}/users/search", json=[])
    responses.add(
        responses.GET, f"{BASE}/users/search", json=[{"id": 61, "mobile": "+4915112345678"}]
    )

    with patch("smsammad.zammad.time.sleep") as sleep_mock:
        customer_id, was_created = client.find_or_create_customer_by_phone("+4915112345678", "DE")

    assert (customer_id, was_created) == (61, False)
    assert sleep_mock.call_count == 2


@responses.activate
def test_find_customer_by_phone_returns_none_when_not_found(client):
    responses.add(responses.GET, f"{BASE}/users/search", json=[])
    assert client.find_customer_by_phone("+4915112345678", "DE") is None


@responses.activate
def test_find_customer_by_phone_multiple_matches_picks_most_recent_contact(client):
    """Mehrere Kunden mit derselben Nummer (z.B. Familie am selben Handy):
    das Ticket muss an den mit dem juengsten Kundenkontakt gehen, nicht
    einfach an den ersten Treffer aus Zammads Suche."""
    responses.add(
        responses.GET,
        f"{BASE}/users/search",
        json=[
            {"id": 10, "mobile": "+491721234567"},
            {"id": 20, "mobile": "+491721234567"},
            {"id": 30, "mobile": "+491721234567"},
        ],
    )
    # Reihenfolge der Ticket-Abfragen folgt der Kandidatenreihenfolge oben.
    responses.add(
        responses.GET,
        f"{BASE}/tickets/search",
        json=[{"id": 1, "number": "1001", "state": "closed", "last_contact_at": "2026-01-01T00:00:00Z"}],
    )
    responses.add(
        responses.GET,
        f"{BASE}/tickets/search",
        json=[{"id": 2, "number": "1002", "state": "open", "last_contact_at": "2026-08-01T00:00:00Z"}],
    )
    responses.add(
        responses.GET,
        f"{BASE}/tickets/search",
        json=[{"id": 3, "number": "1003", "state": "closed", "last_contact_at": "2026-03-01T00:00:00Z"}],
    )

    customer_id = client.find_customer_by_phone("+491721234567", "DE")

    assert customer_id == 20


@responses.activate
def test_find_customer_by_phone_multiple_matches_no_tickets_falls_back_to_first(client):
    """Keiner der mehreren Kandidaten hat je ein Ticket -- dann bleibt es
    beim ersten Treffer aus Zammads Suche (bisheriges Verhalten als
    Fallback), statt None zurueckzugeben."""
    responses.add(
        responses.GET,
        f"{BASE}/users/search",
        json=[
            {"id": 10, "mobile": "+491721234567"},
            {"id": 20, "mobile": "+491721234567"},
        ],
    )
    responses.add(responses.GET, f"{BASE}/tickets/search", json=[])
    responses.add(responses.GET, f"{BASE}/tickets/search", json=[])

    customer_id = client.find_customer_by_phone("+491721234567", "DE")

    assert customer_id == 10


@responses.activate
def test_create_race_retries_search_and_finds_existing(client):
    """Suche findet nichts, Anlage kollidiert (jemand/etwas anderes hat den
    Kunden zwischenzeitlich schon angelegt) -- statt zu scheitern wird noch
    einmal gesucht."""
    responses.add(responses.GET, f"{BASE}/users/search", json=[])
    responses.add(
        responses.POST,
        f"{BASE}/users",
        json={"error": "Login has already been taken", "error_human": "Login has already been taken"},
        status=422,
    )
    responses.add(
        responses.GET, f"{BASE}/users/search", json=[{"id": 77, "mobile": "+4915112345678"}]
    )

    customer_id, was_created = client.find_or_create_customer_by_phone("+4915112345678", "DE")

    assert (customer_id, was_created) == (77, False)


@responses.activate
def test_create_race_reraises_if_retry_search_still_empty(client):
    responses.add(responses.GET, f"{BASE}/users/search", json=[])
    responses.add(
        responses.POST,
        f"{BASE}/users",
        json={"error": "Login has already been taken", "error_human": "Login has already been taken"},
        status=422,
    )
    responses.add(responses.GET, f"{BASE}/users/search", json=[])

    with pytest.raises(ZammadError):
        client.find_or_create_customer_by_phone("+4915112345678", "DE")


@responses.activate
def test_find_open_ticket_picks_newest_last_contact(client):
    responses.add(
        responses.GET,
        f"{BASE}/tickets/search",
        json=[
            {"id": 1, "number": "1001", "state": "open", "last_contact_at": "2026-01-01T00:00:00Z"},
            {"id": 2, "number": "1002", "state": "open", "last_contact_at": "2026-06-01T00:00:00Z"},
            {"id": 3, "number": "1003", "state": "closed", "last_contact_at": "2026-08-01T00:00:00Z"},
        ],
    )
    ticket = client.find_open_ticket_for_customer(42)
    assert ticket.id == 2


@responses.activate
def test_find_open_ticket_none_when_all_closed(client):
    responses.add(
        responses.GET,
        f"{BASE}/tickets/search",
        json=[{"id": 1, "number": "1001", "state": "closed", "last_contact_at": None}],
    )
    assert client.find_open_ticket_for_customer(42) is None


@responses.activate
def test_find_last_ticket_picks_newest_regardless_of_state(client):
    """Anders als find_open_ticket_for_customer: das zuletzt kontaktierte
    Ticket zaehlt, auch wenn es laengst geschlossen ist."""
    responses.add(
        responses.GET,
        f"{BASE}/tickets/search",
        json=[
            {"id": 1, "number": "1001", "state": "open", "last_contact_at": "2026-01-01T00:00:00Z"},
            {"id": 2, "number": "1002", "state": "closed", "last_contact_at": "2026-08-01T00:00:00Z"},
        ],
    )
    ticket = client.find_last_ticket_for_customer(42)
    assert ticket.id == 2


@responses.activate
def test_find_last_ticket_none_when_no_tickets_exist(client):
    responses.add(responses.GET, f"{BASE}/tickets/search", json=[])
    assert client.find_last_ticket_for_customer(42) is None


@responses.activate
def test_search_tickets_by_tag(client):
    responses.add(
        responses.GET, f"{BASE}/tickets/search", json=[{"id": 5}, {"id": 6}]
    )
    assert client.search_tickets_by_tag("sms-out") == [5, 6]


@responses.activate
def test_add_and_remove_tag(client):
    # tags/add nutzt POST, tags/remove laut Zammad-API DELETE -- unterschiedliche
    # Methoden, kein Copy-Paste-Fehler.
    responses.add(responses.POST, f"{BASE}/tags/add", json={})
    responses.add(responses.DELETE, f"{BASE}/tags/remove", json={})
    client.add_tag(5, "sms-sent")
    client.remove_tag(5, "sms-out")


@responses.activate
def test_get_tags(client):
    responses.add(responses.GET, f"{BASE}/tags", json={"tags": ["sms-sent", "sms-budget-warten"]})
    assert client.get_tags(5) == ["sms-sent", "sms-budget-warten"]


@responses.activate
def test_add_article_defaults_to_phone_customer(client):
    """Default passt zum Einhaengen echter eingehender SMS in ein
    bestehendes Ticket."""
    responses.add(responses.POST, f"{BASE}/ticket_articles", json={})
    client.add_article(5, "Hallo vom Kunden")
    payload = json.loads(responses.calls[-1].request.body)
    assert payload["type"] == "phone"
    assert payload["sender"] == "Customer"
    assert payload["internal"] is False


@responses.activate
def test_add_article_system_note_uses_note_type_and_agent_sender(client):
    """Regression: eigene System-/Audit-Vermerke (z.B. 'SMS-Versand'-
    Quittung) duerfen NICHT type='phone' sein, sonst kann ein Zammad-Trigger
    fuer neue oeffentliche Anruf-Artikel auf unsere eigene Notiz erneut
    feuern und eine Endlosschleife (SMS erneut senden) ausloesen."""
    responses.add(responses.POST, f"{BASE}/ticket_articles", json={})
    client.add_article(5, "SMS-Versand: 1 SMS uebergeben.", internal=True, article_type="note", sender="Agent")
    payload = json.loads(responses.calls[-1].request.body)
    assert payload["type"] == "note"
    assert payload["sender"] == "Agent"
    assert payload["internal"] is True


@responses.activate
def test_get_group_name(client):
    responses.add(responses.GET, f"{BASE}/groups/16", json={"id": 16, "name": "SMS-unbekannt"})
    assert client.get_group_name(16) == "SMS-unbekannt"


@responses.activate
def test_get_group_name_is_cached(client):
    responses.add(responses.GET, f"{BASE}/groups/16", json={"id": 16, "name": "SMS-unbekannt"})
    client.get_group_name(16)
    client.get_group_name(16)
    group_calls = [c for c in responses.calls if "/groups/16" in c.request.url]
    assert len(group_calls) == 1


@responses.activate
def test_set_state(client):
    responses.add(responses.PUT, f"{BASE}/tickets/5", json={})
    client.set_state(5, 4)
    payload = json.loads(responses.calls[-1].request.body)
    assert payload == {"state_id": 4}


@responses.activate
def test_set_subject(client):
    responses.add(responses.PUT, f"{BASE}/tickets/5", json={})
    client.set_subject(5, "SMS-Guthaben")
    payload = json.loads(responses.calls[-1].request.body)
    assert payload == {"title": "SMS-Guthaben"}
