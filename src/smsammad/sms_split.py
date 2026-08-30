"""Aufteilung langer SMS-Texte in mehrere Teile mit Teil-Praefix
(Classic-Modus: jedes Stueck eine eigenstaendige Einzel-SMS -- Gegenstueck
zum Multipart-Modus in ticket_to_sms.py, siehe README)."""

from .sms_encoding import (
    GSM7_SINGLE_LIMIT,
    UCS2_SINGLE_LIMIT,
    gsm7_septet_cost,
    is_gsm7_compatible,
    ucs2_unit_cost,
)

_PREFIX_RESERVE = 8  # Platz fuer "(NN/NN) ", reicht bis 99 Teile


def split_for_sms(text: str, limit: int | None = None) -> list[str]:
    """Teilt `text` in Stuecke, die JEWEILS als eigenstaendige Einzel-SMS
    verschickt werden -- `limit` ueberschreibt das Einzel-SMS-Budget
    (Default: encoding-abhaengig, GSM-7 160 Septets / UCS-2 70 Codeein-
    heiten, siehe sms_encoding.py). Die Kodierung wird EINMAL fuer den
    GESAMTEN Text bestimmt (GSM-7 nur wenn ALLE Zeichen GSM-7-faehig sind,
    sonst UCS-2 fuer alles -- Standardverhalten jedes SMS-Encoders), Kosten
    entsprechend gezaehlt (GSM-7-Extension-Zeichen wie "€" kosten 2
    Septets) -- vermeidet unvorhersagbare Budget-Spruenge zwischen den
    Teilen je nachdem, wo zufaellig geschnitten wird.

    Wortumbruch am letzten Leerraum vor der Grenze, damit Woerter nicht
    mitten drin abgeschnitten werden.
    """
    if not text:
        return []

    if is_gsm7_compatible(text):
        cost = gsm7_septet_cost
        single_limit = limit if limit is not None else GSM7_SINGLE_LIMIT
    else:
        cost = ucs2_unit_cost
        single_limit = limit if limit is not None else UCS2_SINGLE_LIMIT

    lines = _greedy_chunks(text, cost, single_limit - _PREFIX_RESERVE)
    total = len(lines)
    if total <= 1:
        return lines

    return [f"({i}/{total}) {line}" for i, line in enumerate(lines, start=1)]


def truncate_to_cost(text: str, max_cost: int) -> str:
    """Kuerzt `text` wortweise auf hoechstens `max_cost` (Septets bei
    GSM-7, Codeeinheiten bei UCS-2, je nach erkannter Gesamt-Kodierung) --
    fuer den Multipart-Modus, wenn der volle Text die sichere Gesamtgrenze
    (sms_encoding.GSM7_MULTIPART_MAX_TOTAL/UCS2_MULTIPART_MAX_TOTAL)
    ueberschreitet und `on_overflow = "truncate"` konfiguriert ist. Anders
    als split_for_sms() kein "(N/M) "-Praefix, da das Ergebnis als EINE
    Nachricht verschickt wird.
    """
    if not text:
        return ""
    cost = gsm7_septet_cost if is_gsm7_compatible(text) else ucs2_unit_cost
    lines = _greedy_chunks(text, cost, max_cost)
    return lines[0] if lines else ""


def _greedy_chunks(text: str, cost, budget: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if cost(candidate) > budget:
            if current:
                lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
