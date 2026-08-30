import codecs

from smsammad.logging_setup import redact_content


def test_short_text_gets_rot13_applied():
    assert redact_content("Hi") == "Uv"


def test_exact_keep_length_gets_rot13_applied():
    assert redact_content("Hallo") == "Unyyb"


def test_longer_text_gets_rot13_prefix_and_masked_after_fifth_char():
    assert redact_content("Hallo Welt") == "Unyyb#####"


def test_masked_length_matches_original():
    text = "Ein laengerer SMS-Text mit vielen Zeichen."
    result = redact_content(text)
    assert len(result) == len(text)
    assert result.startswith(codecs.encode(text[:5], "rot_13"))
    assert set(result[5:]) == {"#"}


def test_custom_keep_length():
    assert redact_content("Hallo Welt", keep=2) == "Un########"


def test_rot13_is_not_plaintext_for_ascii_letters():
    """Kernanforderung: die sichtbaren ersten Zeichen duerfen nicht mehr
    im Klartext im Log stehen, wenn sie ASCII-Buchstaben enthalten."""
    result = redact_content("Guthaben niedrig")
    assert "Gutha" not in result


def test_rot13_is_its_own_inverse():
    text = "Hallo"
    assert codecs.encode(redact_content(text), "rot_13") == text
