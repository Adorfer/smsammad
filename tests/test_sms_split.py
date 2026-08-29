from smsammad.sms_split import split_for_sms


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
