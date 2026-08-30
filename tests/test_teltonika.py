import urllib.parse
from unittest.mock import patch

import pytest
import requests
import responses

from smsammad.config import TeltonikaConfig
from smsammad.teltonika import TeltonikaClient, TeltonikaError


@pytest.fixture(autouse=True)
def no_real_sleep():
    """Der Retry bei transienten Router-Ueberlastungs-Symptomen wartet
    zwischen Versuchen echt (bis zu mehreren Minuten) -- in Tests nicht
    noetig/gewuenscht."""
    with patch("smsammad.teltonika.time.sleep"):
        yield

# Beispiel-Antwort gegen ein echtes RUT240 verifiziert (Klartext, kein JSON).
REAL_SMS_LIST_RESPONSE = (
    "Index: 4\n"
    "Date: Sat Oct 26 19:04:11 2024\n"
    "Sender: +491775280961\n"
    "Text: Test \n"
    "Status: read\n"
    "------------------------------\n"
    "Index: 0\n"
    "Date: Sat Oct 26 16:42:24 2024\n"
    "Sender: 22543\n"
    "Text: Kurzwahl:Systemnachricht ohne Rufnummer\n"
    "Status: read\n"
    "------------------------------\n"
)


@pytest.fixture
def config():
    return TeltonikaConfig(
        host="192.168.1.1",
        username="user1",
        password="pass",
        default_country_code="DE",
        scheme="http",
        verify_tls=True,
    )


@pytest.fixture
def client(config):
    return TeltonikaClient(config)


@responses.activate
def test_send_calls_sms_send(client):
    # POST statt GET: ein langer `text` als Query-String hat live HTTP 413
    # "Request Entity Too Large" verursacht (URL-Laengenlimit des Router-
    # eigenen Webservers), POST mit Body war live erfolgreich.
    responses.add(responses.POST, "http://192.168.1.1/cgi-bin/sms_send", body="OK")
    client.send("0049151112345678", "hallo")
    sent_params = urllib.parse.parse_qs(responses.calls[0].request.body)
    assert sent_params["number"] == ["0049151112345678"]
    assert sent_params["text"] == ["hallo"]


@responses.activate
def test_list_messages_parses_real_plaintext_format(client):
    responses.add(
        responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body=REAL_SMS_LIST_RESPONSE
    )
    messages = client.list_messages()
    assert len(messages) == 2

    assert messages[0].index == 4
    assert messages[0].sender == "+491775280961"
    assert messages[0].text == "Test"
    assert messages[0].timestamp == "Sat Oct 26 19:04:11 2024"

    assert messages[1].index == 0
    assert messages[1].sender == "22543"
    assert messages[1].text == "Kurzwahl:Systemnachricht ohne Rufnummer"


@responses.activate
def test_list_messages_empty(client):
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body="")
    assert client.list_messages() == []


@responses.activate
def test_list_messages_disabled_gateway_raises(client):
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body="Disabled\n")
    with pytest.raises(TeltonikaError):
        client.list_messages()


@responses.activate
def test_http_error_raises_teltonika_error(client):
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", status=500)
    with pytest.raises(TeltonikaError):
        client.list_messages()


@responses.activate
def test_auth_error_raises_teltonika_error(client):
    responses.add(
        responses.GET,
        "http://192.168.1.1/cgi-bin/sms_list",
        status=401,
        body="Bad username or password\n",
    )
    with pytest.raises(TeltonikaError):
        client.list_messages()


@responses.activate
def test_delete_calls_sms_delete(client):
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_delete", body="OK")
    client.delete(3)
    assert responses.calls[0].request.params["number"] == "3"


@responses.activate
def test_get_retries_on_busy_response_body_then_succeeds(client):
    # Live beobachtet: Router antwortet bei CPU-Ueberlastung (z.B. durch
    # eigenen langen Multipart-SMS-Versand) manchmal mit HTTP 200 + "ERROR"
    # statt echten Daten -- muss als transientes Signal erkannt werden.
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body="ERROR\n")
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body="")

    assert client.list_messages() == []
    assert len(responses.calls) == 2


@responses.activate
def test_get_retries_on_timeout_busy_body_then_succeeds(client):
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body="TIMEOUT\n")
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body="")

    assert client.list_messages() == []
    assert len(responses.calls) == 2


@responses.activate
def test_get_retries_on_connection_error_then_succeeds(client):
    # Deckt auch langsamen TCP-Connect/Paketverlust zum Router ab --
    # requests.exceptions.ConnectionError/ConnectTimeout sind beides
    # requests.RequestException-Subklassen, die schon _get_once faengt.
    responses.add(
        responses.GET,
        "http://192.168.1.1/cgi-bin/sms_list",
        body=requests.exceptions.ConnectionError("connection refused"),
    )
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body="")

    assert client.list_messages() == []
    assert len(responses.calls) == 2


@responses.activate
def test_get_exhausts_retries_and_raises(client):
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body="ERROR\n")
    # retry_max_attempts=3 (Default) -> 4 Versuche insgesamt.
    with pytest.raises(TeltonikaError):
        client.list_messages()
    assert len(responses.calls) == 4


@responses.activate
def test_get_retry_uses_configured_delays(config):
    config.retry_max_attempts = 2
    config.retry_first_delay_seconds = 5.0
    config.retry_delay_seconds = 30.0
    client = TeltonikaClient(config)
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body="ERROR\n")

    with patch("smsammad.teltonika.time.sleep") as sleep_mock:
        with pytest.raises(TeltonikaError):
            client.list_messages()

    assert sleep_mock.call_args_list == [((5.0,),), ((30.0,),)]


@responses.activate
def test_get_retry_disabled_with_zero_max_attempts(config):
    config.retry_max_attempts = 0
    client = TeltonikaClient(config)
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_list", body="ERROR\n")

    with pytest.raises(TeltonikaError):
        client.list_messages()
    assert len(responses.calls) == 1


@responses.activate
def test_send_never_retries_even_on_failure(client):
    # Bewusst KEIN Retry fuer sms_send (POST): ein Retry nach einem
    # clientseitigen Timeout koennte eine tatsaechlich schon versendete
    # SMS ein zweites Mal verschicken (live so beobachtet).
    responses.add(responses.POST, "http://192.168.1.1/cgi-bin/sms_send", status=500)

    with pytest.raises(TeltonikaError):
        client.send("0049151112345678", "hallo")
    assert len(responses.calls) == 1


@responses.activate
def test_total_extracts_number(client):
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_total", body="Total: 7\n")
    assert client.total() == 7
