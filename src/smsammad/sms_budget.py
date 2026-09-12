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

-- Schutz gegen Teltonikas fail2ban-Mechanismus (20 Fehlversuche/24h ->
-- dauerhafter Block), siehe access_guard.py. Ein Datensatz pro Zugang
-- ('cgi' fuer das SMS-Gateway, 'api' fuer die REST-API/USSD) existiert
-- NUR waehrend/nach einer Sperre -- Erfolg loescht die Zeile wieder.
-- Doppelversand-Sperre, siehe ticket_to_sms.py: jede erfolgreich an den
-- Router uebergebene SMS wird SOFORT hier vermerkt, bevor Zammad
-- angefasst wird. stage: 0 = gesendet, 1 = Versand-Notiz in Zammad,
-- 2 = Tags umgestellt (erledigt). Scheitert die Zammad-Buchhaltung
-- (Ausfall), holt der naechste Lauf nur die fehlenden Schritte nach,
-- statt dieselbe SMS ein zweites Mal zu senden.
CREATE TABLE IF NOT EXISTS sent_articles (
    ticket_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    note TEXT NOT NULL,
    stage INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ticket_id, article_id)
);

CREATE TABLE IF NOT EXISTS access_state (
    scope TEXT PRIMARY KEY,
    block_level INTEGER NOT NULL DEFAULT 0,
    blocked_until TEXT
);
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
        """Gilt NUR fuer die SMS-Guthabenabfrage (kostet eine echte SMS) --
        die USSD-Abfrage ist synchron/kostenlos und wird von balance_check.py
        bewusst NIE gegen dieses Zeitfenster geprueft, siehe dort."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'last_balance_query'").fetchone()
        if row is None:
            return True
        return now - datetime.fromisoformat(row[0]) >= timedelta(hours=interval_hours)

    # Fingerprint der Zugangsdaten, mit denen eine Sperre ausgeloest wurde,
    # liegt bewusst in der meta-Tabelle (kein ALTER an der frisch deployten
    # access_state-Tabelle). Wird IMMER in derselben Transaktion wie die
    # Sperre selbst geschrieben/geloescht -> beide Tabellen bleiben
    # konsistent.
    @staticmethod
    def _fp_key(scope: str) -> str:
        return f"access_fingerprint_{scope}"

    def access_blocked_until(
        self, scope: str, credential_fingerprint: str | None = None, now: datetime | None = None
    ) -> datetime | None:
        """None, wenn `scope` aktuell frei ist (kein Eintrag, oder eine
        Sperre ist bereits abgelaufen -- dann bewusst NICHT geloescht, das
        macht record_access_failure()/record_access_success() beim
        naechsten tatsaechlichen Zugriffsversuch, sonst wuerde der
        block_level fuer die Cooldown-Progression schon durch reines
        Nachschauen verloren gehen).

        `credential_fingerprint`: wurde die Sperre mit ANDEREN Zugangsdaten
        ausgeloest als den jetzt konfigurierten (Fingerprint stimmt nicht
        ueberein), gilt sie nicht mehr -> None. So hebt eine Korrektur der
        Zugangsdaten in der config.ini die Sperre automatisch auf, ohne
        dass man die Restlaufzeit (bis 24h) abwarten muss."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT blocked_until FROM access_state WHERE scope = ?", (scope,)
            ).fetchone()
            if row is None or row[0] is None:
                return None
            if credential_fingerprint is not None:
                fp_row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (self._fp_key(scope),)
                ).fetchone()
                if fp_row is not None and fp_row[0] != credential_fingerprint:
                    return None
        blocked_until = datetime.fromisoformat(row[0])
        return blocked_until if blocked_until > now else None

    def record_access_failure(
        self,
        scope: str,
        credential_fingerprint: str | None = None,
        stages_hours: tuple[int, ...] = (4, 8, 24),
        now: datetime | None = None,
    ) -> datetime:
        """Zugang `scope` sperren (bzw. die Sperre verlaengern, falls schon
        gesperrt) -- Cooldown eskaliert ueber `stages_hours`, gedeckelt bei
        deren letztem Wert. Liefert den neuen blocked_until-Zeitpunkt.

        Aendern sich die Zugangsdaten (Fingerprint weicht vom gespeicherten
        ab), beginnt die Eskalation bei Stufe 1 neu -- ein Fehler mit NEUEN
        Zugangsdaten ist ein neues Problem, nicht die Fortsetzung des
        alten."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT block_level FROM access_state WHERE scope = ?", (scope,)
            ).fetchone()
            base_level = row[0] if row else 0
            if credential_fingerprint is not None:
                fp_row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (self._fp_key(scope),)
                ).fetchone()
                if fp_row is not None and fp_row[0] != credential_fingerprint:
                    base_level = 0  # andere Zugangsdaten -> Eskalation neu starten
            level = base_level + 1
            delay_hours = stages_hours[min(level - 1, len(stages_hours) - 1)]
            blocked_until = now + timedelta(hours=delay_hours)
            conn.execute(
                "INSERT INTO access_state (scope, block_level, blocked_until) VALUES (?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET "
                "block_level = excluded.block_level, blocked_until = excluded.blocked_until",
                (scope, level, blocked_until.isoformat()),
            )
            if credential_fingerprint is not None:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (self._fp_key(scope), credential_fingerprint),
                )
        return blocked_until

    def record_access_success(self, scope: str) -> bool:
        """Zugang `scope` wieder frei -- Sperr-Zustand loeschen. Liefert
        True, wenn zuvor tatsaechlich ein Fehlerzustand bestand (fuer die
        Entwarnungsmail in access_guard.py), sonst False (ganz normaler
        Erfolg ohne vorherigen Fehler -- der haeufige Fall)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT block_level FROM access_state WHERE scope = ?", (scope,)
            ).fetchone()
            had_failure = row is not None and row[0] > 0
            if row is not None:
                conn.execute("DELETE FROM access_state WHERE scope = ?", (scope,))
                conn.execute("DELETE FROM meta WHERE key = ?", (self._fp_key(scope),))
        return had_failure

    def list_access_blocks(self) -> list[tuple[str, int, datetime | None]]:
        """Alle Zugaenge mit gespeichertem Sperr-Zustand (auch bereits
        abgelaufene Sperren, deren Eskalationsstufe noch gilt) -- fuer
        reset-access. Liefert (scope, block_level, blocked_until)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT scope, block_level, blocked_until FROM access_state ORDER BY scope"
            ).fetchall()
        return [
            (scope, level, datetime.fromisoformat(until) if until else None)
            for scope, level, until in rows
        ]

    def clear_access_block(self, scope: str) -> bool:
        """Manuelles Aufheben einer Sperre (reset-access), ohne jeden
        Router-Kontakt: Sperr-Zustand, Eskalationsstufe und Fingerprint
        loeschen. Fuer den Fall, dass das Problem am ROUTER behoben wurde
        (Berechtigung nachgetragen, Post/Get wieder aktiviert, Router-
        Passwort zurueckgesetzt) -- dann aendert sich die config.ini nicht,
        und der Fingerprint-Abgleich allein hebt die Sperre nicht auf.
        Liefert True, wenn ueberhaupt ein Zustand bestand."""
        with self._connect() as conn:
            deleted = conn.execute("DELETE FROM access_state WHERE scope = ?", (scope,)).rowcount
            conn.execute("DELETE FROM meta WHERE key = ?", (self._fp_key(scope),))
        return deleted > 0

    # Doppelversand-Sperre (siehe sent_articles im Schema / ticket_to_sms.py)
    SENT_STAGE_SENT = 0
    SENT_STAGE_NOTE = 1
    SENT_STAGE_DONE = 2

    def record_sent_article(
        self, ticket_id: int, article_id: int, note: str, now: datetime | None = None
    ) -> None:
        """Direkt nach erfolgreicher Uebergabe an den Router, VOR jedem
        Zammad-Zugriff. Ein erneuter Versand desselben Artikels (Agent hat
        den Tag bewusst neu gesetzt) beginnt wieder bei Stufe 0."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sent_articles (ticket_id, article_id, sent_at, note, stage) "
                "VALUES (?, ?, ?, ?, 0) ON CONFLICT(ticket_id, article_id) DO UPDATE SET "
                "sent_at = excluded.sent_at, note = excluded.note, stage = 0",
                (ticket_id, article_id, now.isoformat(), note),
            )

    def set_sent_article_stage(self, ticket_id: int, article_id: int, stage: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sent_articles SET stage = ? WHERE ticket_id = ? AND article_id = ?",
                (stage, ticket_id, article_id),
            )

    def sent_article_state(self, ticket_id: int, article_id: int) -> tuple[int, str] | None:
        """(stage, note), falls dieser Artikel bereits an den Router
        uebergeben wurde, sonst None. Auswertung siehe
        ticket_to_sms._process_one."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT stage, note FROM sent_articles WHERE ticket_id = ? AND article_id = ?",
                (ticket_id, article_id),
            ).fetchone()
        return (row[0], row[1]) if row else None

    def prune_sent_articles(
        self,
        done_after: timedelta = timedelta(days=30),
        pending_after: timedelta = timedelta(days=7),
        now: datetime | None = None,
    ) -> list[tuple[int, int]]:
        """Alte Vermerke aufraeumen. Erledigte nach 30 Tagen; unerledigte
        nach 7 Tagen (dann hat jemand den Tag 'sms-out' von Hand entfernt,
        die Buchhaltung wird nie mehr nachgeholt) -- deren (ticket_id,
        article_id) werden zurueckgegeben, damit der Aufrufer warnen kann."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            stale = conn.execute(
                "SELECT ticket_id, article_id FROM sent_articles WHERE stage < ? AND sent_at < ?",
                (self.SENT_STAGE_DONE, (now - pending_after).isoformat()),
            ).fetchall()
            conn.execute(
                "DELETE FROM sent_articles WHERE (stage < ? AND sent_at < ?) "
                "OR (stage >= ? AND sent_at < ?)",
                (
                    self.SENT_STAGE_DONE,
                    (now - pending_after).isoformat(),
                    self.SENT_STAGE_DONE,
                    (now - done_after).isoformat(),
                ),
            )
        return [(t, a) for t, a in stale]

    # Zammad-Ausfall-Zustand (siehe zammad_outage.py), in der meta-Tabelle
    # -- keine Schema-Aenderung an der produktiven DB noetig. Alle
    # Uebergaenge atomar per INSERT OR IGNORE / DELETE-rowcount: die Tasks
    # ticket-to-sms und sms-to-ticket laufen per Cron parallel (flock nur
    # je Task) und duerfen die Ausfall-/Entwarnungsmail nicht beide senden.
    _OUTAGE_SINCE = "zammad_outage_since"
    _OUTAGE_NOTIFIED = "zammad_outage_notified_at"

    def mark_zammad_outage_start(self, now: datetime | None = None) -> datetime:
        """Beginn des laufenden Ausfalls festhalten (nur beim ersten
        Fehlschlag, spaetere lassen ihn unveraendert) und zurueckgeben."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                (self._OUTAGE_SINCE, now.isoformat()),
            )
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (self._OUTAGE_SINCE,)
            ).fetchone()
        return datetime.fromisoformat(row[0])

    def claim_zammad_outage_notification(self, now: datetime | None = None) -> bool:
        """True genau fuer den EINEN Aufrufer, der die Ausfall-Mail senden
        darf; alle weiteren (gleicher Ausfall) bekommen False."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            inserted = conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                (self._OUTAGE_NOTIFIED, now.isoformat()),
            ).rowcount
        return inserted == 1

    def clear_zammad_outage(self) -> bool:
        """Ausfall beendet: Zustand loeschen. True, wenn fuer diesen Ausfall
        eine Mail verschickt worden war (dann ist eine Entwarnung faellig)
        -- ebenfalls nur fuer genau einen Aufrufer."""
        with self._connect() as conn:
            notified = conn.execute(
                "DELETE FROM meta WHERE key = ?", (self._OUTAGE_NOTIFIED,)
            ).rowcount
            conn.execute("DELETE FROM meta WHERE key = ?", (self._OUTAGE_SINCE,))
        return notified == 1

    def mark_balance_queried(self, now: datetime | None = None) -> None:
        """Nur nach einer tatsaechlich gesendeten SMS-Guthabenabfrage
        aufrufen (siehe should_query_balance) -- NICHT nach einer
        USSD-Abfrage, sonst wuerde eine haeufige kostenlose USSD-Abfrage
        faelschlich eine faellige SMS-Abfrage mit ausbremsen."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_balance_query', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (now.isoformat(),),
            )
