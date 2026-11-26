"""Deterministic runtime counters from state/ticks.jsonl (ARCHITECTURE.md §5.3).

ticks.jsonl is append-only and holds one JSON object per line with a ``type``:

- ``tick``:   one Vision Sub-Agent pass: at (window end), bundle_id, status, conditions,
              degraded, report (full VisionReport), usage (vision tokens)
- ``review``: one Main Agent turn: at, kind, bundle_ids, ok, late, error, usage, actions
- ``alert``:  one send_notification: at, alert_id, dedupe_key, severity, status, bundle_id
- ``action``: one dispatched Main Agent action: at, name, ok, result or error

The Main Agent never has to recall these figures; they are attached to every report.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from vs_runtime.contracts import ConditionStreak, RuntimeCounters, utcnow


def _ts(value: str | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


class TickLog:
    """Append-only JSONL log, safe to append from several threads of one process."""

    _locks: dict[Path, threading.Lock] = {}

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = self._locks.setdefault(self.path.resolve(), threading.Lock())

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        record = {"at": utcnow().isoformat(), **record}
        line = json.dumps(record, default=str, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(line + "\n")
        return record

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn last line after a crash
        return out


def reviewed_bundle_ids(records: list[dict[str, Any]]) -> set[str]:
    done: set[str] = set()
    for r in records:
        if r.get("type") == "review" and r.get("ok"):
            done.update(r.get("bundle_ids", []))
    return done


def unreviewed_ticks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tick records no successful Main Agent turn has covered, oldest first."""
    done = reviewed_bundle_ids(records)
    return [r for r in records if r.get("type") == "tick" and r["bundle_id"] not in done]


def last_reviewed_at(records: list[dict[str, Any]]) -> datetime | None:
    """Time of the newest tick the Main Agent finished reviewing (the watchdog's measure)."""
    done = reviewed_bundle_ids(records)
    times = [_ts(r["at"]) for r in records if r.get("type") == "tick" and r["bundle_id"] in done]
    return max(times, default=None)


def _streak(ticks: list[dict[str, Any]], holds) -> ConditionStreak | None:
    count, since = 0, None
    for t in reversed(ticks):
        if not holds(t):
            break
        count, since = count + 1, _ts(t["at"])
    if not count:
        return None
    return ConditionStreak(
        count=count, since=since, duration_s=(_ts(ticks[-1]["at"]) - since).total_seconds()
    )


def compute_counters(records: list[dict[str, Any]], now: datetime | None = None) -> RuntimeCounters:
    now = now or utcnow()
    ticks = sorted((r for r in records if r.get("type") == "tick"), key=lambda r: _ts(r["at"]))
    counters = RuntimeCounters(
        computed_at=now, ticks_total=len(ticks), last_reviewed_at=last_reviewed_at(records)
    )
    if ticks:
        status = ticks[-1]["status"]
        counters.status = status
        counters.status_streak = _streak(ticks, lambda t: t["status"] == status)
    # Error ticks carry no observation, so they neither extend nor break a condition.
    judged = [t for t in ticks if t["status"] != "error"]
    if judged:
        for cond in judged[-1].get("conditions", []):
            counters.conditions[cond] = _streak(judged, lambda t: cond in t.get("conditions", []))
    for r in records:
        if r.get("type") == "alert" and r.get("status") == "sent":
            age = (now - _ts(r["at"])).total_seconds()
            for key in ("*", r.get("dedupe_key")):
                if key:
                    counters.seconds_since_last_alert[key] = min(
                        age, counters.seconds_since_last_alert.get(key, age)
                    )
    return counters
