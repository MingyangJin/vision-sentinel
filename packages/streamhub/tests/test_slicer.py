import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from vs_streamhub import ffmpeg
from vs_streamhub.slicer import WindowSlicer


def _probe_audio(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-of", "json", "-show_entries",
                          "stream=codec_name,sample_rate,channels", str(path)], capture_output=True, check=True)  # fmt: skip
    return json.loads(out.stdout)["streams"][0]


def _jpeg_size(path):
    info = ffmpeg.probe(path)
    return info.width, info.height


def test_slices_latest_window_into_all_assets(media, segmenter, tmp_path):
    seg_dir = tmp_path / "rec" / "cam1"
    end = datetime.now(timezone.utc)
    segmenter(media["tone"], seg_dir, end=end)
    out = tmp_path / "bundle" / "cam1"
    res = WindowSlicer().slice("cam1", seg_dir, end - timedelta(seconds=6), end, out)

    assert res.status == "ok"
    assert {a.path for a in res.assets} == {"clip.mp4", "contact_sheet.jpg", "frame.jpg", "audio.mp3", "meta.json"}
    assert all(a.camera_id == "cam1" and (out / a.path).stat().st_size > 0 for a in res.assets)
    assert {a.kind for a in res.assets} == {"video", "contact_sheet", "frame", "audio", "telemetry"}

    clip = ffmpeg.probe(out / "clip.mp4")
    assert clip.video_codec == "h264" and 5.0 <= clip.duration <= 7.0
    sheet_w, sheet_h = _jpeg_size(out / "contact_sheet.jpg")
    assert (sheet_w, sheet_h) == (3 * 320, 2 * 240)  # 3x2 grid of 320x240 tiles
    a = _probe_audio(out / "audio.mp3")
    assert (a["codec_name"], a["sample_rate"], a["channels"]) == ("mp3", "16000", 1)

    meta = json.loads((out / "meta.json").read_text())
    assert meta["status"] == "ok" and meta["camera_id"] == "cam1"
    assert meta["segment_count"] >= 3 and len(meta["segments"]) == meta["segment_count"]
    assert meta["requested"]["sec"] == 6 and 5.0 <= meta["covered"]["sec"] <= 6.01
    assert meta["audio"]["present"] and meta["audio"]["written"]
    assert -30 < meta["audio"]["rms_dbfs"] < meta["audio"]["peak_dbfs"] < 0  # sine at 1/8 amplitude: ~-21/-18
    assert meta["video"]["width"] == 320


def test_no_audio_track_is_skipped(media, segmenter, tmp_path):
    seg_dir = tmp_path / "cam"
    segmenter(media["mute"], seg_dir)
    res = WindowSlicer().slice("cam", seg_dir, datetime.now(timezone.utc) - timedelta(seconds=6),
                               datetime.now(timezone.utc), tmp_path / "out")  # fmt: skip
    assert res.status == "ok"
    assert "audio.mp3" not in {a.path for a in res.assets} and not (tmp_path / "out" / "audio.mp3").exists()
    assert res.meta["audio"] == {"present": False, "rms_dbfs": None, "peak_dbfs": None, "written": False,
                                 "skipped_reason": "no audio track"}  # fmt: skip


def test_digital_silence_is_skipped(media, segmenter, tmp_path):
    seg_dir = tmp_path / "cam"
    segmenter(media["silent"], seg_dir)
    now = datetime.now(timezone.utc)
    res = WindowSlicer().slice("cam", seg_dir, now - timedelta(seconds=6), now, tmp_path / "out")
    assert res.meta["audio"]["present"] and not res.meta["audio"]["written"]
    assert res.meta["audio"]["skipped_reason"] == "digital silence"
    assert "audio.mp3" not in {a.path for a in res.assets}


