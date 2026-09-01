import pytest

from smsammad.phone import (
    PhoneNumberError,
    is_mobile_number,
    to_e164,
    to_human_readable,
    to_teltonika_format,
)


@pytest.mark.parametrize(
    "raw",
    ["0151 12345678", "+49 151 12345678", "0049 151 12345678", "015112345678"],
)
def test_to_e164_de_variants_agree(raw):
    assert to_e164(raw, "DE") == "+4915112345678"


def test_to_teltonika_format_de():
    assert to_teltonika_format("0151 12345678", "DE") == "004915112345678"


def test_invalid_number_raises():
    with pytest.raises(PhoneNumberError):
        to_e164("not a number", "DE")


def test_is_mobile_number_true_for_de_mobile():
    assert is_mobile_number("0151 12345678", "DE") is True


def test_is_mobile_number_false_for_de_landline():
    assert is_mobile_number("030 12345678", "DE") is False


def test_is_mobile_number_false_for_invalid_value_instead_of_raising():
    assert is_mobile_number("not a number", "DE") is False


def test_to_human_readable_de_uses_dash_separator():
    """Bindestrich statt Leerzeichen als Gruppentrenner -- Zammads
    Volltextsuche behandelt Leerzeichen als Token-Grenze (live
    verifiziert), '-' aber nicht. Siehe zammad.py find_customer_by_phone."""
    assert to_human_readable("+4917212344567", "DE") == "0172-1234-4567"
