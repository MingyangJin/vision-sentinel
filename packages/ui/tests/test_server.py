"""Offline test suite for the Unified Web UI (vs_ui).

Covers:
- Built static SPA serving + SPA fallback + API 404 isolation
- Non-loopback bind security warning (§5.7)
- Camera listing, snapshot, MJPEG stream, rolling segments, clip slicing, and ZERO credential exposure
- Topic presets, Topic creation, mid-flow edits (cameras, cadence, window, models, pause/resume,
  speaker opt-in, human-owned goal.md), TopicManager integration
- Vision Sub-Agent tick feed + token totals + rubric/notes/tools inspection
- Strict path-traversal and symlink-escape protection on bundle & segment media
- Main Agent chat endpoint (reply, actions, token usage, attachment validation)
- Alert Inbox endpoints (list, ack, thumbs up/down feedback, thumbnail/clip) both in-process
  (NotifyService) and over loopback HTTP proxy (vs_notify FastAPI server)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vs_notify import NotifyService, Store
from vs_notify.server import create_app as create_notify_app
from vs_runtime.contracts import (
    AlertRequest,
    CameraConfig,
    ObservationAsset,
    OrchestratorAction,
    OrchestratorResult,
    StreamHealth,
    TokenUsage,
    utcnow,
)
from vs_runtime.fakes import FakeHarness
from vs_runtime.topic import GOAL, Topic, init_topic
from vs_streamhub import StreamHub

from vs_ui.server import create_app, is_loopback_host, warn_if_non_loopback


FAKE_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64


class FakeLiveHub:
    """Minimal StreamHub test double exposing snapshot, MJPEG, segments, clip, and health."""

    def __init__(self, recordings_root: Path):
        self.recordings_root = recordings_root
        self._cameras = {
            "porch": CameraConfig(
                camera_id="porch",
                adapter="rtsp",
                source="rtsp://192.0.2.77:554/secret-path",
                credential_ref="SECRET_PORCH_PASS",
                speaker_enabled=True,
                volume_cap=0.25,
            ),
            "yard": CameraConfig(
                camera_id="yard",
                adapter="file",
                source="/private/local/video.mp4",
                speaker_enabled=False,
            ),
        }
        self.adapters = {
            cid: type(
                "FakeAdapter",
                (),
                {
                    "config": cfg,
                    "live_url": "rtsp://127.0.0.1:8554/" + cid if cid == "porch" else None,
                },
            )()
            for cid, cfg in self._cameras.items()
        }
        self._status = {"porch": "ok", "yard": "degraded"}
        # Seed a rolling segment on disk for porch
        seg_dir = self.segment_dir("porch")
        seg_dir.mkdir(parents=True, exist_ok=True)
        (seg_dir / "2026-11-25_12-00-00.mp4").write_bytes(FAKE_MP4)
        (seg_dir / "2026-11-25_12-00-02.mp4").write_bytes(FAKE_MP4)

    @property
    def camera_ids(self) -> list[str]:
        return list(self._cameras)

    def segment_dir(self, camera_id: str) -> Path:
        return self.recordings_root / camera_id

    def health(self, camera_id: str) -> StreamHealth:
        if camera_id not in self._cameras:
            return StreamHealth(camera_id=camera_id, status="offline", detail="unknown")
        return StreamHealth(
            camera_id=camera_id,
            status=self._status[camera_id],
            last_segment_at=utcnow(),
            detail="healthy" if self._status[camera_id] == "ok" else "slight lag",
        )

    def latest_snapshot(self, camera_id: str) -> bytes | None:
        if camera_id == "porch":
            return FAKE_JPEG
        return None

    async def iter_mjpeg(self, camera_id: str, *, fps: float = 2.0):
        for _ in range(3):
            yield FAKE_JPEG

    def extract_clip(self, camera_id: str, start: datetime, end: datetime, out_path: Path) -> Path | None:
        if camera_id != "porch":
            return None
        out_path.write_bytes(FAKE_MP4)
        return out_path

    def extract_window(
        self, camera_id: str, start: datetime, end: datetime, out_dir: Path, kinds: set[str]
    ) -> list[ObservationAsset]:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "clip.mp4").write_bytes(FAKE_MP4)
        return [ObservationAsset(camera_id=camera_id, kind="video", path="clip.mp4", mime="video/mp4")]


class RecordingChatHarness(FakeHarness):
    def __init__(self):
        super().__init__()
        self.chat_calls: list[tuple[str, str, list[str] | None]] = []

    def session_info(self, topic_dir: Path) -> dict:
        return {
            "harness": "fake",
            "active": True,
            "conversation_id": f"conv-{Path(topic_dir).name}",
            "turns": len(self.chat_calls),
            "total_turns": len(self.chat_calls),
            "usage": {"input": 1200, "cached": 800, "output": 140, "thinking": 50, "duration_s": 1.4},
            "cumulative_usage": {"input": 1200, "cached": 800, "output": 140, "thinking": 50, "duration_s": 1.4},
        }

    def chat_with_orchestrator(self, topic, message: str, attachments: list[str] | None = None):
        self.chat_calls.append((topic.topic_id, message, attachments))
        return OrchestratorResult(
            ok=True,
            reply=f"Updated monitoring rules for {topic.topic_id}: {message[:60]}",
            actions=[
                OrchestratorAction(
                    name="update_vision_rubric",
                    args={"content": "# Updated rubric\nIgnore tree shadows.\n"},
                )
            ],
            usage=TokenUsage(input=1200, cached=800, output=140, thinking=50, duration_s=1.4),
        )


def _seed_topic(topics_root: Path, topic_id: str = "porch-watch") -> Topic:
    t = init_topic(
        topics_root / topic_id,
        camera_ids=["porch"],
        goal="Alert when a parcel arrives at the porch.",
        topic_id=topic_id,
        cadence_sec=60,
        window_sec=20,
    )
    t.write_tool("motion.py", "import json\nprint(json.dumps({'motion': 0.12}))\n")
    # Seed a bundle with media
    bdir = t.bundles_dir / "20261125T120000Z-abc123"
    (bdir / "porch").mkdir(parents=True, exist_ok=True)
    (bdir / "porch" / "frame.jpg").write_bytes(FAKE_JPEG)
    (bdir / "porch" / "clip.mp4").write_bytes(FAKE_MP4)
    (bdir / "bundle.json").write_text(json.dumps({"bundle_id": bdir.name}))
    # Seed ticks.jsonl
    t.state_dir.mkdir(exist_ok=True)
    tick_rec = {
        "type": "tick",
        "at": "2026-11-25T12:00:00+00:00",
        "bundle_id": bdir.name,
        "status": "notable",
        "conditions": ["package_present"],
        "degraded": False,
        "usage": {"input": 22000, "cached": 15000, "output": 320, "thinking": 180, "duration_s": 5.2},
        "report": {
            "bundle_id": bdir.name,
            "topic_id": topic_id,
            "status": "notable",
            "summary": "A brown parcel was placed next to the front door.",
            "observations": ["Parcel visible near mat"],
            "conditions": ["package_present"],
            "confidence": 0.92,
            "suggest_review": True,
            "assets_read": ["porch/frame.jpg"],
            "camera_status": {"porch": "ok"},
            "degraded": False,
            "usage": {"input": 22000, "cached": 15000, "output": 320, "thinking": 180, "duration_s": 5.2},
            "created_at": "2026-11-25T12:00:00+00:00",
        },
    }
    review_rec = {
        "type": "review",
        "kind": "report",
        "ok": True,
        "bundle_ids": [bdir.name],
        "usage": {"input": 3500, "cached": 18000, "output": 180, "thinking": 90, "duration_s": 3.1},
        "actions": ["send_notification"],
    }
    t.ticks_path.write_text(json.dumps(tick_rec) + "\n" + json.dumps(review_rec) + "\n")
    return t


@pytest.fixture
def env(tmp_path: Path):
    topics_root = tmp_path / "topics"
    topics_root.mkdir()
    topic = _seed_topic(topics_root, "porch-watch")
    hub = FakeLiveHub(tmp_path / "recordings")
    harness = RecordingChatHarness()
    store = Store(tmp_path / "notify.sqlite")
    notify = NotifyService(
        store,
        sinks=[],
        topic_dir_for=lambda tid: topics_root / tid if (topics_root / tid).is_dir() else None,
    )
    app = create_app(
        hub=hub,
        notifier=notify,
        topics_root=topics_root,
        harness=harness,
        bind_host="127.0.0.1",
    )
    return {
        "client": TestClient(app),
        "app": app,
        "topics_root": topics_root,
        "topic": topic,
        "hub": hub,
        "harness": harness,
        "notify": notify,
        "tmp_path": tmp_path,
    }


# --- 1. Static SPA + non-loopback warning --------------------------------------


def test_spa_static_serving_and_api_404(env):
    client: TestClient = env["client"]
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Vision Sentinel" in r.text

    # SPA fallback for client-side route
    r_spa = client.get("/topics/porch-watch")
    assert r_spa.status_code == 200
    assert "text/html" in r_spa.headers["content-type"]

    # Unknown API route must return JSON 404, not HTML
    r_api = client.get("/api/does-not-exist")
    assert r_api.status_code == 404
    assert r_api.json()["detail"] == "not found"


def test_non_loopback_bind_warning(caplog, env):
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("0.0.0.0") is False
    assert is_loopback_host("192.168.1.50") is False

    with caplog.at_level(logging.WARNING, logger="vs_ui.server"):
        assert warn_if_non_loopback("127.0.0.1") is False
        assert not caplog.records
        assert warn_if_non_loopback("0.0.0.0") is True
        assert any("NO login" in rec.message and "0.0.0.0" in rec.message for rec in caplog.records)

    exposed_app = create_app(hub=env["hub"], topics_root=env["topics_root"], bind_host="0.0.0.0")
    health = TestClient(exposed_app).get("/api/health").json()
    assert health["non_loopback_warning"] is True
    assert health["bind_host"] == "0.0.0.0"


def test_loopback_bind_rejects_foreign_host_header(env):
    """No login, so on a loopback bind the Host header must be loopback (DNS rebinding)."""
    app = create_app(hub=env["hub"], topics_root=env["topics_root"], enforce_host=True,
                     allowed_hosts=["cams.tailnet.example"])
    for base, code in (
        ("http://127.0.0.1:8080", 200), ("http://localhost:8080", 200), ("http://[::1]:8080", 200),
        ("http://cams.tailnet.example", 200), ("http://cams.tailnet.example:8443", 200),
        ("http://evil.example:8080", 403), ("http://testserver", 403),
    ):
        c = TestClient(app, base_url=base)
        assert c.get("/api/health").status_code == code, base
        assert c.get("/api/cameras").status_code == code, base
        assert c.get("/").status_code == code, base


# --- 2. Cameras, live stream, snapshot, segments, clip & ZERO credential leak --


def test_cameras_never_expose_urls_or_credentials(env, tmp_path: Path):
    client: TestClient = env["client"]
    r = client.get("/api/cameras")
    assert r.status_code == 200
    raw = r.text
    cams = r.json()
    assert len(cams) == 2
    assert {c["camera_id"] for c in cams} == {"porch", "yard"}
    # Zero URLs, IPs, file sources, or credential refs in output
    for forbidden in ("192.0.2.77", "rtsp://", "secret-path", "SECRET_PORCH_PASS", "/private/local"):
        assert forbidden not in raw

    # Also test with a real StreamHub instance holding an RTSP camera with credential_ref
    real_hub = StreamHub(
        [
            CameraConfig(
                camera_id="driveway",
                adapter="rtsp",
                source="rtsp://192.0.2.42:554/h264Preview_01_main",
                credential_ref="DRIVEWAY_CREDS",
            )
        ],
        recordings_root=tmp_path / "real_rec",
        mediamtx="never",
    )
    real_app = create_app(hub=real_hub, topics_root=env["topics_root"])
    real_res = TestClient(real_app).get("/api/cameras")
    assert real_res.status_code == 200
    assert "192.0.2.42" not in real_res.text
    assert "DRIVEWAY_CREDS" not in real_res.text
    assert "h264Preview" not in real_res.text
    assert real_res.json()[0]["camera_id"] == "driveway"

    # A failing camera writer must not carry the camera address into the Web UI either:
    # FFmpeg prints the URL it could not open, and that line feeds the health detail.
    import sys
    import time

    adapter = real_hub.adapter("driveway")
    script = ("import sys; sys.stderr.write('Connection to tcp://192.0.2.42:554 failed: "
              "rtsp://192.0.2.42:554/h264Preview_01_main\\n'); sys.exit(1)")  # fmt: skip
    adapter.needs_writer = lambda: True
    adapter.writer_command = lambda: ([sys.executable, "-c", script], None)
    adapter.start()
    try:
        deadline = time.monotonic() + 10
        while not (adapter._supervisor and adapter._supervisor.last_error) and time.monotonic() < deadline:
            time.sleep(0.05)
        failing = TestClient(real_app).get("/api/cameras")
    finally:
        adapter.stop()
    detail = failing.json()[0]["health"]["detail"]
    assert "last_error=" in detail and "failed" in detail
    for forbidden in ("192.0.2.42", "rtsp://", "tcp://", "h264Preview"):
        assert forbidden not in failing.text


def test_camera_snapshot_mjpeg_segments_and_clip(env):
    client: TestClient = env["client"]

    # Snapshot
    snap = client.get("/api/cameras/porch/snapshot.jpg")
    assert snap.status_code == 200
    assert snap.headers["content-type"].startswith("image/jpeg")
    assert snap.content == FAKE_JPEG

    assert client.get("/api/cameras/yard/snapshot.jpg").status_code == 503
    assert client.get("/api/cameras/nope/snapshot.jpg").status_code == 404

    # MJPEG stream
    stream = client.get("/api/cameras/porch/stream?fps=10&max_frames=2")
    assert stream.status_code == 200
    assert "multipart/x-mixed-replace" in stream.headers["content-type"]
    assert b"--frame\r\n" in stream.content
    assert FAKE_JPEG in stream.content

    # Segments listing & playback
    segs = client.get("/api/cameras/porch/segments").json()
    assert segs["camera_id"] == "porch"
    assert len(segs["segments"]) == 2
    seg_name = segs["segments"][0]["name"]
    seg_resp = client.get(f"/api/cameras/porch/segments/{seg_name}")
    assert seg_resp.status_code == 200
    assert seg_resp.headers["content-type"].startswith("video/mp4")
    assert seg_resp.content == FAKE_MP4

    # Segment path-traversal & symlink escape blocked
    outside = env["tmp_path"] / "secret.mp4"
    outside.write_bytes(b"TOPSECRET")
    sym = env["hub"].segment_dir("porch") / "2026-11-25_12-00-09.mp4"
    sym.symlink_to(outside)
    assert client.get("/api/cameras/porch/segments/2026-11-25_12-00-09.mp4").status_code == 403
    assert client.get("/api/cameras/porch/segments/..%2Fsecret.mp4").status_code in (400, 404)

    # Clip extraction
    clip = client.get("/api/cameras/porch/clip?seconds=15")
    assert clip.status_code == 200
    assert clip.headers["content-type"].startswith("video/mp4")
    assert clip.content == FAKE_MP4
    assert client.get("/api/cameras/yard/clip?seconds=15").status_code == 404


# --- 3. Topics CRUD, presets, mid-flow edits, goal.md, ticks & bundle security -


def test_presets_create_and_midflow_edit_topic(env):
    client: TestClient = env["client"]

    presets = client.get("/api/presets").json()
    preset_ids = {p["id"] for p in presets}
    assert {"package-watch", "door-left-open", "pet-watch", "multi-cam-watch", "custom"} <= preset_ids

    # Create from preset
    created = client.post(
        "/api/topics",
        json={
            "id": "front-gate",
            "camera_ids": ["porch"],
            "preset": "door-left-open",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["id"] == "front-gate"
    assert body["camera_ids"] == ["porch"]
    assert "door" in body["goal"].lower() or "gate" in body["goal"].lower()

    # Duplicate rejected
    dup = client.post("/api/topics", json={"id": "front-gate", "camera_ids": ["porch"]})
    assert dup.status_code == 409

    # Mid-flow edit: M:N cameras, cadence, window, models, paused, speaker_enabled, and goal.md
    patched = client.patch(
        "/api/topics/front-gate",
        json={
            "camera_ids": ["porch", "yard"],
            "cadence_sec": 30,
            "window_sec": 12,
            "main_model": {"model": "gemini-4-neon-high", "effort": "high"},
            "vision_model": {"model": "gemini-4-hydrogen-medium", "effort": "medium"},
            "paused": True,
            "speaker_enabled": True,
            "goal": "Human-updated goal: watch both porch and yard after 9 PM.",
        },
    )
    assert patched.status_code == 200
    p = patched.json()
    assert p["camera_ids"] == ["porch", "yard"]
    assert p["cadence_sec"] == 30
    assert p["window_sec"] == 12
    assert p["main_model"]["model"] == "gemini-4-neon-high"
    assert p["vision_model"]["model"] == "gemini-4-hydrogen-medium"
    assert p["paused"] is True
    assert p["speaker_enabled"] is True
    assert "after 9 PM" in p["goal"]
    # Verify goal.md persisted on disk
    goal_disk = (env["topics_root"] / "front-gate" / GOAL).read_text()
    assert "Human-updated goal: watch both porch and yard after 9 PM." in goal_disk

    # Resume mid-flow
    resumed = client.patch("/api/topics/front-gate", json={"paused": False})
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False


def test_ticks_tools_and_bundle_path_traversal_protection(env):
    client: TestClient = env["client"]
    topic: Topic = env["topic"]
    bid = "20261125T120000Z-abc123"

    r = client.get("/api/topics/porch-watch/ticks")
    assert r.status_code == 200
    data = r.json()
    assert len(data["ticks"]) == 1
    assert data["ticks"][0]["status"] == "notable"
    assert data["token_totals"]["vision"]["input"] == 22000
    assert data["token_totals"]["main"]["input"] == 3500
    assert "Vision rubric" in data["vision_rubric"]
    assert "Orchestrator notes" in data["orchestrator_notes"]
    assert len(data["tools"]) == 1
    assert data["tools"][0]["name"] == "motion.py"
    assert "motion" in data["tools"][0]["code"]

    # Legitimate bundle asset preview
    img = client.get(f"/api/topics/porch-watch/bundles/{bid}/porch/frame.jpg")
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/jpeg")
    assert img.content == FAKE_JPEG

    # Path traversal attempts MUST all fail and never return goal.md
    for bad_url in (
        f"/api/topics/porch-watch/bundles/{bid}/../../goal.md",
        f"/api/topics/porch-watch/bundles/{bid}/%2e%2e/%2e%2e/goal.md",
        f"/api/topics/porch-watch/bundles/{bid}/porch/../../../goal.md",
        "/api/topics/porch-watch/bundles/..%2F../goal.md",
        "/api/topics/porch-watch/bundles/..%2Fstate/ticks.jsonl",
    ):
        resp = client.get(bad_url)
        assert resp.status_code in (400, 403, 404), f"expected block for {bad_url}, got {resp.status_code}"
        assert "Alert when a parcel" not in resp.text

    # Symlink inside bundle pointing outside bundle dir MUST be rejected
    evil_link = topic.bundles_dir / bid / "porch" / "evil.jpg"
    evil_link.symlink_to(topic.dir / GOAL)
    sym_resp = client.get(f"/api/topics/porch-watch/bundles/{bid}/porch/evil.jpg")
    assert sym_resp.status_code == 403
    assert "Alert when a parcel" not in sym_resp.text


# --- 4. Main Agent Chat --------------------------------------------------------


def test_topic_chat_and_attachment_confinement(env):
    client: TestClient = env["client"]
    bid = "20261125T120000Z-abc123"

    r = client.post(
        "/api/topics/porch-watch/chat",
        json={
            "message": "Ignore the tree shadow on the left side.",
            "attachments": [f"{bid}/porch/frame.jpg"],
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "Ignore the tree shadow" in data["reply"]
    assert data["actions"][0]["name"] == "update_vision_rubric"
    assert data["usage"]["input"] == 1200
    assert len(env["harness"].chat_calls) == 1
    assert env["harness"].chat_calls[0][2][0].endswith("porch/frame.jpg")

    # Traversal attachment rejected
    bad = client.post(
        "/api/topics/porch-watch/chat",
        json={"message": "read secret", "attachments": ["../../goal.md"]},
    )
    assert bad.status_code == 403


# --- 5. Alert Inbox: in-process NotifyService + loopback HTTP proxy ------------


def test_alerts_list_ack_feedback_and_media_in_process(env):
    client: TestClient = env["client"]
    notify: NotifyService = env["notify"]
    topic: Topic = env["topic"]
    bid = "20261125T120000Z-abc123"

    rec = notify.send_alert(
        AlertRequest(
            topic_id="porch-watch",
            severity="warning",
            title="Package delivered at porch",
            body="Brown box placed near entryway.",
            bundle_id=bid,
            clip_ref="porch/clip.mp4",
            camera_ids=["porch"],
            dedupe_key="pkg-1",
            requires_ack=True,
        )
    )

    # List alerts
    alerts = client.get("/api/alerts").json()
    assert len(alerts) == 1
    assert alerts[0]["id"] == rec.id
    assert alerts[0]["has_thumbnail"] is True
    assert alerts[0]["has_clip"] is True

    # Media routes
    thumb = client.get(f"/api/alerts/{rec.id}/thumbnail")
    assert thumb.status_code == 200
    assert thumb.content == FAKE_JPEG
    clip = client.get(f"/api/alerts/{rec.id}/clip")
    assert clip.status_code == 200
    assert clip.content == FAKE_MP4

    # Acknowledge
    acked = client.post(f"/api/alerts/{rec.id}/ack", json={"by": "alice"})
    assert acked.status_code == 200
    assert acked.json()["acked_by"] == "alice"
    assert acked.json()["acked_at"] is not None

    # Filter unacked=true now excludes it
    assert client.get("/api/alerts?unacked=true").json() == []

    # Thumbs down feedback with note -> logged to feedback.jsonl + pins bundle to state/saved/
    fb = client.post(
        f"/api/alerts/{rec.id}/feedback",
        json={"rating": "down", "note": "That was a doormat shadow", "by": "alice"},
    )
    assert fb.status_code == 200
    fb_body = fb.json()
    assert fb_body["feedback"]["rating"] == "down"
    assert fb_body["pinned"] is True
    assert (topic.saved_dir / bid / "porch" / "frame.jpg").is_file()
    fb_lines = (topic.state_dir / "feedback.jsonl").read_text().strip().splitlines()
    assert len(fb_lines) == 1
    assert json.loads(fb_lines[0])["note"] == "That was a doormat shadow"

    # Topic Mute API: timed mute, until_next_event (AI), and unmute
    m1 = client.post("/api/topics/porch-watch/mute", json={"mode": "timed", "duration_min": 30})
    assert m1.status_code == 200
    assert m1.json()["mute"]["mode"] == "timed"
    assert m1.json()["mute"]["muted_until"] is not None

    m2 = client.post(
        "/api/topics/porch-watch/mute",
        json={
            "mode": "until_next_event",
            "anchor_alert_id": rec.id,
            "anchor_summary": "Package left: Brown box at door",
        },
    )
    assert m2.status_code == 200
    assert m2.json()["mute"]["mode"] == "until_next_event"
    assert "Brown box" in m2.json()["mute"]["anchor_summary"]

    m3 = client.post("/api/topics/porch-watch/mute", json={"mode": "none"})
    assert m3.status_code == 200
    assert m3.json()["mute"]["mode"] == "none"


def test_alerts_loopback_http_proxy(tmp_path: Path):
    """Verify AlertsBridge proxies list/ack/feedback cleanly to vs_notify HTTP server."""
    topics_root = tmp_path / "topics"
    topics_root.mkdir()
    _seed_topic(topics_root, "porch-watch")

    store = Store(tmp_path / "remote_notify.sqlite")
    svc = NotifyService(store, sinks=[], topic_dir_for=lambda tid: topics_root / tid)
    rec = svc.send_alert(
        AlertRequest(
            topic_id="porch-watch",
            severity="urgent",
            title="Gate left open",
            body="Open for 6 minutes",
            camera_ids=["porch"],
        )
    )
    token = "test-household-token-123"
    notify_app = create_notify_app(svc, token)

    # Wrap notify_app in a sync TestClient (httpx.Client subclass) simulating loopback NotifyClient
    class FakeNotifyClient:
        def __init__(self):
            self._http = TestClient(notify_app, base_url="http://127.0.0.1:8790")

    ui_app = create_app(
        hub=FakeLiveHub(tmp_path / "rec"),
        notifier=FakeNotifyClient(),
        notify_token=token,
        topics_root=topics_root,
    )
    ui_client = TestClient(ui_app)

    listed = ui_client.get("/api/alerts")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == rec.id

    acked = ui_client.post(f"/api/alerts/{rec.id}/ack", json={"by": "bob"})
    assert acked.status_code == 200
    assert acked.json()["acked_by"] == "bob"

    fb = ui_client.post(
        f"/api/alerts/{rec.id}/feedback",
        json={"rating": "up", "note": "Good catch", "by": "bob"},
    )
    assert fb.status_code == 200
    assert fb.json()["feedback"]["rating"] == "up"


# --- 6. Integration with vs_runtime.manager.TopicManager -----------------------


def test_integration_with_runtime_topic_manager(tmp_path: Path):
    from vs_runtime import TopicManager

    topics_root = tmp_path / "topics"
    topics_root.mkdir()
    _seed_topic(topics_root, "porch-watch")
    hub = FakeLiveHub(tmp_path / "rec")
    harness = RecordingChatHarness()
    notify = NotifyService(Store(tmp_path / "notify.sqlite"), sinks=[])

    manager = TopicManager(topics_root, harness=harness, hub=hub, notifier=notify)
    app = create_app(manager=manager, hub=hub, notifier=notify)
    client = TestClient(app)

    assert len(client.get("/api/topics").json()) == 1
    created = client.post(
        "/api/topics",
        json={"id": "pet-sofa", "camera_ids": ["porch"], "preset": "pet-watch"},
    )
    assert created.status_code == 201
    assert created.json()["id"] == "pet-sofa"

    updated = client.patch(
        "/api/topics/pet-sofa",
        json={"paused": True, "speaker_enabled": True, "goal": "Updated via TopicManager"},
    )
    assert updated.status_code == 200
    assert updated.json()["paused"] is True
    assert updated.json()["speaker_enabled"] is True
    assert "Updated via TopicManager" in updated.json()["goal"]

    chat = client.post("/api/topics/pet-sofa/chat", json={"message": "Hello Main Agent"})
    assert chat.status_code == 200
    assert chat.json()["ok"] is True

    # Regression #1: bundle path traversal via TopicManager returns 403/404, NEVER 500
    bid = "20261125T120000Z-abc123"
    ok_asset = client.get(f"/api/topics/porch-watch/bundles/{bid}/porch/frame.jpg")
    assert ok_asset.status_code == 200
    assert ok_asset.content == FAKE_JPEG

    for bad_url in (
        "/api/topics/porch-watch/bundles/..%2F..%2Fgoal.md/x",
        f"/api/topics/porch-watch/bundles/{bid}/../../goal.md",
        f"/api/topics/porch-watch/bundles/{bid}/%2e%2e/%2e%2e/goal.md",
        "/api/topics/porch-watch/bundles/../goal.md",
    ):
        resp = client.get(bad_url)
        assert resp.status_code in (400, 403, 404), f"unexpected {resp.status_code} for {bad_url}"
        assert "Alert when a parcel" not in resp.text

    # Symlink escape inside bundle via TopicManager must be blocked (403/404, never 500 or leak)
    porch_topic = manager.get_topic("porch-watch")
    sym = porch_topic.bundles_dir / bid / "porch" / "escape.jpg"
    sym.symlink_to(porch_topic.dir / GOAL)
    sym_resp = client.get(f"/api/topics/porch-watch/bundles/{bid}/porch/escape.jpg")
    assert sym_resp.status_code in (403, 404)
    assert "Alert when a parcel" not in sym_resp.text

    # Regression #2: session metadata populates from manager.harness when harness=None
    ticks_resp = client.get("/api/topics/porch-watch/ticks")
    assert ticks_resp.status_code == 200
    session = ticks_resp.json()["session"]
    assert session is not None
    assert session["conversation_id"] == "conv-porch-watch"
    assert session["turns"] == 1
