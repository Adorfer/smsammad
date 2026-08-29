import pytest

from smsammad.phone import PhoneNumberError, to_e164, to_teltonika_format


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
