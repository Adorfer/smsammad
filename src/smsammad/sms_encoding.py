"""GSM-03.38/UCS-2-bewusste Zeichenkosten fuer SMS-Laengenberechnung.

Tabelle gegen die offizielle ETSI/Unicode-Consortium-Zuordnung verifiziert
(unicode.org/Public/MAPPINGS/ETSI/GSM0338.TXT). Zwei Kostenmodelle:

- GSM-7 (Default-Alphabet + Extension-Table): moeglich, wenn JEDES Zeichen
  im Text Teil des GSM-7-Zeichensatzes ist. Zeichen aus der Extension-
  Table (Escape-Sequenz 0x1B + Code, z.B. "€", "^", "{", "}") kosten ZWEI
  Septets statt eines -- ein haeufiger Fehler ist, hier "1 Unicode-Zeichen
  = 1 Septet" anzunehmen.
- UCS-2: sobald auch nur EIN Zeichen nicht GSM-7-faehig ist (z.B. Umlaute
  auesserhalb des GSM-7-Satzes, Emoji), wird die GESAMTE Nachricht als
  UCS-2 kodiert. Kosten = Anzahl UTF-16-Codeeinheiten (Zeichen ausserhalb
  der Basic Multilingual Plane, z.B. die meisten Emoji, bestehen aus TWO
  Codeeinheiten -- Surrogate-Paar).

Limits unten entsprechen den von AWS/Twilio dokumentierten sicheren
Grenzwerten fuer Concatenated SMS (siehe README).
"""

import string

_ROW_00_0F = "@£$¥èéùìòç\nØø\rÅå"
_ROW_10_1F = "Δ_ΦΓΛΩΠΨΣΘΞÆæßÉ"  # 0x1B (Escape) bewusst ausgelassen
_ROW_20_2F = " !\"#¤%&'()*+,-./"
_ROW_30_3F = string.digits + ":;<=>?"
_ROW_40_4F = "¡" + string.ascii_uppercase[:15]  # ¡ A-O
_ROW_50_5F = string.ascii_uppercase[15:] + "ÄÖÑÜ§"  # P-Z Ä Ö Ñ Ü §
_ROW_60_6F = "¿" + string.ascii_lowercase[:15]  # ¿ a-o
_ROW_70_7F = string.ascii_lowercase[15:] + "äöñüà"  # p-z ä ö ñ ü à

GSM7_BASIC = frozenset(
    _ROW_00_0F
    + _ROW_10_1F
    + _ROW_20_2F
    + _ROW_30_3F
    + _ROW_40_4F
    + _ROW_50_5F
    + _ROW_60_6F
    + _ROW_70_7F
)
# Extension-Table (Escape 0x1B + Code) -- kostet 2 Septets statt 1.
GSM7_EXTENDED = frozenset("\x0c^{}\\[~]|€")

GSM7_CHARS = GSM7_BASIC | GSM7_EXTENDED

# Sichere Grenzwerte fuer Concatenated SMS, siehe README (AWS/Twilio-
# Dokumentation, live gegen die Teltonika-API verifiziert, dass ein
# Ueberschreiten unzuverlaessig wird -- stille Kuerzung bzw. haengender
# Request + doppelte Zustellung beobachtet).
GSM7_SINGLE_LIMIT = 160
GSM7_MULTIPART_PART_LIMIT = 153
GSM7_MULTIPART_MAX_TOTAL = 1530  # 10 Teile
UCS2_SINGLE_LIMIT = 70
UCS2_MULTIPART_PART_LIMIT = 67
UCS2_MULTIPART_MAX_TOTAL = 630  # ~10 Teile


def is_gsm7_compatible(text: str) -> bool:
    """True, wenn JEDES Zeichen im GSM-7-Satz (Basic oder Extension) ist --
    sonst muss die GESAMTE Nachricht als UCS-2 kodiert werden."""
    return all(ch in GSM7_CHARS for ch in text)


def gsm7_septet_cost(text: str) -> int:
    """Anzahl Septets, WENN `text` GSM-7-faehig ist (vorher mit
    is_gsm7_compatible pruefen). Extension-Zeichen kosten 2 Septets."""
    return sum(2 if ch in GSM7_EXTENDED else 1 for ch in text)


def ucs2_unit_cost(text: str) -> int:
    """Anzahl UTF-16-Codeeinheiten -- Zeichen ausserhalb der Basic
    Multilingual Plane (z.B. die meisten Emoji) kosten 2 Einheiten
    (Surrogate-Paar), genau wie ein UCS-2/SMS-Encoder sie zaehlen wuerde.
    """
    return len(text.encode("utf-16-le")) // 2


def encoding_cost(text: str) -> tuple[str, int]:
    """Liefert ('gsm7', Septet-Anzahl) oder ('ucs2', Codeeinheiten-Anzahl)
    -- die Kodierung, die ein Sende-Gateway fuer `text` waehlen wuerde."""
    if is_gsm7_compatible(text):
        return "gsm7", gsm7_septet_cost(text)
    return "ucs2", ucs2_unit_cost(text)
