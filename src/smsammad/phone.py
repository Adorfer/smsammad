"""Rufnummer-Normalisierung ueber phonenumbers (libphonenumber)."""

import phonenumbers


class PhoneNumberError(ValueError):
    pass


def _parse(number: str, default_region: str) -> phonenumbers.PhoneNumber:
    try:
        parsed = phonenumbers.parse(number, default_region)
    except phonenumbers.NumberParseException as exc:
        raise PhoneNumberError(f"Ungueltige Rufnummer '{number}': {exc}") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise PhoneNumberError(f"Ungueltige Rufnummer '{number}'")
    return parsed


def to_teltonika_format(number: str, default_region: str) -> str:
    """z.B. '0151 12345678' (DE) -> '0049151112345678'."""
    parsed = _parse(number, default_region)
    return f"00{parsed.country_code}{parsed.national_number}"


def to_e164(number: str, default_region: str) -> str:
    """z.B. '0151 12345678' (DE) -> '+4915112345678'."""
    parsed = _parse(number, default_region)
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def to_human_readable(number: str, default_region: str) -> str:
    """z.B. '+4917212344567' (DE) -> '0172 1234 4567': Landesvorwahl durch
    den nationalen Trunk-Praefix '0' ersetzt, danach in Vierergruppen.
    Nimmt einen "0"-Trunk-Praefix an (gilt fuer DE/AT/CH u.a., nicht
    universell) -- fuer dieses Projekt ausreichend, da einlaendig genutzt.
    """
    parsed = _parse(number, default_region)
    digits = f"0{parsed.national_number}"
    return " ".join(digits[i : i + 4] for i in range(0, len(digits), 4))
