"""SQLite persistence: alerts (with their request and delivery results), acks, feedback, devices."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from vs_runtime.contracts import AlertRecord, AlertRequest, FeedbackRequest

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    created_ts REAL NOT NULL,
    topic_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    dedupe_key TEXT,
    status TEXT NOT NULL,
    request TEXT NOT NULL,
    deliveries TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS alerts_dedupe ON alerts (topic_id, dedupe_key, created_ts);
CREATE INDEX IF NOT EXISTS alerts_created ON alerts (created_ts);
CREATE TABLE IF NOT EXISTS acks (
    alert_id TEXT PRIMARY KEY REFERENCES alerts(id),
    ts REAL NOT NULL,
    by TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL REFERENCES alerts(id),
    ts REAL NOT NULL,
    rating TEXT NOT NULL,
    note TEXT NOT NULL,
    by TEXT NOT NULL,
    pinned INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    token TEXT NOT NULL,
    name TEXT NOT NULL,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    UNIQUE (platform, token)
);
"""


def to_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


class Store:
    """One SQLite file; a lock serializes access from the event loop and the threadpool."""

    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _run(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._db.execute(sql, args)

    # alerts

    def last_sent(self, topic_id: str, dedupe_key: str, since_ts: float) -> str | None:
        """Id of the newest successfully sent alert with this key since ``since_ts``."""
        rows = self._rows(
            "SELECT id FROM alerts WHERE topic_id=? AND dedupe_key=? AND status='sent' "
            "AND created_ts>=? ORDER BY created_ts DESC LIMIT 1",
            (topic_id, dedupe_key, since_ts),
        )
        return rows[0]["id"] if rows else None

    def insert_alert(self, request: AlertRequest, ts: float) -> str:
        alert_id = uuid.uuid4().hex
        self._run(
            "INSERT INTO alerts (id, created_ts, topic_id, severity, dedupe_key, status, request) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (alert_id, ts, request.topic_id, request.severity, request.dedupe_key,
             request.model_dump_json()),
        )
        return alert_id

    def finish_alert(self, alert_id: str, status: str, deliveries: dict[str, str]) -> None:
        self._run("UPDATE alerts SET status=?, deliveries=? WHERE id=?",
                  (status, json.dumps(deliveries), alert_id))

    def get_alert(self, alert_id: str) -> AlertRecord | None:
        rows = self._rows("SELECT * FROM alerts WHERE id=?", (alert_id,))
        return self._record(rows[0]) if rows else None

    def list_alerts(
        self,
        topic_id: str | None = None,
        severity: str | None = None,
        unacked: bool | None = None,
        since_ts: float | None = None,
        limit: int = 50,
    ) -> list[AlertRecord]:
        where, args = [], []
        if topic_id:
            where.append("a.topic_id=?")
            args.append(topic_id)
        if severity:
            where.append("a.severity=?")
            args.append(severity)
        if since_ts is not None:
            where.append("a.created_ts>=?")
            args.append(since_ts)
        if unacked is not None:
            where.append("k.alert_id IS NULL" if unacked else "k.alert_id IS NOT NULL")
        sql = "SELECT a.* FROM alerts a LEFT JOIN acks k ON k.alert_id=a.id"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY a.created_ts DESC LIMIT ?"
        return [self._record(r) for r in self._rows(sql, (*args, limit))]

    def _record(self, row: sqlite3.Row) -> AlertRecord:
        ack = self._rows("SELECT ts, by FROM acks WHERE alert_id=?", (row["id"],))
        feedback = self._rows(
            "SELECT ts, rating, note, by FROM feedback WHERE alert_id=? ORDER BY id", (row["id"],)
        )
        return AlertRecord(
            id=row["id"],
            request=AlertRequest.model_validate_json(row["request"]),
            created_at=to_dt(row["created_ts"]),
            status=row["status"] if row["status"] != "pending" else "sent",
            deliveries=json.loads(row["deliveries"]),
            acked_at=to_dt(ack[0]["ts"]) if ack else None,
            acked_by=ack[0]["by"] if ack else None,
            feedback=[
                FeedbackRequest(alert_id=row["id"], rating=f["rating"], note=f["note"],
                                by=f["by"], at=to_dt(f["ts"]))
                for f in feedback
            ],
        )

    # acks and feedback (kept apart: ack means "seen", feedback means "was this right")

    def ack(self, alert_id: str, ts: float, by: str | None) -> None:
        """Record the first acknowledgment; later acks keep the original."""
        self._run("INSERT OR IGNORE INTO acks (alert_id, ts, by) VALUES (?, ?, ?)", (alert_id, ts, by))

    def add_feedback(self, fb: FeedbackRequest, pinned: bool) -> None:
        self._run(
            "INSERT INTO feedback (alert_id, ts, rating, note, by, pinned) VALUES (?, ?, ?, ?, ?, ?)",
            (fb.alert_id, fb.at.timestamp(), fb.rating, fb.note, fb.by, int(pinned)),
        )

    # devices

    def register_device(self, platform: str, token: str, name: str, ts: float) -> dict:
        self._run(
            "INSERT INTO devices (id, platform, token, name, created_ts, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (platform, token) "
            "DO UPDATE SET name=excluded.name, updated_ts=excluded.updated_ts",
            (uuid.uuid4().hex, platform, token, name, ts, ts),
        )
        row = self._rows(
            "SELECT id, platform, name, created_ts FROM devices WHERE platform=? AND token=?",
            (platform, token),
        )[0]
        return {"id": row["id"], "platform": row["platform"], "name": row["name"],
                "registered_at": to_dt(row["created_ts"]).isoformat()}

    def devices(self, platform: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM devices", ()
        if platform:
            sql, args = sql + " WHERE platform=?", (platform,)
        return [dict(r) for r in self._rows(sql, args)]

    def remove_device(self, platform: str, token: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM devices WHERE platform=? AND token=?", (platform, token)
            )
            return cur.rowcount > 0
