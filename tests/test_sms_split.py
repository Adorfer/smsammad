from smsammad.sms_split import split_for_sms, truncate_to_cost


def test_short_text_single_part_no_prefix():
    assert split_for_sms("Hallo Welt", limit=150) == ["Hallo Welt"]


def test_empty_text_no_parts():
    assert split_for_sms("", limit=150) == []


def test_long_text_gets_split_with_prefix():
    text = " ".join(["wort"] * 60)
    parts = split_for_sms(text, limit=50)
    assert len(parts) > 1
    for i, part in enumerate(parts, start=1):
        assert part.startswith(f"({i}/{len(parts)}) ")
        assert len(part) <= 50


def test_no_word_is_cut_mid_way():
    text = "a" * 40 + " " + "b" * 40
    parts = split_for_sms(text, limit=50)
    assert "a" * 40 in "".join(parts)
    assert "b" * 40 in "".join(parts)


def test_default_limit_is_encoding_aware_gsm7():
    # Reiner GSM-7-Text: Default-Limit ist 160 Septets (Einzel-SMS).
    text = " ".join(["aaaa"] * 33)  # 33*5-1 = 164 Zeichen inkl. Leerzeichen
    parts = split_for_sms(text)
    assert len(parts) > 1


def test_default_limit_is_encoding_aware_ucs2_is_lower():
    # Sobald ein nicht-GSM-7-Zeichen vorkommt: Default-Limit sinkt auf 70
    # Codeeinheiten (Einzel-SMS) -- derselbe Textlaenge in GSM-7 braeuchte
    # noch keinen Split, in UCS-2 schon. "ê" (e-circumflex) ist NICHT im
    # GSM-7-Zeichensatz (im Unterschied zu z.B. "ä", das dort enthalten
    # ist) -- bewusst gewaehlt, um wirklich UCS-2 zu erzwingen.
    gsm7_text = " ".join(["aaaa"] * 13)  # 64 Zeichen
    assert len(split_for_sms(gsm7_text)) == 1

    ucs2_text = " ".join(["êêêê"] * 13)  # 64 Zeichen, aber UCS-2
    assert len(split_for_sms(ucs2_text)) > 1


def test_extension_char_costs_two_septets_when_splitting():
    # 80 "€" (je 2 Septets = 160 Septets) ueberschreitet bei Limit 150
    # (Body-Budget 142 Septets) den Platz -- reine Zeichenzahl (80) taete
    # das nicht, der Septet-Kosten-Fehler waere sonst unbemerkt.
    text = " ".join(["€€€€"] * 20)  # 80 "€"-Zeichen
    parts = split_for_sms(text, limit=150)
    assert len(parts) > 1


def test_truncate_to_cost_cuts_at_word_boundary():
    text = " ".join(["wort"] * 60)
    truncated = truncate_to_cost(text, max_cost=50)
    assert len(truncated) <= 50
    assert not truncated.endswith("wor")


def test_truncate_to_cost_no_prefix():
    text = " ".join(["wort"] * 60)
    truncated = truncate_to_cost(text, max_cost=50)
    assert not truncated.startswith("(")


def test_truncate_to_cost_empty_text():
    assert truncate_to_cost("", max_cost=50) == ""


def test_truncate_to_cost_short_text_unchanged():
    assert truncate_to_cost("Hallo", max_cost=50) == "Hallo"


def test_truncate_to_cost_counts_extension_chars_double():
    # 80 "€" = 160 Septets -- bei max_cost=150 muss gekuerzt werden.
    text = " ".join(["€€€€"] * 20)  # 80 "€"-Zeichen
    truncated = truncate_to_cost(text, max_cost=150)
    assert len(truncated) < len(text)
