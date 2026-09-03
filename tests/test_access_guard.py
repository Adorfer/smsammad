from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from smsammad.access_guard import AccessBlocked, guarded_call
from smsammad.config import NotificationConfig
from smsammad.sms_budget import SmsBudget


class AuthError(Exception):
    pass


class OtherError(Exception):
    pass


def _budget(tmp_path):
    return SmsBudget(tmp_path / "stats.db", 20, 100)


def _notification():
    return NotificationConfig(
        smtp_host="mail.example.local",
        smtp_port=587,
        smtp_user="smsammad@example.local",
        smtp_password="pw",
        recipient="ops@example.local",
        enabled=True,
    )


@pytest.fixture(autouse=True)
def no_real_sleep():
    with patch("smsammad.access_guard.time.sleep"):
        yield


def test_success_on_first_attempt_calls_fn_once_and_sends_no_mail(tmp_path):
    budget = _budget(tmp_path)
    calls = []

    with patch("smsammad.access_guard.send_mail") as mail:
        result = guarded_call(budget, "cgi", (AuthError,), _notification(), "Testaktion", lambda: calls.append(1) or "ok")

    assert result == "ok"
    assert calls == [1]
    mail.assert_not_called()


def test_non_auth_error_passes_through_without_retry_or_block(tmp_path):
    budget = _budget(tmp_path)
    attempts = []

    def fn():
        attempts.append(1)
        raise OtherError("kaputt")

    with patch("smsammad.access_guard.send_mail") as mail:
        with pytest.raises(OtherError):
            guarded_call(budget, "cgi", (AuthError,), _notification(), "Testaktion", fn)

    assert len(attempts) == 1  # kein Retry fuer einen nicht-Auth-Fehler
    assert budget.access_blocked_until("cgi") is None
    mail.assert_not_called()


def test_single_auth_failure_retries_once_and_succeeds(tmp_path):
    budget = _budget(tmp_path)
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) == 1:
            raise AuthError("401")
        return "ok"

    with patch("smsammad.access_guard.send_mail") as mail:
        result = guarded_call(budget, "cgi", (AuthError,), _notification(), "Testaktion", fn)

    assert result == "ok"
    assert len(attempts) == 2
    assert budget.access_blocked_until("cgi") is None
    mail.assert_not_called()  # erfolgreicher Retry ist kein "Fehlerfall", keine Mail noetig


def test_two_consecutive_auth_failures_block_and_send_exactly_one_mail(tmp_path):
    budget = _budget(tmp_path)
    attempts = []

    def fn():
        attempts.append(1)
        raise AuthError("401")

    with patch("smsammad.access_guard.send_mail") as mail:
        with pytest.raises(AccessBlocked) as excinfo:
            guarded_call(budget, "cgi", (AuthError,), _notification(), "Testaktion", fn)

    assert len(attempts) == 2  # genau ein Wiederholversuch, kein drittes/viertes Mal
    assert excinfo.value.just_entered is True
    assert budget.access_blocked_until("cgi") is not None
    mail.assert_called_once()
    assert "gesperrt" in mail.call_args.kwargs["subject"]


def test_blocked_scope_skips_call_entirely_without_mail(tmp_path):
    budget = _budget(tmp_path)
    budget.record_access_failure("cgi")
    calls = []

    with patch("smsammad.access_guard.send_mail") as mail:
        with pytest.raises(AccessBlocked) as excinfo:
            guarded_call(budget, "cgi", (AuthError,), _notification(), "Testaktion", lambda: calls.append(1))

    assert calls == []  # kein Router-Kontakt waehrend der Sperre
    assert excinfo.value.just_entered is False
    mail.assert_not_called()  # Mail ging schon beim urspruenglichen Sperren raus


def test_recovery_after_block_resets_state_and_sends_one_mail(tmp_path):
    budget = _budget(tmp_path)
    budget.record_access_failure("cgi")
    # Sperre manuell ablaufen lassen, um den Erfolgsfall direkt zu testen
    with budget._connect() as conn:
        conn.execute(
            "UPDATE access_state SET blocked_until = ? WHERE scope = 'cgi'",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
        )

    with patch("smsammad.access_guard.send_mail") as mail:
        result = guarded_call(budget, "cgi", (AuthError,), _notification(), "Testaktion", lambda: "ok")

    assert result == "ok"
    assert budget.access_blocked_until("cgi") is None
    mail.assert_called_once()
    assert "wieder ok" in mail.call_args.kwargs["subject"]


def test_scopes_are_independent(tmp_path):
    budget = _budget(tmp_path)
    budget.record_access_failure("cgi")

    with patch("smsammad.access_guard.send_mail") as mail:
        result = guarded_call(budget, "api", (AuthError,), _notification(), "Testaktion", lambda: "ok")

    assert result == "ok"
    mail.assert_not_called()  # "api" hatte nie ein Problem, keine Entwarnungsmail noetig
