from smsammad.htmltext import html_to_text


def test_paragraphs_become_lines():
    assert html_to_text("<p>Hallo</p><p>Welt</p>") == "Hallo\nWelt"


def test_list_items_become_lines():
    html = "<ul><li>Eins</li><li>Zwei</li></ul>"
    assert html_to_text(html) == "Eins\nZwei"


def test_entities_are_unescaped():
    assert html_to_text("<p>Toene: &auml;&ouml;&uuml; &amp; mehr</p>") == "Toene: äöü & mehr"


def test_plain_text_passthrough():
    assert html_to_text("kein html hier") == "kein html hier"
