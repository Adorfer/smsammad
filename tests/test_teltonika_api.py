import pytest
import responses

from smsammad.config import BalanceConfig
from smsammad.teltonika_api import TeltonikaApiClient, TeltonikaApiError

HOST = "192.168.1.1"


def _balance_config(**overrides):
    defaults = dict(
        warn_threshold_eur=5.0,
        alarm_threshold_eur=1.0,
        method="ussd",
        api_username="smsammad",
        api_password="secret",
        modem_id="1-1",
    )
    defaults.update(overrides)
    return BalanceConfig(**defaults)


@pytest.fixture
def client():
    return TeltonikaApiClient(HOST, _balance_config(), verify_tls=False)


@responses.activate
def test_send_ussd_returns_response_text(client):
    responses.add(
        responses.POST,
        f"https://{HOST}/api/login",
        json={"success": True, "data": {"username": "smsammad", "token": "tok123", "expires": 299}},
    )
    responses.add(
        responses.POST,
        f"https://{HOST}/api/modems/1-1/actions/send_ussd",
        json={"data": {"response": "2026-08-30 00:07:34 1,Aktuelles Guthaben: 25,77 EUR\r\n0 Weiter,15\n"}},
    )

    result = client.send_ussd("*100#")

    assert "Aktuelles Guthaben: 25,77 EUR" in result
    # Bearer-Token aus dem Login muss im zweiten Request verwendet werden.
    ussd_call = [c for c in responses.calls if "send_ussd" in c.request.url][0]
    assert ussd_call.request.headers["Authorization"] == "Bearer tok123"


@responses.activate
def test_login_failure_raises_api_error(client):
    responses.add(
        responses.POST,
        f"https://{HOST}/api/login",
        json={"success": False, "errors": [{"error": "Login failed"}]},
        status=401,
    )

    with pytest.raises(TeltonikaApiError):
        client.send_ussd("*100#")


@responses.activate
def test_action_permission_denied_raises_api_error(client):
    """Live beobachtet: Login gelingt (200), aber die eigentliche Aktion
    liefert 403, wenn der Account keine Berechtigung fuer das Mobile-API
    hat."""
    responses.add(
        responses.POST,
        f"https://{HOST}/api/login",
        json={"success": True, "data": {"username": "smsammad", "token": "tok123", "expires": 299}},
    )
    responses.add(
        responses.POST,
        f"https://{HOST}/api/modems/1-1/actions/send_ussd",
        json={"success": False, "errors": [{"error": "Unauthorized"}]},
        status=403,
    )

    with pytest.raises(TeltonikaApiError):
        client.send_ussd("*100#")


@responses.activate
def test_empty_response_raises_api_error(client):
    responses.add(
        responses.POST,
        f"https://{HOST}/api/login",
        json={"success": True, "data": {"username": "smsammad", "token": "tok123", "expires": 299}},
    )
    responses.add(
        responses.POST,
        f"https://{HOST}/api/modems/1-1/actions/send_ussd",
        json={"data": {"response": ""}},
    )

    with pytest.raises(TeltonikaApiError):
        client.send_ussd("*100#")
