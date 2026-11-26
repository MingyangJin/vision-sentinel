import json
import socket

import pytest

from vs_runtime.contracts import CameraConfig
from vs_streamhub import RtmpIngestAdapter, StreamHub, build_adapter, scrub_credentials

SECRET_KEY = "Ph0ne-Str3am-K3y"


def test_rtmp_ingest_registration_and_validation(tmp_path):
    a = build_adapter(CameraConfig(camera_id="phone", adapter="rtmp_ingest"), tmp_path)
    b = build_adapter(CameraConfig(camera_id="phone2", adapter="rtmp"), tmp_path)
    assert isinstance(a, RtmpIngestAdapter) and isinstance(b, RtmpIngestAdapter)
    with pytest.raises(ValueError, match="credential_ref"):
        RtmpIngestAdapter(CameraConfig(camera_id="p", adapter="rtmp_ingest", source="rtmp://u:p@127.0.0.1:1935/live/p"), tmp_path)
    with pytest.raises(ValueError, match="rtmp:// or rtsp://"):
        RtmpIngestAdapter(CameraConfig(camera_id="p", adapter="rtmp_ingest", source="http://127.0.0.1/live"), tmp_path)


def test_rtmp_ingest_mediamtx_enables_rtmp_and_checks_port(tmp_path, port):
    fake_mtx = tmp_path / "mediamtx"
    fake_mtx.write_text("#!/bin/sh\n")
    fake_mtx.chmod(0o755)
    rtsp_p, api_p, rtmp_p = port(), port(), port()
    cfg = CameraConfig(
        camera_id="phone_1",
        adapter="rtmp_ingest",
        source="live/phone_1",
        options={"port": rtmp_p, "publish_ips": ["192.0.2.0/24"]},
    )
    hub = StreamHub([cfg], tmp_path / "rec", mediamtx_binary=str(fake_mtx), rtsp_port=rtsp_p, api_port=api_p)
    plan = hub.plan()
    assert plan["mediamtx"]["rtmp"] == f"127.0.0.1:{rtmp_p}"
    assert plan["cameras"][0]["ingest"] == "mediamtx"
    assert plan["cameras"][0]["ingest_url"] == f"rtmp://127.0.0.1:{rtmp_p}/phone1"
    assert not hub.adapter("phone_1").needs_writer()

    mtx_cfg = hub.mediamtx.config()
    assert mtx_cfg["rtmp"] is True and mtx_cfg["rtmpAddress"] == f"127.0.0.1:{rtmp_p}"
    assert mtx_cfg["paths"]["phone1"]["source"] == "publisher"
    assert any("192.0.2.0/24" in u["ips"] for u in mtx_cfg["authInternalUsers"])
    assert hub.probe("phone_1").status == "ok"

    # Busy RTMP port triggers clear preflight error
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", rtmp_p))
        busy.listen()
        with pytest.raises(RuntimeError, match=rf"127\.0\.0\.1:{rtmp_p} is already in use.*rtmp_port"):
            hub.start()


def test_rtmp_ingest_ffmpeg_fallback_rtmp_and_rtsp(monkeypatch, tmp_path):
    monkeypatch.setenv("VS_TEST_PHONE_KEY", SECRET_KEY)
    rtmp_cfg = CameraConfig(
        camera_id="phone_rtmp",
        adapter="rtmp_ingest",
        source="live/phone",
        credential_ref="env:VS_TEST_PHONE_KEY",
        options={"ingest": "ffmpeg", "port": 19350},
    )
    hub = StreamHub([rtmp_cfg], tmp_path / "rec", mediamtx="never")
    plan = hub.plan()
    assert SECRET_KEY not in json.dumps(plan)
    assert plan["cameras"][0]["ingest"] == "ffmpeg"
    assert plan["cameras"][0]["ingest_url"] == "rtmp://127.0.0.1:19350/live/phone"
    assert hub.adapter("phone_rtmp").needs_writer()

    argv, _ = hub.adapter("phone_rtmp").writer_command()
    assert ["-listen", "1", "-f", "flv", "-i", f"rtmp://127.0.0.1:19350/live/phone?key={SECRET_KEY}"] == argv[argv.index("-listen"):argv.index("-i") + 2]
    assert SECRET_KEY not in scrub_credentials(" ".join(argv))
    assert hub.probe("phone_rtmp").status == "ok"

    # RTSP push listen fallback with transcode
    rtsp_cfg = CameraConfig(
        camera_id="phone_rtsp",
        adapter="rtmp_ingest",
        source="rtsp://127.0.0.1:18560/phone",
        options={"ingest": "ffmpeg", "transcode": True},
    )
    a = RtmpIngestAdapter(rtsp_cfg, tmp_path / "rec" / "phone_rtsp")
    assert a.protocol == "rtsp" and not a.wants_rtmp
    argv2, _ = a.writer_command()
    assert "-rtsp_flags" in argv2 and "listen" in argv2 and "libx264" in argv2
    assert "rtsp://127.0.0.1:18560/phone" in argv2
