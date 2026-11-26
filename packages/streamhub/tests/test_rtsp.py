import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vs_runtime.contracts import CameraConfig
from vs_streamhub import RtspAdapter, StreamHub, ffmpeg, scrub_credentials
from vs_streamhub.credentials import resolve_secret
from vs_streamhub.adapters import Supervisor
from vs_streamhub.ffmpeg import MediaInfo

SECRET = "Hx7-very-secret"


@pytest.fixture
def cam_env(monkeypatch):
    monkeypatch.setenv("VS_TEST_PORCH", f"viewer:{SECRET}")
    return CameraConfig(camera_id="porch", adapter="rtsp", source="rtsp://192.0.2.10:554/stream1",
                        credential_ref="env:VS_TEST_PORCH")  # fmt: skip


def _no_secret(obj) -> None:
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    assert SECRET not in text and "viewer:" not in text


def test_source_validation(tmp_path):
    with pytest.raises(ValueError, match="credential_ref"):
        RtspAdapter(CameraConfig(camera_id="x", adapter="rtsp", source="rtsp://u:p@192.0.2.1/s"), tmp_path)
    with pytest.raises(ValueError, match="rtsp://"):
        RtspAdapter(CameraConfig(camera_id="x", adapter="rtsp", source="http://192.0.2.1/s"), tmp_path)


def test_ffmpeg_plan_injects_credentials_only_at_launch(cam_env, tmp_path):
    hub = StreamHub([cam_env.model_copy(update={"options": {"ingest": "ffmpeg"}})], tmp_path)
    plan = hub.plan()
    _no_secret(plan)
    cam = plan["cameras"][0]
    assert cam["ingest"] == "ffmpeg" and cam["source"] == "rtsp://***@192.0.2.10:554/stream1"
    assert cam["live_url"] is None and plan["mediamtx"] is None
    argv, _ = hub.adapter("porch").writer_command()
    assert f"rtsp://viewer:{SECRET}@192.0.2.10:554/stream1" in argv  # the launched process gets it ...
    assert ["-rtsp_transport", "tcp"] == argv[argv.index("-rtsp_transport"):][:2]
    logged = scrub_credentials(" ".join(argv))  # ... what is logged never
    _no_secret(logged)
    assert "rtsp://***@192.0.2.10" in logged


def test_mediamtx_plan_keeps_secret_in_env_only(cam_env, tmp_path):
    fake = tmp_path / "mediamtx"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    hub = StreamHub([cam_env], tmp_path / "rec", mediamtx_binary=str(fake), rtsp_port=18601, api_port=18602)
    plan = hub.plan()
    _no_secret(plan)
    assert plan["cameras"][0]["ingest"] == "mediamtx"
    assert hub.live_url("porch") == "rtsp://127.0.0.1:18601/porch"
    server = hub.mediamtx
    _no_secret(server.config())
    cfg_file = server.write_config()
    _no_secret(cfg_file.read_text())
    cfg = json.loads(cfg_file.read_text())
    assert cfg["rtspAddress"] == "127.0.0.1:18601" and cfg["apiAddress"] == "127.0.0.1:18602"
    assert "source" not in cfg["paths"]["porch"] and "%path" in cfg["paths"]["porch"]["recordPath"]
    assert cfg["paths"]["porch"]["recordPath"].startswith(str((tmp_path / "rec" / "porch").resolve()))
    argv, env = server.command()
    _no_secret(argv)
    assert env["MTX_PATHS_PORCH_SOURCE"] == f"rtsp://viewer:{SECRET}@192.0.2.10:554/stream1"
    assert not hub.adapter("porch").needs_writer()  # MediaMTX holds the one camera session


@pytest.mark.parametrize("busy", ["rtsp_port", "api_port"])
def test_mediamtx_refuses_busy_port(cam_env, tmp_path, port, busy):
    import socket

    fake = tmp_path / "mediamtx"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    ports = {"rtsp_port": port(), "api_port": port()}
    with socket.socket() as other:  # another MediaMTX already listening
        other.bind(("127.0.0.1", ports[busy]))
        other.listen()
        hub = StreamHub([cam_env], tmp_path / "rec", mediamtx_binary=str(fake), **ports)
        with pytest.raises(RuntimeError, match=rf"127\.0\.0\.1:{ports[busy]} is already in use.*{busy}"):
            hub.start()
    assert not hub.mediamtx.running and hub.adapter("porch").started_at is None


def test_falls_back_to_ffmpeg_without_mediamtx(cam_env, tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None if name == "mediamtx" else "/usr/bin/" + name)
    hub = StreamHub([cam_env], tmp_path)
    assert hub.plan()["cameras"][0]["ingest"] == "ffmpeg" and hub.adapter("porch").needs_writer()
    explicit = StreamHub([cam_env.model_copy(update={"options": {"ingest": "mediamtx"}})], tmp_path)
    with pytest.raises(RuntimeError, match="mediamtx"):
        explicit.plan()


