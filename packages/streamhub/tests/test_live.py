import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from vs_runtime.contracts import CameraConfig
from vs_streamhub import MJPEG_MEDIA_TYPE, LiveStreamProvider, StreamHub, ffmpeg
from vs_streamhub.live import _pop_jpegs, format_mjpeg_frame


def test_list_cameras_never_exposes_urls_or_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("VS_LIVE_SEC", "admin:SuperSecret99")
    cams = [
        CameraConfig(camera_id="porch", adapter="rtsp", source="rtsp://192.0.2.10:554/stream1", credential_ref="env:VS_LIVE_SEC"),
        CameraConfig(camera_id="demo", adapter="file_replay", source="/some/local/file.mp4", speaker_enabled=True, volume_cap=0.25),
    ]
    hub = StreamHub(cams, tmp_path / "rec", mediamtx="never")
    summary = hub.list_cameras()
    raw = json.dumps(summary)
    assert "SuperSecret99" not in raw
    assert "192.0.2.10" not in raw
    assert "/some/local/file.mp4" not in raw
    assert "VS_LIVE_SEC" not in raw
    assert {c["camera_id"] for c in summary} == {"porch", "demo"}
    demo_row = next(c for c in summary if c["camera_id"] == "demo")
    assert demo_row["speaker_enabled"] is True and demo_row["volume_cap"] == 0.25


def test_latest_snapshot_from_rolling_segments(media, segmenter, tmp_path):
    hub = StreamHub([CameraConfig(camera_id="cam1", adapter="file_replay", source=str(media["tone"]))], tmp_path / "rec")
    # Offline / empty buffer returns None
    assert hub.latest_snapshot("cam1") is None
    with pytest.raises(KeyError):
        hub.latest_snapshot("unknown")

    end = datetime.now(timezone.utc)
    segs = segmenter(media["tone"], hub.segment_dir("cam1"), end=end)
    jpeg = hub.latest_snapshot("cam1", width=320)
    assert jpeg is not None and jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")

    # Truncated / tiny newest segment gracefully falls back to the previous segment
    segs[-1].write_bytes(b"tiny")
    jpeg2 = hub.latest_snapshot("cam1", width=320)
    assert jpeg2 is not None and jpeg2.startswith(b"\xff\xd8")


