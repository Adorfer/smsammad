"""On-Demand SMS-Statistik-Mail: SMS in/out der letzten 24 Stunden, 7 und 30
Tage nach Zammad-Gruppe (ohne Agenten-Aufschluesselung -- die gibt es bei
Bedarf in der Budget-Ueberschreitungs-Mail, siehe
ticket_to_sms._format_breakdown).
"""

import logging
from datetime import datetime, timedelta, timezone
from html import escape

from .config import Config
from .notify import send_mail
from .sms_budget import SmsBudget

logger = logging.getLogger("smsammad")

_PERIODS = ((1, "24 Stunden"), (7, "7 Tage"), (30, "30 Tage"))

# Helle Pastelltoene, damit In/Out auch bei vielen Spalten auf einen Blick
# unterscheidbar bleiben.
_COLOR_IN = "#f5eedd"  # helles Beige
_COLOR_OUT = "#e6ebee"  # helles Grau
_COLOR_HEADER = "#f0f0f0"
_BORDER = "1px solid #ccc"

Pivot = dict[str, dict[str, dict[str, int]]]


def _collect(budget: SmsBudget, now: datetime) -> Pivot:
    """Gruppe -> Periodenlabel -> {'in': n, 'out': n}."""
    pivot: Pivot = {}
    for days, label in _PERIODS:
        since = now - timedelta(days=days)
        for row in budget.summary_by_group(since):
            name = row.group_name or "(unbekannt)"
            group = pivot.setdefault(name, {lbl: {"in": 0, "out": 0} for _, lbl in _PERIODS})
            group[label][row.direction] = row.count
    return pivot


def _format_text_table(pivot: Pivot) -> str:
    if not pivot:
        return "(keine Daten)"

    name_width = max(len("Gruppe"), max(len(name) for name in pivot))
    header = f"{'Gruppe':<{name_width}}"
    sub = " " * name_width
    for _, label in _PERIODS:
        header += f"  {label:^11}"
        sub += f"  {'Ein':>5} {'Aus':>5}"
    lines = [header, sub, "-" * max(len(header), len(sub))]
    for name in sorted(pivot):
        line = f"{name:<{name_width}}"
        for _, label in _PERIODS:
            counts = pivot[name][label]
            line += f"  {counts['in']:>5} {counts['out']:>5}"
        lines.append(line)
    return "\n".join(lines)


def _format_html_table(pivot: Pivot) -> str:
    if not pivot:
        return "<p>(keine Daten)</p>"

    style_table = "border-collapse:collapse;font-family:sans-serif;font-size:13px;"
    style_th = f"background:{_COLOR_HEADER};padding:4px 8px;border:{_BORDER};text-align:center;"
    style_td_name = f"padding:4px 8px;border:{_BORDER};"
    style_td_in = f"background:{_COLOR_IN};padding:4px 8px;border:{_BORDER};text-align:right;"
    style_td_out = f"background:{_COLOR_OUT};padding:4px 8px;border:{_BORDER};text-align:right;"

    html = [f'<table style="{style_table}">', "<tr>", f'<th style="{style_th}" rowspan="2">Gruppe</th>']
    for _, label in _PERIODS:
        html.append(f'<th style="{style_th}" colspan="2">{escape(label)}</th>')
    html.append("</tr><tr>")
    for _ in _PERIODS:
        html.append(f'<th style="{style_th}">Ein</th><th style="{style_th}">Aus</th>')
    html.append("</tr>")

    for name in sorted(pivot):
        html.append(f'<tr><td style="{style_td_name}">{escape(name)}</td>')
        for _, label in _PERIODS:
            counts = pivot[name][label]
            html.append(f'<td style="{style_td_in}">{counts["in"]}</td>')
            html.append(f'<td style="{style_td_out}">{counts["out"]}</td>')
        html.append("</tr>")
    html.append("</table>")
    return "".join(html)


def _avg_daily_consumption(history: list[tuple[datetime, float]]) -> float | None:
    """Durchschnittlicher Verbrauch/Tag aus dem ersten und letzten Messwert
    eines Zeitraums. None, falls weniger als 2 Messwerte vorliegen oder
    zwischen ihnen keine messbare Zeit vergangen ist."""
    if len(history) < 2:
        return None
    first_ts, first_balance = history[0]
    last_ts, last_balance = history[-1]
    elapsed_days = (last_ts - first_ts).total_seconds() / 86400
    if elapsed_days <= 0:
        return None
    return (first_balance - last_balance) / elapsed_days


