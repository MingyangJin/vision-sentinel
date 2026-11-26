"""Minimal liveness watchdog (ARCHITECTURE.md §5.6). No AI, two measures only:

1. Stream Hub health per camera. A drop (or recovery) is told to the Main Agent of every
   Topic subscribed to that camera, as an OrchestratorEvent; the Main Agent decides.
2. Main Agent review progress per Topic: the age of the newest tick it finished reviewing,
   read from state/ticks.jsonl. A live process is not enough; a session can be up while
   its turns time out or are refused for quota. Past the limit, the watchdog sends the one
   alert that does not come from the Main Agent: a direct system-health alert.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Callable

from vs_runtime.contracts import (
    AlertRequest,
    CameraStatus,
    Notifier,
    OrchestratorEvent,
    StreamHubClient,
    utcnow,
)
from vs_runtime.counters import TickLog, last_reviewed_at
from vs_runtime.topic import Topic

log = logging.getLogger(__name__)


def stale_limit_sec(topic: Topic) -> float:
    """Allowed review age: the configured limit plus one cadence and any digest interval."""
    cfg = topic.config
    return cfg.review_stale_sec + cfg.cadence_sec + (cfg.review_policy.digest_interval_sec or 0)


class Watchdog:
    def __init__(
        self,
        topics: list[Topic],
        hub: StreamHubClient | None,
        notifier: Notifier,
        *,
        on_event: Callable[[str, OrchestratorEvent], None] | None = None,
        clock: Callable[[], datetime] = utcnow,
        startup_grace: bool = True,
    ):
        self.topics = topics
        self.hub = hub
        self.notifier = notifier
        self.on_event = on_event  # (topic_id, event) -> deliver to that Topic's Main Agent
        self.clock = clock
        # In-process (beside the loop): measure from our own start at the earliest, so a
        # restart after a planned stop does not alert. Standalone: an old last review means
        # the runtime is down, so it counts at once.
        self.startup_grace = startup_grace
        self.started_at = clock()
        self.cameras: dict[str, CameraStatus] = {}
        self._stale: set[str] = set()  # topics already alerted for the current stale spell
        self._paused: set[str] = set()
        self._resumed_at: dict[str, datetime] = {}

    def review_age(self, topic: Topic, now: datetime) -> float:
        last = last_reviewed_at(TickLog(topic.ticks_path).records())
        floor = self._resumed_at.get(topic.id)
        if not last:
            base = max(self.started_at, floor) if floor else self.started_at
        else:
            base = max(last, self.started_at) if self.startup_grace else last
            if floor and floor > base:
                base = floor
        return max(0.0, (now - base).total_seconds())

    def check_once(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or self.clock()
        out: dict[str, Any] = {"checked_at": now.isoformat(), "cameras": {}, "topics": {},
                               "events": [], "alerts": []}
        stale: dict[str, float] = {}
        for t in list(self.topics):
            t.reload()
            if t.config.paused:
                self._paused.add(t.id)
                self._stale.discard(t.id)
                out["topics"][t.id] = {
                    "review_age_s": 0.0, "limit_s": stale_limit_sec(t),
                    "stale": False, "paused": True,
                }
                continue
            if t.id in self._paused:
                self._paused.discard(t.id)
                self._resumed_at[t.id] = now
            age, limit = self.review_age(t, now), stale_limit_sec(t)
            out["topics"][t.id] = {
                "review_age_s": round(age, 1), "limit_s": limit,
                "stale": age > limit, "paused": False,
            }
            if age > limit:
                stale[t.id] = age
            else:
                self._stale.discard(t.id)

        for cam in sorted({c for t in list(self.topics) for c in t.config.camera_ids}):
            status = self._camera_status(cam)
            out["cameras"][cam] = status
            prev = self.cameras.get(cam)
            self.cameras[cam] = status
            if status == "offline" and prev != "offline":
                kind, msg = "camera_drop", f"Camera {cam!r} is offline (was {prev or 'unknown'})."
            elif prev == "offline" and status != "offline":
                kind, msg = "camera_restored", f"Camera {cam!r} is back ({status})."
            else:
                continue
            for t in list(self.topics):
                if (
                    cam in t.config.camera_ids
                    and not t.config.paused
                    and t.id not in stale
                    and self.on_event
                ):
                    self.on_event(t.id, OrchestratorEvent(topic_id=t.id, kind=kind, message=msg))
                    out["events"].append({"topic_id": t.id, "kind": kind, "camera_id": cam})

        for t in list(self.topics):
            if t.id in stale and t.id not in self._stale:
                if self._alert_stale(t, stale[t.id], out["cameras"]):
                    self._stale.add(t.id)
                    out["alerts"].append(t.id)
            try:
                (t.state_dir / "health.json").write_text(json.dumps(
                    {"checked_at": out["checked_at"], **out["topics"][t.id],
                     "cameras": {c: out["cameras"][c] for c in t.config.camera_ids}}, indent=2))
            except OSError:
                pass
        return out

    def _camera_status(self, cam: str) -> CameraStatus:
        if self.hub is None:
            return "ok"
        try:
            return self.hub.health(cam).status
        except Exception as e:
            log.warning("health check failed for %s: %s", cam, e)
            return "offline"

    def _alert_stale(self, t: Topic, age: float, cameras: dict[str, str]) -> bool:
        cams = ", ".join(f"{c}: {cameras.get(c, '?')}" for c in t.config.camera_ids)
        try:
            self.notifier.send_alert(AlertRequest(
                topic_id=t.id, severity="urgent", source="watchdog", requires_ack=True,
                title=f"Vision Sentinel: Topic '{t.id}' is not being reviewed",
                body=(f"The Main Agent has not finished reviewing a tick for {age:.0f} s "
                      f"(limit {stale_limit_sec(t):.0f} s), so no alert decisions are being "
                      f"made for this Topic. Cameras: {cams}."),
                dedupe_key=f"watchdog:review-stale:{t.id}",
                camera_ids=list(t.config.camera_ids),
            ))
            return True
        except Exception:
            log.exception("watchdog could not send the system-health alert for %s", t.id)
            return False

    def run(self, stop: threading.Event, interval_sec: float = 15.0) -> None:
        while not stop.is_set():
            try:
                self.check_once()
            except Exception:
                log.exception("watchdog check failed")
            stop.wait(interval_sec)