def test_iter_mjpeg_yields_multipart_frames(media, segmenter, tmp_path):
    hub = StreamHub([CameraConfig(camera_id="cam1", adapter="file_replay", source=str(media["tone"]))], tmp_path / "rec")
    segmenter(media["tone"], hub.segment_dir("cam1"))
    provider = LiveStreamProvider(hub)

    async def collect():
        chunks = []
        async for chunk in provider.iter_mjpeg("cam1", fps=15.0, width=320, max_frames=3):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())
    assert len(chunks) == 3
    assert MJPEG_MEDIA_TYPE == "multipart/x-mixed-replace; boundary=frame"
    for chunk in chunks:
        assert chunk.startswith(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ")
        body = chunk.split(b"\r\n\r\n", 1)[1][:-2]
        assert body.startswith(b"\xff\xd8") and body.endswith(b"\xff\xd9")


def test_mjpeg_parser_and_formatter():
    f1 = b"\xff\xd8frame1\xff\xd9"
    f2 = b"\xff\xd8frame2\xff\xd9"
    buf = bytearray(b"junk" + f1 + f2 + b"\xff\xd8partial")
    got = _pop_jpegs(buf)
    assert got == [f1, f2]
    assert bytes(buf) == b"\xff\xd8partial"
    formatted = format_mjpeg_frame(f1)
    assert f"Content-Length: {len(f1)}".encode() in formatted and f1 in formatted


def test_list_segments_segment_file_and_extract_clip(media, segmenter, tmp_path):
    hub = StreamHub([CameraConfig(camera_id="cam1", adapter="file_replay", source=str(media["tone"]))], tmp_path / "rec")
    end = datetime.now(timezone.utc)
    segmenter(media["tone"], hub.segment_dir("cam1"), end=end)

    infos = hub.list_segments("cam1")
    assert len(infos) >= 4
    assert [s.start for s in infos] == sorted(s.start for s in infos)
    assert infos[-1].is_active is True
    d0 = infos[0].to_dict()
    assert set(d0) == {"name", "start", "end", "duration_sec", "size_bytes", "is_active"}
    assert d0["size_bytes"] > 0 and d0["duration_sec"] > 0
    assert len(hub.list_segments("cam1", limit=2)) == 2

    # Safe segment lookup + traversal rejection
    p = hub.segment_file("cam1", infos[0].name)
    assert p.is_file() and p.parent == hub.segment_dir("cam1").resolve()
    for bad in ("../etc/passwd", "sub/2026-01-01_00-00-00.mp4", "notes.txt", ""):
        with pytest.raises(ValueError):
            hub.segment_file("cam1", bad)
    with pytest.raises(FileNotFoundError):
        hub.segment_file("cam1", "2020-01-01_00-00-00.mp4")

    # Extract browser-playable clip for [end-6s, end)
    clip_out = tmp_path / "ui-cache" / "clip.mp4"
    written = hub.extract_clip("cam1", end - timedelta(seconds=6), end, clip_out, width=320)
    assert written == clip_out and clip_out.is_file() and clip_out.stat().st_size > 0
    info = ffmpeg.probe(clip_out)
    assert info and info.video_codec == "h264" and 4.5 <= info.duration <= 6.5

    # Out-of-range window returns None
    miss = hub.extract_clip("cam1", end - timedelta(hours=2), end - timedelta(hours=1), tmp_path / "miss.mp4")
    assert miss is None


def _collect_fmp4(provider, segment_dir, *, newest_complete, max_chunks, timeout=20):
    async def go():
        out = []
        async for chunk in provider._iter_segments_fmp4(segment_dir, max_chunks=max_chunks,
                                                        newest_complete=newest_complete):  # fmt: skip
            out.append(chunk)
        return out

    return asyncio.run(asyncio.wait_for(go(), timeout))


def test_live_fmp4_sends_only_completed_segments(media, segmenter, tmp_path):
    """A writer is still appending to the newest segment: streaming it early sends a partial
    fragment (or bare headers) and then skips the rest of that segment, which froze the live
    view on file-replay and webcam cameras."""
    hub = StreamHub([CameraConfig(camera_id="cam1", adapter="file_replay", source=str(media["tone"]))], tmp_path / "rec")
    seg_dir = hub.segment_dir("cam1")
    segs = segmenter(media["tone"], seg_dir)
    assert len(segs) >= 3
    in_progress = segs[-1]
    whole = in_progress.read_bytes()
    in_progress.write_bytes(whole[: whole.index(b"moof") - 4])  # headers only, first fragment not flushed

    async def run():
        chunks = []
        gen = hub.live._iter_segments_fmp4(seg_dir, max_chunks=2)
        chunks.append(await asyncio.wait_for(anext(gen), 15))  # newest COMPLETED segment, not the open one
        waiting = asyncio.ensure_future(anext(gen))
        await asyncio.sleep(1.0)
        assert not waiting.done()  # the open segment is not sent while it is the newest
        in_progress.write_bytes(whole)  # writer finishes it ...
        nxt = in_progress.with_name("2999-01-01_00-00-00-000000.mp4")
        nxt.write_bytes(whole[: whole.index(b"moof") - 4])  # ... and opens the next one
        chunks.append(await asyncio.wait_for(waiting, 15))
        await gen.aclose()
        return chunks

    first, second = asyncio.run(run())
    for chunk in (first, second):
        assert chunk[4:8] == b"ftyp" and b"moof" in chunk  # real media, never bare headers
    assert len(second) > 1000


def test_live_fmp4_pushed_segments_stream_without_waiting(media, segmenter, tmp_path):
    """http_ingest segments are complete when they appear, so the newest one is sent at once."""
    from vs_streamhub import HttpIngestAdapter
    from vs_streamhub.adapters.base import BaseAdapter

    assert HttpIngestAdapter.segments_atomic is True and BaseAdapter.segments_atomic is False
    hub = StreamHub([CameraConfig(camera_id="cam1", adapter="file_replay", source=str(media["tone"]))], tmp_path / "rec")
    seg_dir = hub.segment_dir("cam1")
    segmenter(media["tone"], seg_dir)
    [chunk] = _collect_fmp4(hub.live, seg_dir, newest_complete=True, max_chunks=1)
    assert chunk[4:8] == b"ftyp" and b"moof" in chunk
