import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from vs_runtime.contracts import CameraConfig, StreamHubClient
from vs_streamhub import FileReplayAdapter, StreamHub, ffmpeg, segments


def test_file_replay_writes_rolling_segments(media, tmp_path):
    seg_dir = tmp_path / "rec" / "demo"
    adapter = FileReplayAdapter(CameraConfig(camera_id="demo", adapter="file", source=str(media["short"])),
                                seg_dir, segment_sec=1)  # fmt: skip
    assert adapter.probe().status == "ok"
    assert adapter.health().status == "offline"  # not started, nothing written
    adapter.start()
    try:
        deadline = time.monotonic() + 20
        while len(segments.list_segments(seg_dir)) < 4 and time.monotonic() < deadline:
            time.sleep(0.25)
        assert adapter.health().status == "ok"
    finally:
        adapter.stop()
    segs = segments.list_segments(seg_dir)
    assert len(segs) >= 4  # the 4 s clip loops past its end into more segments
    assert [s.start for s in segs] == sorted(s.start for s in segs)
    info = ffmpeg.probe(segs[0].path)
    assert info.video_codec == "h264" and info.audio_codec == "aac" and info.duration > 0.5
    assert "not started" in adapter.health().detail


def test_folder_replay_and_missing_source(media, tmp_path):
    folder = tmp_path / "clips"
    folder.mkdir()
    for name in ("a.mp4", "b.mp4"):
        (folder / name).write_bytes(media["short"].read_bytes())
    a = FileReplayAdapter(CameraConfig(camera_id="f", adapter="file", source=str(folder)), tmp_path / "f")
    assert len(a.files()) == 2 and "2 file(s)" in a.probe().detail
    argv, _ = a.writer_command()
    assert "concat" in argv and not any(".mp4" in x and "clips" in x for x in argv)  # listing, not args
    missing = FileReplayAdapter(CameraConfig(camera_id="m", adapter="file", source=str(tmp_path / "nope.mp4")),
                                tmp_path / "m")  # fmt: skip
    assert missing.probe().status == "offline"
    with pytest.raises(FileNotFoundError):
        missing.writer_command()


def test_hub_end_to_end_extract_window(media, tmp_path):
    cams = [CameraConfig(camera_id="demo", adapter="file", source=str(media["tone"]))]
    hub = StreamHub(cams, tmp_path / "rec", segment_sec=1, mediamtx="never")
    assert isinstance(hub, StreamHubClient)
    with hub:
        assert hub.wait_until_ok("demo", 20).status == "ok"
        time.sleep(5)
        end = datetime.now(timezone.utc)
        out = tmp_path / "bundle" / "demo"
        assets = hub.extract_window("demo", end - timedelta(seconds=4), end, out,
                                    {"video", "contact_sheet", "audio", "telemetry"})  # fmt: skip
    assert {a.path for a in assets} == {"clip.mp4", "contact_sheet.jpg", "audio.mp3", "meta.json"}
    meta = json.loads((out / "meta.json").read_text())
    assert meta["status"] in ("ok", "degraded") and meta["covered"]["sec"] >= 2
    assert meta["stream_health"]["camera_id"] == "demo"
    assert (tmp_path / "rec" / "demo").is_dir()


def test_prune_keeps_newest_and_recent(tmp_path):
    cams = [CameraConfig(camera_id="c", adapter="file", source="unused.mp4")]
    hub = StreamHub(cams, tmp_path, retention_sec=60)
    d = hub.segment_dir("c")
    d.mkdir()
    now = datetime.now()
    names = [(now - timedelta(seconds=s)).strftime("%Y-%m-%d_%H-%M-%S") + ".mp4" for s in (600, 300, 30, 10)]
    for n in names:
        (d / n).write_bytes(b"x")
    (d / "notes.txt").write_text("not a segment")
    # a segment ends where the next begins: the 300 s-old one ran until 30 s ago, so it stays
    assert hub.prune() == 1
    assert sorted(p.name for p in d.iterdir()) == sorted([*names[1:], "notes.txt"])
    # a stale camera keeps its newest segment even when it is past retention
    for n in names[1:-1]:
        (d / n).unlink()
    hub.retention_sec = 1
    assert hub.prune() == 0 and (d / names[-1]).exists()


def test_hub_config_and_errors(tmp_path):
    hub = StreamHub.from_config({"recordings_root": str(tmp_path), "retention_sec": 120,
                                 "cameras": {"a": {"adapter": "file", "source": "x.mp4"}}})  # fmt: skip
    assert hub.camera_ids == ["a"] and hub.segment_dir("a") == tmp_path / "a"
    assert hub.health("zzz").status == "offline"
    with pytest.raises(ValueError, match="duplicate"):
        hub.add_camera(CameraConfig(camera_id="a", adapter="file", source="x.mp4"))
    with pytest.raises(ValueError, match="unknown adapter"):
        hub.add_camera(CameraConfig(camera_id="b", adapter="carrier-pigeon"))
    with pytest.raises(ValueError, match="invalid camera_id"):
        hub.add_camera(CameraConfig(camera_id="../etc", adapter="file"))
    with pytest.raises(KeyError):
        hub.extract_window("zzz", datetime.now(timezone.utc), datetime.now(timezone.utc), tmp_path, {"video"})


@pytest.mark.skipif(not __import__("shutil").which("mediamtx"), reason="mediamtx not installed")
def test_file_replay_through_mediamtx(media, tmp_path, port):
    cams = [CameraConfig(camera_id="demo_cam", adapter="file", source=str(media["tone"]), options={"ingest": "mediamtx"})]
    hub = StreamHub(cams, tmp_path / "rec", rtsp_port=port(), api_port=port())
    hub.mediamtx_settings.segment_sec = 2
    url = hub.live_url("demo_cam")
    assert url.startswith("rtsp://127.0.0.1:") and url.endswith("/democam")
    with hub:
        assert hub.wait_until_ok("demo_cam", 25).status == "ok"
        time.sleep(4)
        assert hub.mediamtx.ready("democam")
        live = ffmpeg.probe(url, timeout=10, extra=["-rtsp_transport", "tcp"])  # a second reader, same pull
        assert live and live.video_codec == "h264"
        res = hub.slice_latest("demo_cam", 4, tmp_path / "out")
    segs = segments.list_segments(tmp_path / "rec" / "demo_cam")
    assert segs and segs[0].path.name.startswith("democam_")
    assert res.status in ("ok", "degraded") and (tmp_path / "out" / "clip.mp4").exists()
    assert not list((tmp_path / "rec" / ".mediamtx").glob("*.key"))  # no stray certificates
