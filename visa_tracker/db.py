from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  error_summary TEXT,
  duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  check_id INTEGER NOT NULL REFERENCES checks(id),
  observed_at TEXT NOT NULL,
  post_id TEXT NOT NULL,
  visa_class_id TEXT NOT NULL,
  slots_count INTEGER NOT NULL DEFAULT 0,
  earliest_date TEXT,
  fingerprint TEXT NOT NULL,
  raw_json TEXT,
  UNIQUE(check_id, post_id, visa_class_id)
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id INTEGER NOT NULL REFERENCES observations(id),
  channel TEXT NOT NULL,
  sent_at TEXT,
  status TEXT NOT NULL,
  payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_time ON observations(observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_post_visa ON observations(post_id, visa_class_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_checks_time ON checks(started_at);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(sent_at);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ObservationRow:
    post_id: str
    visa_class_id: str
    slots_count: int
    earliest_date: str | None
    fingerprint: str
    raw: dict[str, Any]


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def start_check(self, source: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO checks (started_at, source, status) VALUES (?, ?, ?)",
                (utc_now(), source, "running"),
            )
            return int(cur.lastrowid)

    def finish_check(
        self,
        check_id: int,
        *,
        status: str,
        error_summary: str | None,
        duration_ms: int,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE checks
                SET finished_at = ?, status = ?, error_summary = ?, duration_ms = ?
                WHERE id = ?
                """,
                (utc_now(), status, error_summary, duration_ms, check_id),
            )

    def insert_observations(self, check_id: int, rows: list[ObservationRow]) -> list[int]:
        ids: list[int] = []
        now = utc_now()
        with self.connect() as conn:
            for row in rows:
                cur = conn.execute(
                    """
                    INSERT INTO observations (
                      check_id, observed_at, post_id, visa_class_id,
                      slots_count, earliest_date, fingerprint, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        check_id,
                        now,
                        row.post_id,
                        row.visa_class_id,
                        row.slots_count,
                        row.earliest_date,
                        row.fingerprint,
                        json.dumps(row.raw),
                    ),
                )
                ids.append(int(cur.lastrowid))
        return ids

    def recent_alert_for_fingerprint(self, fingerprint: str, since_iso: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT a.id
                FROM alerts a
                JOIN observations o ON o.id = a.observation_id
                WHERE o.fingerprint = ?
                  AND a.status = 'sent'
                  AND a.sent_at IS NOT NULL
                  AND a.sent_at >= ?
                LIMIT 1
                """,
                (fingerprint, since_iso),
            ).fetchone()
            return row is not None

    def record_alert(
        self,
        observation_id: int,
        *,
        channel: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts (observation_id, channel, sent_at, status, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    channel,
                    utc_now() if status == "sent" else None,
                    status,
                    json.dumps(payload),
                ),
            )

    def metrics(self) -> dict[str, Any]:
        with self.connect() as conn:
            checks_total = conn.execute("SELECT COUNT(*) AS c FROM checks").fetchone()["c"]
            ok_checks = conn.execute(
                "SELECT COUNT(*) AS c FROM checks WHERE status = 'ok'"
            ).fetchone()["c"]
            error_checks = conn.execute(
                "SELECT COUNT(*) AS c FROM checks WHERE status = 'error'"
            ).fetchone()["c"]
            intervals = conn.execute(
                """
                SELECT
                  (julianday(finished_at) - julianday(started_at)) * 86400000 AS duration_ms
                FROM checks
                WHERE finished_at IS NOT NULL
                ORDER BY id DESC
                LIMIT 50
                """
            ).fetchall()
            started = [
                r["started_at"]
                for r in conn.execute(
                    "SELECT started_at FROM checks ORDER BY id DESC LIMIT 30"
                ).fetchall()
            ]
            gaps: list[float] = []
            for i in range(len(started) - 1):
                a = datetime.fromisoformat(started[i])
                b = datetime.fromisoformat(started[i + 1])
                gaps.append(abs((a - b).total_seconds()))

            by_post = conn.execute(
                """
                SELECT post_id, visa_class_id,
                       COUNT(*) AS checks,
                       SUM(CASE WHEN slots_count > 0 THEN 1 ELSE 0 END) AS with_slots
                FROM observations
                GROUP BY post_id, visa_class_id
                ORDER BY post_id, visa_class_id
                """
            ).fetchall()

            recent_obs = conn.execute(
                """
                SELECT observed_at, post_id, visa_class_id, slots_count, earliest_date, fingerprint
                FROM observations
                ORDER BY id DESC
                LIMIT 40
                """
            ).fetchall()

            recent_alerts = conn.execute(
                """
                SELECT a.sent_at, a.status, a.channel, o.post_id, o.visa_class_id, o.slots_count
                FROM alerts a
                JOIN observations o ON o.id = a.observation_id
                ORDER BY a.id DESC
                LIMIT 20
                """
            ).fetchall()

            last_check = conn.execute(
                """
                SELECT started_at, finished_at, source, status, error_summary, duration_ms
                FROM checks ORDER BY id DESC LIMIT 1
                """
            ).fetchone()

            by_hour = conn.execute(
                """
                SELECT CAST(strftime('%H', observed_at) AS INTEGER) AS hour_utc,
                       COUNT(*) AS checks,
                       SUM(CASE WHEN slots_count > 0 THEN 1 ELSE 0 END) AS with_slots
                FROM observations
                GROUP BY hour_utc
                ORDER BY hour_utc
                """
            ).fetchall()

            by_day = conn.execute(
                """
                SELECT substr(observed_at, 1, 10) AS day,
                       COUNT(*) AS checks,
                       SUM(CASE WHEN slots_count > 0 THEN 1 ELSE 0 END) AS with_slots
                FROM observations
                GROUP BY day
                ORDER BY day DESC
                LIMIT 60
                """
            ).fetchall()

            timeline = conn.execute(
                """
                SELECT substr(observed_at, 1, 13) AS hour_bucket,
                       SUM(slots_count) AS slots_sum,
                       SUM(CASE WHEN slots_count > 0 THEN 1 ELSE 0 END) AS openings,
                       COUNT(*) AS observations
                FROM observations
                GROUP BY hour_bucket
                ORDER BY hour_bucket DESC
                LIMIT 72
                """
            ).fetchall()

            by_source = conn.execute(
                """
                SELECT source, COUNT(*) AS checks,
                       SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok
                FROM checks
                GROUP BY source
                ORDER BY checks DESC
                """
            ).fetchall()

            slot_lifetimes = conn.execute(
                """
                SELECT fingerprint,
                       MIN(observed_at) AS first_seen,
                       MAX(observed_at) AS last_seen,
                       COUNT(*) AS sightings
                FROM observations
                WHERE slots_count > 0
                GROUP BY fingerprint
                ORDER BY last_seen DESC
                LIMIT 30
                """
            ).fetchall()

        probability = []
        for r in by_post:
            checks = int(r["checks"])
            with_slots = int(r["with_slots"])
            probability.append(
                {
                    "post_id": r["post_id"],
                    "visa_class_id": r["visa_class_id"],
                    "checks": checks,
                    "with_slots": with_slots,
                    "p_available": round((with_slots / checks) if checks else 0.0, 4),
                }
            )

        lifetimes = []
        for r in slot_lifetimes:
            first = datetime.fromisoformat(r["first_seen"])
            last = datetime.fromisoformat(r["last_seen"])
            lifetimes.append(
                {
                    "fingerprint": r["fingerprint"],
                    "first_seen": r["first_seen"],
                    "last_seen": r["last_seen"],
                    "sightings": int(r["sightings"]),
                    "lifetime_sec": max(0, int((last - first).total_seconds())),
                }
            )

        return {
            "checks_total": checks_total,
            "checks_ok": ok_checks,
            "checks_error": error_checks,
            "check_success_rate": round((ok_checks / checks_total), 4) if checks_total else 0.0,
            "observed_interval_sec_median": _median(gaps),
            "observed_interval_sec_p95": _percentile(gaps, 0.95),
            "last_check_duration_ms_median": _median(
                [float(r["duration_ms"]) for r in intervals if r["duration_ms"] is not None]
            ),
            "probability": probability,
            "probability_by_hour_utc": _bucket_rows(by_hour, "hour_utc"),
            "probability_by_day": list(reversed(_bucket_rows(by_day, "day"))),
            "timeline_hourly": list(reversed([dict(r) for r in timeline])),
            "checks_by_source": [dict(r) for r in by_source],
            "slot_lifetimes": lifetimes,
            "recent_observations": [dict(r) for r in recent_obs],
            "recent_alerts": [dict(r) for r in recent_alerts],
            "last_check": dict(last_check) if last_check else None,
        }

    def seed_history(
        self,
        *,
        posts: tuple[str, ...],
        visas: tuple[str, ...],
        days: int = 14,
        interval_minutes: int = 30,
    ) -> dict[str, int]:
        """Synthetic history for Phase 3 dashboard testing."""
        from datetime import timedelta

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        checks = 0
        observations = 0
        start = now - timedelta(days=days)
        cursor = start
        with self.connect() as conn:
            while cursor <= now:
                source = "seed:demo" if cursor.hour % 2 == 0 else "seed:auth_demo"
                cur = conn.execute(
                    """
                    INSERT INTO checks (started_at, finished_at, source, status, duration_ms)
                    VALUES (?, ?, ?, 'ok', ?)
                    """,
                    (
                        cursor.isoformat(),
                        (cursor + timedelta(seconds=2)).isoformat(),
                        source,
                        1500 + (cursor.minute % 7) * 100,
                    ),
                )
                check_id = int(cur.lastrowid)
                checks += 1
                for post in posts:
                    for visa in visas:
                        # Sparse openings: ~2% overall, higher on weekday UTC 13-15 for Mumbai H1B
                        open_slot = False
                        if post == "mumbai" and visa == "H1B" and cursor.weekday() < 5:
                            if cursor.hour in {13, 14, 15} and cursor.minute in {0, 30}:
                                open_slot = cursor.day % 5 == 0
                        if post == "chennai" and visa == "H4" and cursor.hour == 10:
                            open_slot = cursor.day % 9 == 0
                        slots = 2 if open_slot else 0
                        earliest = cursor.date().isoformat() if open_slot else None
                        fp = f"{post}|{visa}|{slots}|{earliest or '-'}"
                        conn.execute(
                            """
                            INSERT INTO observations (
                              check_id, observed_at, post_id, visa_class_id,
                              slots_count, earliest_date, fingerprint, raw_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                check_id,
                                cursor.isoformat(),
                                post,
                                visa,
                                slots,
                                earliest,
                                fp,
                                json.dumps({"seed": True}),
                            ),
                        )
                        observations += 1
                cursor += timedelta(minutes=interval_minutes)
        return {"checks": checks, "observations": observations}


def _bucket_rows(rows: list[sqlite3.Row], key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        checks = int(r["checks"])
        with_slots = int(r["with_slots"])
        out.append(
            {
                key: r[key],
                "checks": checks,
                "with_slots": with_slots,
                "p_available": round((with_slots / checks) if checks else 0.0, 4),
            }
        )
    return out


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2:
        return round(s[mid], 2)
    return round((s[mid - 1] + s[mid]) / 2, 2)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return round(s[0], 2)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[idx], 2)
