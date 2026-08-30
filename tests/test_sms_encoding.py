import pytest

from smsammad.sms_encoding import (
    GSM7_EXTENDED,
    encoding_cost,
    gsm7_septet_cost,
    is_gsm7_compatible,
    ucs2_unit_cost,
)


def test_plain_ascii_is_gsm7_compatible():
    assert is_gsm7_compatible("Hallo Welt 123!") is True


def test_umlaut_forces_ucs2():
    # 'ä' ist NICHT im GSM-7-Basissatz (nur 'ä'/'ö'/'ü'/'ß' klein UND ein
    # paar Grossbuchstaben sind vertreten -- 'Ä' z.B. IST im Basissatz).
    # Nimm ein sicher nicht enthaltenes Zeichen: Emoji.
    assert is_gsm7_compatible("Hallo 😀") is False


def test_all_gsm7_basic_letters_recognized():
    # Stichprobe ueber alle Zeilen der Tabelle -- inkl. der beiden
    # "Ausreisser" @0x40 (¡ statt @) und @0x60 (¿) sowie das versteckte
    # '_' zwischen den griechischen Grossbuchstaben.
    for ch in "AZaz09 ¡¿_ÄÖÑÜ§äöñüàÅåÆæßÉ£¥¤":
        assert is_gsm7_compatible(ch), f"{ch!r} sollte GSM-7-Basissatz sein"


@pytest.mark.parametrize("ch", sorted(GSM7_EXTENDED))
def test_extension_chars_cost_two_septets(ch):
    assert is_gsm7_compatible(ch) is True
    assert gsm7_septet_cost(ch) == 2


def test_basic_chars_cost_one_septet_each():
    assert gsm7_septet_cost("ABC") == 3


def test_mixed_basic_and_extension_septet_cost():
    # "A" (1) + "€" (2) + "B" (1) = 4
    assert gsm7_septet_cost("A€B") == 4


def test_ucs2_unit_cost_bmp_chars():
    assert ucs2_unit_cost("ä") == 1
    assert ucs2_unit_cost("äöü") == 3


def test_ucs2_unit_cost_astral_char_is_surrogate_pair():
    # Emoji ausserhalb der BMP kostet 2 UTF-16-Codeeinheiten.
    assert ucs2_unit_cost("😀") == 2


def test_encoding_cost_picks_gsm7_for_plain_text():
    encoding, cost = encoding_cost("Hallo")
    assert encoding == "gsm7"
    assert cost == 5


def test_encoding_cost_picks_ucs2_when_any_char_is_non_gsm7():
    encoding, cost = encoding_cost("Hallo 😀")
    assert encoding == "ucs2"
    assert cost == ucs2_unit_cost("Hallo 😀")


def test_single_non_gsm7_char_forces_whole_message_to_ucs2():
    # Nur EIN nicht-GSM-7-Zeichen im sonst reinen GSM-7-Text reicht.
    encoding, _ = encoding_cost("Sehr langer ansonsten reiner GSM-7 Text 😀")
    assert encoding == "ucs2"
