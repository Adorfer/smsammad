import pytest

from zammad2teltonikasms.main import sms_to_ticket, ticket_to_sms


def test_ticket_to_sms_not_yet_implemented():
    with pytest.raises(NotImplementedError):
        ticket_to_sms(None)


def test_sms_to_ticket_not_yet_implemented():
    with pytest.raises(NotImplementedError):
        sms_to_ticket(None)