def _collect_balance(budget: SmsBudget, now: datetime) -> dict:
    consumption = {
        label: _avg_daily_consumption(budget.balance_history_since(now - timedelta(days=days)))
        for days, label in _PERIODS
    }
    latest = budget.latest_balance()
    runway_days = None
    rate_7d = consumption.get("7 Tage")
    if latest is not None and rate_7d is not None and rate_7d > 0:
        runway_days = latest[1] / rate_7d
    return {"latest": latest, "consumption": consumption, "runway_days": runway_days}


def _format_balance_text(data: dict) -> str:
    latest = data["latest"]
    if latest is None:
        return "(keine Guthaben-Daten)"
    ts, amount = latest
    lines = [
        f"Aktuelles Guthaben: {amount:.2f} Euro "
        f"(Stand: {ts.astimezone().strftime('%d.%m.%Y %H:%M')})",
        "",
        "Ø Verbrauch/Tag:",
    ]
    for _, label in _PERIODS:
        rate = data["consumption"][label]
        rate_str = f"{rate:.2f} Euro/Tag" if rate is not None else "(keine Daten)"
        lines.append(f"  {label}: {rate_str}")
    lines.append("")
    if data["runway_days"] is not None:
        lines.append(f"Geschaetzte Reichweite: {data['runway_days']:.0f} Tage (Basis: letzte 7 Tage)")
    else:
        lines.append("Geschaetzte Reichweite: unbestimmbar (kein Verbrauch erkennbar)")
    return "\n".join(lines)


def _format_balance_html(data: dict) -> str:
    latest = data["latest"]
    if latest is None:
        return "<p>(keine Guthaben-Daten)</p>"
    ts, amount = latest

    style_table = "border-collapse:collapse;font-family:sans-serif;font-size:13px;"
    style_th = f"background:{_COLOR_HEADER};padding:4px 8px;border:{_BORDER};text-align:left;"
    style_td = f"padding:4px 8px;border:{_BORDER};"

    html = [
        f"<p>Aktuelles Guthaben: <b>{amount:.2f} Euro</b> "
        f"(Stand: {escape(ts.astimezone().strftime('%d.%m.%Y %H:%M'))})</p>",
        f'<table style="{style_table}">',
        f'<tr><th style="{style_th}">Zeitraum</th><th style="{style_th}">Ø Verbrauch/Tag</th></tr>',
    ]
    for _, label in _PERIODS:
        rate = data["consumption"][label]
        rate_str = f"{rate:.2f} Euro/Tag" if rate is not None else "(keine Daten)"
        html.append(
            f'<tr><td style="{style_td}">{escape(label)}</td>'
            f'<td style="{style_td}">{escape(rate_str)}</td></tr>'
        )
    html.append("</table>")
    if data["runway_days"] is not None:
        html.append(
            f"<p>Geschaetzte Reichweite: <b>{data['runway_days']:.0f} Tage</b> "
            f"(Basis: letzte 7 Tage)</p>"
        )
    else:
        html.append("<p>Geschaetzte Reichweite: unbestimmbar (kein Verbrauch erkennbar)</p>")
    return "".join(html)


def run(budget: SmsBudget, config: Config, dry_run: bool = False) -> None:
    now = datetime.now(timezone.utc)
    pivot = _collect(budget, now)

    text_body = "SMS-Statistik nach Gruppe:\n\n" + _format_text_table(pivot)
    html_body = "<p>SMS-Statistik nach Gruppe:</p>" + _format_html_table(pivot)

    if config.balance is not None:
        balance_data = _collect_balance(budget, now)
        text_body += "\n\nGuthaben:\n\n" + _format_balance_text(balance_data)
        html_body += "<p>Guthaben:</p>" + _format_balance_html(balance_data)

    subject = "SMSammad: SMS-Statistik"

    if dry_run:
        logger.info("[dry-run] wuerde Statistik-Mail senden:\n%s", text_body)
        return

    send_mail(config.notification, subject, text_body, html_body=html_body)
    logger.info("Statistik-Mail verschickt")
