"""Aufteilung langer SMS-Texte in mehrere Teile mit Teil-Praefix."""

_PREFIX_RESERVE = 8  # Platz fuer "(NN/NN) ", reicht bis 99 Teile


def split_for_sms(text: str, limit: int = 150) -> list[str]:
    """Teilt `text` in Stuecke <= `limit` Zeichen inkl. Praefix wie '(1/3) '.

    Wortumbruch am letzten Leerraum vor der Grenze, damit Woerter nicht
    mitten drin abgeschnitten werden.
    """
    if not text:
        return []

    body_limit = limit - _PREFIX_RESERVE
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > body_limit:
            if current:
                lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    total = len(lines)
    if total <= 1:
        return lines

    return [f"({i}/{total}) {line}" for i, line in enumerate(lines, start=1)]