def test_mediamtx_path_names_are_env_safe(tmp_path):
    from vs_streamhub.mediamtx import path_name

    assert path_name("Front-Door_2", set()) == "frontdoor2"
    assert path_name("front_door", {"frontdoor"}) == "frontdoor2"
    assert path_name("___", set()) == "cam"


def test_probe_reports_without_leaking(cam_env, tmp_path, monkeypatch):
    seen = {}

    def fake_probe(target, **kw):
        seen["url"] = target
        return None

    monkeypatch.setattr(ffmpeg, "probe", fake_probe)
    a = RtspAdapter(cam_env, tmp_path)
    h = a.probe()
    assert h.status == "offline" and "192.0.2.10" in h.detail
    _no_secret(h.model_dump_json())
    assert SECRET in seen["url"]  # ffprobe itself had to authenticate
    monkeypatch.setattr(ffmpeg, "probe", lambda target, **kw: MediaInfo(10, "h264", 1920, 1080, 15.0, "aac"))
    h = a.probe()
    assert h.status == "ok" and "h264 1920x1080" in h.detail


def test_probe_with_unresolvable_credential(tmp_path, monkeypatch):
    monkeypatch.delenv("VS_TEST_NOPE", raising=False)
    a = RtspAdapter(CameraConfig(camera_id="c", adapter="rtsp", source="rtsp://192.0.2.9/s",
                                 credential_ref="env:VS_TEST_NOPE"), tmp_path)  # fmt: skip
    h = a.probe()
    assert h.status == "offline" and "did not resolve" in h.detail


def test_probe_timeout_never_leaks_argv(monkeypatch):
    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["ffprobe", f"rtsp://viewer:{SECRET}@192.0.2.10/s"], timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(ffmpeg.FFmpegError) as e:
        ffmpeg.run(["ffprobe", "x"], timeout=1)
    assert SECRET not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__


def test_supervisor_reconnects_and_scrubs(cam_env, tmp_path):
    resolve_secret("env:VS_TEST_PORCH")
    script = (f"import sys; sys.stderr.write('Connection to rtsp://viewer:{SECRET}@192.0.2.10/s refused; "
              f"pw {SECRET}\\n'); sys.exit(1)")  # fmt: skip
    sup = Supervisor("rtsp:porch", lambda: ([sys.executable, "-c", script], dict(os.environ)), max_backoff=0.2)
    sup.start()
    try:
        deadline = time.monotonic() + 10
        while sup.restarts < 2 and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        sup.stop()
    assert sup.restarts >= 2  # it keeps reconnecting
    assert "refused" in sup.last_error and "rtsp://***@192.0.2.10" in sup.last_error
    _no_secret(sup.last_error)


def test_rtsp_health_reports_reconnect(cam_env, tmp_path, monkeypatch):
    a = RtspAdapter(cam_env.model_copy(update={"options": {"ingest": "ffmpeg"}}), tmp_path / "porch")
    script = f"import sys; sys.stderr.write('401 Unauthorized rtsp://viewer:{SECRET}@192.0.2.10/s\\n'); sys.exit(1)"
    monkeypatch.setattr(a, "writer_command", lambda: ([sys.executable, "-c", script], None))
    a.start()
    try:
        deadline = time.monotonic() + 10
        while not (a._supervisor and a._supervisor.restarts) and time.monotonic() < deadline:
            time.sleep(0.1)
        h = a.health()
    finally:
        a.stop()
    assert h.status == "offline" and "restarts=" in h.detail and "401 Unauthorized" in h.detail
    # The One Rule: health leaves the hub (Web UI, bundle meta.json) without the camera address.
    assert "192.0.2.10" not in h.detail and "rtsp://" not in h.detail
    _no_secret(h.model_dump_json())


def test_check_cli_is_redacted(cam_env, tmp_path):
    cfg = tmp_path / "hub.toml"
    cfg.write_text(f"""
[streamhub]
recordings_root = "{tmp_path / 'rec'}"
mediamtx = "never"

[cameras.porch]
adapter = "rtsp"
source = "rtsp://192.0.2.10:554/stream1"
credential_ref = "env:VS_TEST_PORCH"
""")
    out = subprocess.run([sys.executable, "-m", "vs_streamhub", "check", str(cfg), "--no-probe"],
                         capture_output=True, text=True, env=dict(os.environ), timeout=60)  # fmt: skip
    assert out.returncode == 0, out.stderr
    _no_secret(out.stdout + out.stderr)
    plan = json.loads(out.stdout)
    assert plan["cameras"][0]["source"] == "rtsp://***@192.0.2.10:554/stream1"
    assert Path(plan["recordings_root"]) == tmp_path / "rec"
