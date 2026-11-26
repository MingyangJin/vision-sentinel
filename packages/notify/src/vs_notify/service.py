"""The Notification Service core: dedupe/cooldown, fan-out to sinks, ack, feedback, media lookup.

``NotifyService`` implements ``vs_runtime.contracts.Notifier`` in-process; the HTTP server
and ``NotifyClient`` wrap the same object.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any

from vs_runtime.contracts import AlertRecord, AlertRequest, FeedbackRequest, NotificationSink

from .sinks import wants
from .store import Store, to_dt

log = logging.getLogger("vs_notify")

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
THUMB_CANDIDATES = (
    "frame.jpg",
    "thumbnail.jpg",
    "contact_sheet.jpg",
    "frame.png",
    "thumbnail.png",
    "contact_sheet.png",
)


class NotFound(KeyError):
    pass


def safe_name(value: str | None) -> bool:
    return bool(value) and bool(SAFE_NAME.match(value)) and ".." not in value


def _safe_rel_path(rel: str) -> bool:
    """True if ``rel`` is a clean relative path with no traversal or absolute prefix."""
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel or "\x00" in rel:
        return False
    parts = PurePosixPath(rel).parts
    return bool(parts) and all(p not in ("", ".", "..") for p in parts)


def _confined_file(bundle_dir: Path, rel: str) -> Path | None:
    """Resolve ``rel`` inside ``bundle_dir`` and ensure it stays inside (blocks symlinks out)."""
    if not _safe_rel_path(rel):
        return None
    root = bundle_dir.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


def topics_root_resolver(root: str | Path | None) -> Callable[[str], Path | None]:
    def resolve(topic_id: str) -> Path | None:
        if root is None or not safe_name(topic_id):
            return None
        path = Path(root) / topic_id
        return path if path.is_dir() else None

    return resolve


class NotifyService:
    def __init__(
        self,
        store: Store,
        sinks: list[NotificationSink] | None = None,
        topic_dir_for: Callable[[str], Path | None] | None = None,
        on_feedback: Callable[[FeedbackRequest, AlertRecord], None] | None = None,
        play_alert: Callable[[str, Path, float], None] | None = None,
        default_cooldown_sec: float = 300.0,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.sinks = list(sinks or [])
        self.topic_dir_for = topic_dir_for or topics_root_resolver(None)
        self.on_feedback = on_feedback
        self.play_alert = play_alert
        self.default_cooldown_sec = default_cooldown_sec
        self.clock = clock
        self._bind_sinks()

    def _bind_sinks(self) -> None:
        for sink in self.sinks:
            if hasattr(sink, "bind_store"):
                sink.bind_store(self.store)
            if hasattr(sink, "bind_service"):
                sink.bind_service(self)
            if self.play_alert is not None and hasattr(sink, "bind_play_alert"):
                sink.bind_play_alert(self.play_alert)

    # Notifier

    def send_alert(self, request: AlertRequest) -> AlertRecord:
        now = self.clock()
        if request.dedupe_key:
            cooldown = request.cooldown_sec
            if cooldown is None:
                cooldown = self.default_cooldown_sec
            prior = self.store.last_sent(request.topic_id, request.dedupe_key, now - cooldown)
            if prior and cooldown > 0:
                log.info("alert deduped: %s/%s within %ss of %s",
                         request.topic_id, request.dedupe_key, cooldown, prior)
                original = self.store.get_alert(prior)
                return original.model_copy(update={"request": request, "status": "deduped"})

        alert_id = self.store.insert_alert(request, now)
        record = self.store.get_alert(alert_id)
        deliveries = self._fan_out(record)
        failed = deliveries and all(v != "ok" for v in deliveries.values())
        self.store.finish_alert(alert_id, "failed" if failed else "sent", deliveries)
        return self.store.get_alert(alert_id)

    def _fan_out(self, record: AlertRecord) -> dict[str, str]:
        sinks = [s for s in self.sinks if wants(s, record)]
        if not sinks:
            return {}

        def deliver(sink: NotificationSink) -> str:
            try:
                sink.send(record)
                return "ok"
            except Exception as exc:  # one sink's failure never blocks another
                log.warning("sink %s failed for alert %s: %s", sink.name, record.id, exc)
                return f"error: {type(exc).__name__}: {exc}"[:500]

        with ThreadPoolExecutor(max_workers=len(sinks)) as pool:
            results = list(pool.map(deliver, sinks))
        return {s.name: r for s, r in zip(sinks, results)}

    # reads

    def get(self, alert_id: str) -> AlertRecord:
        record = self.store.get_alert(alert_id)
        if record is None:
            raise NotFound(alert_id)
        return record

    def list(self, **filters) -> list[AlertRecord]:
        return self.store.list_alerts(**filters)

    # media resolution (strictly confined to saved/ or bundles/<bundle_id>/)

    def _bundle_dir(self, topic_id: str, bundle_id: str | None) -> Path | None:
        if not safe_name(topic_id) or not safe_name(bundle_id):
            return None
        topic_dir = self.topic_dir_for(topic_id)
        if topic_dir is None:
            return None
        state_root = (topic_dir / "state").resolve()
        for sub in ("saved", "bundles"):
            cand = (topic_dir / "state" / sub / bundle_id).resolve()
            if cand.is_dir() and cand.is_relative_to(state_root):
                return cand
        return None

    def _as_record(self, alert_or_id: AlertRecord | str) -> AlertRecord:
        return alert_or_id if isinstance(alert_or_id, AlertRecord) else self.get(alert_or_id)

    def resolve_clip(self, alert_or_id: AlertRecord | str) -> Path | None:
        record = self._as_record(alert_or_id)
        r = record.request
        bdir = self._bundle_dir(r.topic_id, r.bundle_id)
        if bdir is None:
            return None
        if r.clip_ref is not None:
            if not _safe_rel_path(r.clip_ref):
                return None  # never fall back on a traversal/malformed clip_ref
            hit = _confined_file(bdir, r.clip_ref)
            if hit is not None and hit.suffix.lower() in VIDEO_EXTS:
                return hit
        for cam in r.camera_ids:
            if safe_name(cam):
                hit = _confined_file(bdir, f"{cam}/clip.mp4")
                if hit is not None:
                    return hit
        hit = _confined_file(bdir, "clip.mp4")
        if hit is not None:
            return hit
        for child in sorted(bdir.iterdir()):
            if child.is_dir() and safe_name(child.name):
                hit = _confined_file(bdir, f"{child.name}/clip.mp4")
                if hit is not None:
                    return hit
        return None

    def resolve_thumbnail(self, alert_or_id: AlertRecord | str) -> Path | None:
        record = self._as_record(alert_or_id)
        r = record.request
        bdir = self._bundle_dir(r.topic_id, r.bundle_id)
        if bdir is None:
            return None
        if r.clip_ref is not None:
            if not _safe_rel_path(r.clip_ref):
                return None
            hit = _confined_file(bdir, r.clip_ref)
            if hit is not None and hit.suffix.lower() in IMAGE_EXTS:
                return hit
        for cam in r.camera_ids:
            if safe_name(cam):
                for name in THUMB_CANDIDATES:
                    hit = _confined_file(bdir, f"{cam}/{name}")
                    if hit is not None:
                        return hit
        for name in THUMB_CANDIDATES:
            hit = _confined_file(bdir, name)
            if hit is not None:
                return hit
        for child in sorted(bdir.iterdir()):
            if child.is_dir() and safe_name(child.name):
                for name in THUMB_CANDIDATES:
                    hit = _confined_file(bdir, f"{child.name}/{name}")
                    if hit is not None:
                        return hit
        return None

    def alert_view(self, record: AlertRecord) -> dict[str, Any]:
        """Serialize an ``AlertRecord`` with ``has_clip`` and ``has_thumbnail`` for UI/mobile."""
        data = record.model_dump(mode="json")
        data["has_clip"] = self.resolve_clip(record) is not None
        data["has_thumbnail"] = self.resolve_thumbnail(record) is not None
        return data

    # ack: "seen"

    def ack(self, alert_id: str, by: str | None = None) -> AlertRecord:
        self.get(alert_id)
        self.store.ack(alert_id, self.clock(), by)
        return self.get(alert_id)

    # feedback: "was this right" — separate from ack, may come later or never

    def feedback(self, alert_id: str, rating: str, note: str = "", by: str = "") -> dict:
        record = self.get(alert_id)
        fb = FeedbackRequest(alert_id=alert_id, rating=rating, note=note, by=by,
                             at=to_dt(self.clock()))
        r = record.request
        topic_dir = self.topic_dir_for(r.topic_id)
        saved = self._pin_bundle(topic_dir, r.bundle_id) if topic_dir else None
        self.store.add_feedback(fb, pinned=saved is not None)
        if topic_dir:
            line = {
                "alert_id": alert_id,
                "topic_id": r.topic_id,
                "rating": fb.rating,
                "note": fb.note,
                "who": fb.by,
                "timestamp": fb.at.isoformat(),
                "bundle_id": r.bundle_id,
                "rubric_version": r.rubric_version,
                "tool_versions": r.tool_versions,
                "severity": r.severity,
                "title": r.title,
                "dedupe_key": r.dedupe_key,
                "saved_bundle": str(saved.relative_to(topic_dir)) if saved else None,
            }
            state = topic_dir / "state"
            state.mkdir(exist_ok=True)
            with open(state / "feedback.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        else:
            log.warning("feedback for alert %s: no directory for topic %r; logged to SQLite only",
                        alert_id, r.topic_id)
        record = self.get(alert_id)
        if self.on_feedback:
            try:
                self.on_feedback(fb, record)
            except Exception:
                log.exception("on_feedback hook failed")
        return {"feedback": fb, "pinned": saved is not None,
                "saved_bundle": str(saved) if saved else None, "alert": record}

    @staticmethod
    def _pin_bundle(topic_dir: Path, bundle_id: str | None) -> Path | None:
        """Copy the rated bundle into ``state/saved/`` so rolling expiry never removes it."""
        if not safe_name(bundle_id):
            return None
        dest = topic_dir / "state" / "saved" / bundle_id
        if dest.is_dir():
            return dest
        src = topic_dir / "state" / "bundles" / bundle_id
        if not src.is_dir():
            return None
        shutil.copytree(src, dest)
        return dest

    # devices (M3 push targets)

    def register_device(self, platform: str, token: str, name: str = "") -> dict:
        return self.store.register_device(platform, token, name, self.clock())