def test_offline_camera_yields_meta_only(tmp_path):
    now = datetime.now(timezone.utc)
    res = WindowSlicer().slice("gone", tmp_path / "nothing", now - timedelta(seconds=30), now, tmp_path / "out",
                               {"video", "telemetry"})  # fmt: skip
    assert res.status == "offline"
    assert [a.path for a in res.assets] == ["meta.json"]
    meta = json.loads((tmp_path / "out" / "meta.json").read_text())
    assert meta["status"] == "offline" and meta["segment_count"] == 0


def test_window_outside_buffer_is_offline(media, segmenter, tmp_path):
    seg_dir = tmp_path / "cam"
    end = datetime.now(timezone.utc) - timedelta(minutes=10)
    segmenter(media["short"], seg_dir, end=end)
    now = datetime.now(timezone.utc)
    res = WindowSlicer().slice("cam", seg_dir, now - timedelta(seconds=10), now, tmp_path / "out")
    assert res.status == "offline" and res.assets[-1].path == "meta.json"


def test_gap_marks_degraded(media, segmenter, tmp_path):
    seg_dir = tmp_path / "cam"
    end = datetime.now(timezone.utc)
    segmenter(media["tone"], seg_dir, end=end, gap_after=2, gap_sec=8)
    res = WindowSlicer().slice("cam", seg_dir, end - timedelta(seconds=25), end, tmp_path / "out")
    assert res.status == "degraded"
    assert res.meta["gaps_sec"] and "gaps" in res.meta["detail"]
    assert (tmp_path / "out" / "clip.mp4").exists()


def test_short_coverage_marks_degraded(media, segmenter, tmp_path):
    seg_dir = tmp_path / "cam"
    end = datetime.now(timezone.utc)
    segmenter(media["short"], seg_dir, end=end)  # 4 s of media
    res = WindowSlicer().slice("cam", seg_dir, end - timedelta(seconds=60), end, tmp_path / "out")
    assert res.status == "degraded" and "covered" in res.meta["detail"]


def test_kinds_subset_writes_only_requested(media, segmenter, tmp_path):
    seg_dir = tmp_path / "cam"
    segmenter(media["tone"], seg_dir)
    now = datetime.now(timezone.utc)
    res = WindowSlicer().slice("cam", seg_dir, now - timedelta(seconds=6), now, tmp_path / "out", {"contact_sheet"})
    assert [a.path for a in res.assets] == ["contact_sheet.jpg"]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["contact_sheet.jpg", "meta.json"]


def test_never_touches_the_segments(media, segmenter, tmp_path):
    seg_dir = tmp_path / "cam"
    segs = segmenter(media["tone"], seg_dir)
    before = {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in segs}
    now = datetime.now(timezone.utc)
    WindowSlicer().slice("cam", seg_dir, now - timedelta(seconds=6), now, tmp_path / "out")
    after = {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in seg_dir.iterdir()}
    assert before == after


def test_truncated_open_segment_is_tolerated(media, segmenter, tmp_path):
    """The newest segment may be mid-write (a header, or half a fragment): use what reads."""
    seg_dir = tmp_path / "cam"
    end = datetime.now(timezone.utc)
    segs = segmenter(media["tone"], seg_dir, end=end)
    data = segs[-1].read_bytes()
    segs[-1].write_bytes(data[: len(data) // 3])
    res = WindowSlicer().slice("cam", seg_dir, end - timedelta(seconds=8), end, tmp_path / "out")
    assert res.status in ("ok", "degraded") and (tmp_path / "out" / "clip.mp4").exists()


@pytest.mark.parametrize("fps", [None, 1.0])
def test_fps_option(media, segmenter, tmp_path, fps):
    seg_dir = tmp_path / "cam"
    segmenter(media["tone"], seg_dir)
    now = datetime.now(timezone.utc)
    WindowSlicer(fps=fps).slice("cam", seg_dir, now - timedelta(seconds=6), now, tmp_path / "out", {"video"})
    got = ffmpeg.probe(tmp_path / "out" / "clip.mp4").fps
    assert got == pytest.approx(fps or 15, rel=0.1)
