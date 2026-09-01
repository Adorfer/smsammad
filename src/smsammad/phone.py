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


_MOBILE_TYPES = {
    phonenumbers.PhoneNumberType.MOBILE,
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
}


def is_mobile_number(number: str, default_region: str) -> bool:
    """True wenn phonenumbers die Nummer als Mobilfunknummer erkennt (oder
    als Land, in dem Fest-/Mobilfunk-Nummernraeume nicht eindeutig
    unterscheidbar sind). Ungueltige/nicht parsebare Werte liefern False
    statt einer Exception -- wird nur fuer optionale Fallback-Felder mit
    unkontrolliertem Freitext-Inhalt genutzt, ein Absturz waere hier
    unangemessen."""
    try:
        parsed = _parse(number, default_region)
    except PhoneNumberError:
        return False
    return phonenumbers.number_type(parsed) in _MOBILE_TYPES


def to_human_readable(number: str, default_region: str) -> str:
    """z.B. '+4917212344567' (DE) -> '0172-1234-4567': Landesvorwahl durch
    den nationalen Trunk-Praefix '0' ersetzt, danach in Vierergruppen.
    Nimmt einen "0"-Trunk-Praefix an (gilt fuer DE/AT/CH u.a., nicht
    universell) -- fuer dieses Projekt ausreichend, da einlaendig genutzt.

    Bindestrich statt Leerzeichen als Trennzeichen: live gegen eine echte
    Zammad-Instanz verifiziert, dass deren Volltextsuche ein Leerzeichen
    als Token-Grenze behandelt (ein mit Leerzeichen gruppierter Wert wird
    dadurch von find_customer_by_phone()'s Such-Token nie gefunden,
    sobald die gesuchten letzten Ziffern ueber eine Gruppengrenze
    reichen), '-' und '.' aber NICHT -- bleiben Teil desselben,
    durchsuchbaren Tokens.
    """
    parsed = _parse(number, default_region)
    digits = f"0{parsed.national_number}"
    return "-".join(digits[i : i + 4] for i in range(0, len(digits), 4))
