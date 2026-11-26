"""Tests for HttpIngestAdapter (alias macos_camera / camera_client)."""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

from vs_runtime.contracts import CameraConfig, utcnow
from vs_streamhub import HttpIngestAdapter, StreamHub, build_adapter


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _post(url: str, body: bytes, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_http_ingest_receives_mp4_and_slices_window(tmp_path: Path, media: dict[str, Path]):
    sample_clip = media["tone"]
    port = _free_port()
    cfg = CameraConfig(
        camera_id="mac_cam",
        adapter="macos_camera",
        options={"bind": "127.0.0.1", "port": port},
    )
    ad = build_adapter(cfg, tmp_path / "rec" / "mac_cam")
    assert isinstance(ad, HttpIngestAdapter)

    hub = StreamHub([cfg], tmp_path / "rec")
    hub.start()
    try:
        # Before first upload, health is degraded (waiting for camera client)
        assert hub.health("mac_cam").status == "degraded"

        # Health endpoint lists camera
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/ingest/health", timeout=5) as r:
            assert r.status == 200
            assert b"mac_cam" in r.read()

        # Invalid payload rejected with 400
        code, _ = _post(f"http://127.0.0.1:{port}/v1/ingest/mac_cam/segment", b"not an mp4 at all")
        assert code == 400

        # Real MP4 segment upload -> 200, health becomes ok, WindowSlicer slices clip + contact sheet + audio
        mp4_bytes = sample_clip.read_bytes()
        code, resp = _post(
            f"http://127.0.0.1:{port}/v1/ingest/mac_cam/segment",
            mp4_bytes,
            headers={"Content-Type": "video/mp4"},
        )
        assert code == 200
        assert b'"ok": true' in resp
        assert hub.health("mac_cam").status == "ok"

        now = utcnow()
        assets = hub.extract_window(
            "mac_cam",
            now - timedelta(seconds=15),
            now + timedelta(seconds=2),
            tmp_path / "out",
            {"video", "contact_sheet", "frame", "audio", "telemetry"},
        )
        kinds = {a.kind for a in assets}
        assert {"video", "contact_sheet", "frame", "audio", "telemetry"} <= kinds
        assert (tmp_path / "out" / "clip.mp4").stat().st_size > 0
    finally:
        hub.stop()
    assert hub.health("mac_cam").status == "offline"


def test_http_ingest_bearer_auth_and_shared_port(tmp_path: Path, media: dict[str, Path], monkeypatch):
    sample_clip = media["tone"]
    port = _free_port()
    monkeypatch.setenv("VS_TEST_CAM_TOKEN", "s3cret-push-token")
    cams = [
        CameraConfig(
            camera_id="mac_one",
            adapter="http_ingest",
            credential_ref="env:VS_TEST_CAM_TOKEN",
            options={"bind": "127.0.0.1", "port": port},
        ),
        CameraConfig(
            camera_id="mac_two",
            adapter="camera_client",
            options={"bind": "127.0.0.1", "port": port},
        ),
    ]
    hub = StreamHub(cams, tmp_path / "rec")
    hub.start()
    try:
        mp4_bytes = sample_clip.read_bytes()
        # mac_one requires Bearer token
        code, _ = _post(f"http://127.0.0.1:{port}/v1/ingest/mac_one/segment", mp4_bytes)
        assert code == 401
        code, _ = _post(
            f"http://127.0.0.1:{port}/v1/ingest/mac_one/segment",
            mp4_bytes,
            headers={"Authorization": "Bearer wrong"},
        )
        assert code == 401
        code, _ = _post(
            f"http://127.0.0.1:{port}/v1/ingest/mac_one/segment",
            mp4_bytes,
            headers={"Authorization": "Bearer s3cret-push-token"},
        )
        assert code == 200

        # mac_two shares the same port with open loopback auth
        code, _ = _post(f"http://127.0.0.1:{port}/v1/ingest/mac_two/segment", mp4_bytes)
        assert code == 200

        # Unknown camera_id -> 404
        code, _ = _post(f"http://127.0.0.1:{port}/v1/ingest/nope/segment", mp4_bytes)
        assert code == 404

        assert hub.health("mac_one").status == "ok"
        assert hub.health("mac_two").status == "ok"
    finally:
        hub.stop()


def test_http_ingest_non_loopback_bind_requires_token(tmp_path: Path, monkeypatch):
    import pytest

    open_lan = CameraConfig(camera_id="mac_cam", adapter="http_ingest", options={"bind": "0.0.0.0", "port": 0})
    with pytest.raises(ValueError, match="credential_ref"):
        build_adapter(open_lan, tmp_path / "rec" / "mac_cam")

    monkeypatch.setenv("VS_TEST_INGEST_TOKEN", "ingest-test-token")
    guarded = open_lan.model_copy(update={"credential_ref": "env:VS_TEST_INGEST_TOKEN"})
    ad = build_adapter(guarded, tmp_path / "rec" / "mac_cam")
    assert ad.describe()["bearer_required"] is True and "ingest-test-token" not in str(ad.describe())

    local = CameraConfig(camera_id="mac_cam", adapter="http_ingest", options={"bind": "127.0.0.1", "port": 0})
    assert build_adapter(local, tmp_path / "rec" / "mac_cam").describe()["bearer_required"] is False
