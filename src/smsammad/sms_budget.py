"""SMS-Sende-Budget (Rate Limiting je Stunde/24h) UND Statistik-Rohdaten
(Zeitpunkt, Richtung, Gruppe, Agent) in einer SQLite-Datenbank, persistent
ueber Cronlaeufe. Rollierende Fenster, keine Kalenderstunden/-tage.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

_HOUR = timedelta(hours=1)
_DAY = timedelta(hours=24)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sms_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    direction TEXT NOT NULL,
    group_name TEXT,
    agent TEXT,
    ticket_number TEXT
);
CREATE INDEX IF NOT EXISTS idx_sms_events_direction_ts ON sms_events(direction, ts);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS balance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    balance_eur REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_balance_history_ts ON balance_history(ts);
"""


@dataclass
class BudgetStatus:
    sent_last_hour: int
    sent_last_24h: int
    max_per_hour: int
    max_per_24h: int

    def has_capacity(self, n: int) -> bool:
        return self.sent_last_hour + n <= self.max_per_hour and (
            self.sent_last_24h + n <= self.max_per_24h
        )


@dataclass
class GroupStat:
    direction: str
    group_name: str | None
    agent: str | None
    count: int


class SmsBudget:
    def __init__(self, db_file: Path, max_per_hour: int, max_per_24h: int) -> None:
        self._db_file = db_file
        self._max_per_hour = max_per_hour
        self._max_per_24h = max_per_24h

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_file, timeout=10)
        try:
            conn.executescript(_SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _count_out_since(self, conn: sqlite3.Connection, since: datetime) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM sms_events WHERE direction='out' AND ts > ?",
            (since.isoformat(),),
        ).fetchone()
        return row[0]

    def status(self, now: datetime | None = None) -> BudgetStatus:
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            sent_last_hour = self._count_out_since(conn, now - _HOUR)
            sent_last_24h = self._count_out_since(conn, now - _DAY)
        return BudgetStatus(
            sent_last_hour=sent_last_hour,
            sent_last_24h=sent_last_24h,
            max_per_hour=self._max_per_hour,
            max_per_24h=self._max_per_24h,
        )

    def can_send(self, n: int, now: datetime | None = None) -> bool:
        return self.status(now).has_capacity(n)

    def record_sent(
        self,
        n: int,
        group: str | None = None,
        agent: str | None = None,
        ticket_number: str | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO sms_events (ts, direction, group_name, agent, ticket_number) "
                "VALUES (?, 'out', ?, ?, ?)",
                [(now.isoformat(), group, agent, ticket_number)] * n,
            )

    def record_received(
        self,
        group: str | None = None,
        ticket_number: str | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sms_events (ts, direction, group_name, agent, ticket_number) "
                "VALUES (?, 'in', ?, NULL, ?)",
                (now.isoformat(), group, ticket_number),
            )

    def next_available_at(self, n: int, now: datetime | None = None) -> datetime:
        """Fruehester Zeitpunkt, ab dem (rollierend) wieder mindestens `n`
        SMS gesendet werden koennten -- fuer eine Hinweis-Notiz mit
        voraussichtlicher Sendezeit. Liefert `now`, falls bereits jetzt
        moeglich."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            hour_window = [
                datetime.fromisoformat(r[0])
                for r in conn.execute(
                    "SELECT ts FROM sms_events WHERE direction='out' AND ts > ? ORDER BY ts",
                    ((now - _HOUR).isoformat(),),
                )
            ]
            day_window = [
                datetime.fromisoformat(r[0])
                for r in conn.execute(
                    "SELECT ts FROM sms_events WHERE direction='out' AND ts > ? ORDER BY ts",
                    ((now - _DAY).isoformat(),),
                )
            ]

        def eta_for(window: list[datetime], limit: int, span: timedelta) -> datetime:
            allowed = max(limit - n, 0)
            if len(window) <= allowed:
                return now
            idx = len(window) - allowed - 1
            return window[idx] + span

        hour_eta = eta_for(hour_window, self._max_per_hour, _HOUR)
        day_eta = eta_for(day_window, self._max_per_24h, _DAY)
        return max(hour_eta, day_eta, now)

    def should_notify(self, cooldown_minutes: int, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'last_budget_notification'"
            ).fetchone()
        if row is None:
            return True
        return now - datetime.fromisoformat(row[0]) >= timedelta(minutes=cooldown_minutes)

    def mark_notified(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_budget_notification', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (now.isoformat(),),
            )

    def summary_by_group_and_agent(
        self, since: datetime, direction: str = "out"
    ) -> list[GroupStat]:
        """Fuer die Budget-Warn-Mail: Aufschluesselung nach Gruppe UND Agent
        (eine Richtung, per Default 'out')."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT group_name, agent, COUNT(*) FROM sms_events "
                "WHERE direction = ? AND ts > ? GROUP BY group_name, agent "
                "ORDER BY COUNT(*) DESC",
                (direction, since.isoformat()),
            ).fetchall()
        return [GroupStat(direction, group, agent, count) for group, agent, count in rows]

    def summary_by_group(self, since: datetime) -> list[GroupStat]:
        """Fuer die periodische Stats-Mail: beide Richtungen, nur nach
        Gruppe (kein Agent)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT direction, group_name, COUNT(*) FROM sms_events "
                "WHERE ts > ? GROUP BY direction, group_name",
                (since.isoformat(),),
            ).fetchall()
        return [GroupStat(direction, group, None, count) for direction, group, count in rows]

    def record_balance(self, amount_eur: float, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO balance_history (ts, balance_eur) VALUES (?, ?)",
                (now.isoformat(), amount_eur),
            )

    def latest_balance(self) -> tuple[datetime, float] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ts, balance_eur FROM balance_history ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row[0]), row[1]

    def balance_history_since(self, since: datetime) -> list[tuple[datetime, float]]:
        """Aufsteigend nach Zeitpunkt sortiert."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, balance_eur FROM balance_history WHERE ts > ? ORDER BY ts",
                (since.isoformat(),),
            ).fetchall()
        return [(datetime.fromisoformat(ts), balance) for ts, balance in rows]

    def should_query_balance(self, interval_hours: int, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'last_balance_query'").fetchone()
        if row is None:
            return True
        return now - datetime.fromisoformat(row[0]) >= timedelta(hours=interval_hours)

    def mark_balance_queried(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_balance_query', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (now.isoformat(),),
            )
