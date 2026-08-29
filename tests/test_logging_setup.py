from smsammad.logging_setup import redact_content


def test_short_text_unchanged():
    assert redact_content("Hi") == "Hi"


def test_exact_keep_length_unchanged():
    assert redact_content("Hallo") == "Hallo"


def test_longer_text_gets_masked_after_fifth_char():
    assert redact_content("Hallo Welt") == "Hallo#####"


def test_masked_length_matches_original():
    text = "Ein laengerer SMS-Text mit vielen Zeichen."
    result = redact_content(text)
    assert len(result) == len(text)
    assert result.startswith(text[:5])
    assert set(result[5:]) == {"#"}


def test_custom_keep_length():
    assert redact_content("Hallo Welt", keep=2) == "Ha########"
