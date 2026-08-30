import pytest
import responses

from smsammad.config import TeltonikaConfig
from smsammad.teltonika import TeltonikaClient, TeltonikaError

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
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_send", body="OK")
    client.send("0049151112345678", "hallo")
    assert responses.calls[0].request.params["number"] == "0049151112345678"
    assert responses.calls[0].request.params["text"] == "hallo"


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
def test_total_extracts_number(client):
    responses.add(responses.GET, "http://192.168.1.1/cgi-bin/sms_total", body="Total: 7\n")
    assert client.total() == 7
