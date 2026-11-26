import json
from datetime import datetime, timedelta, timezone

from vs_runtime.contracts import CameraConfig
from vs_streamhub import StreamHub


def test_multi_camera_synchronized_window_slicing(media, segmenter, tmp_path):
    """M cameras -> 1 Topic: synchronized wall-clock slice with one camera offline."""
    cams = [
        CameraConfig(camera_id="front", adapter="file_replay", source=str(media["tone"])),
        CameraConfig(camera_id="back", adapter="file_replay", source=str(media["mute"])),
        CameraConfig(camera_id="garage", adapter="file_replay", source=str(media["short"])),  # stays offline (no segments)
    ]
    hub = StreamHub(cams, tmp_path / "rec", mediamtx="never")
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=6)
    segmenter(media["tone"], hub.segment_dir("front"), end=end)
    segmenter(media["mute"], hub.segment_dir("back"), end=end)

    bundle = tmp_path / "bundle_001"
    kinds = {"video", "contact_sheet", "audio", "telemetry"}

    front_assets = hub.extract_window("front", start, end, bundle / "front", kinds)
    back_assets = hub.extract_window("back", start, end, bundle / "back", kinds)
    garage_assets = hub.extract_window("garage", start, end, bundle / "garage", kinds)

    assert {a.path for a in front_assets} == {"clip.mp4", "contact_sheet.jpg", "audio.mp3", "meta.json"}
    assert all(a.camera_id == "front" for a in front_assets)
    # back has no audio track -> gracefully omitted
    assert {a.path for a in back_assets} == {"clip.mp4", "contact_sheet.jpg", "meta.json"}
    assert all(a.camera_id == "back" for a in back_assets)
    # garage is offline -> only meta.json with status="offline", no exception
    assert [a.path for a in garage_assets] == ["meta.json"]
    assert json.loads((bundle / "garage" / "meta.json").read_text())["status"] == "offline"
    assert json.loads((bundle / "front" / "meta.json").read_text())["status"] == "ok"
    assert json.loads((bundle / "back" / "meta.json").read_text())["status"] == "ok"


def test_single_camera_serves_multiple_topics_without_interference(media, segmenter, tmp_path):
    """1 camera -> N Topics: both slice different windows from the same rolling buffer."""
    hub = StreamHub([CameraConfig(camera_id="shared", adapter="file_replay", source=str(media["tone"]))], tmp_path / "rec")
    end = datetime.now(timezone.utc)
    segmenter(media["tone"], hub.segment_dir("shared"), end=end)

    t1 = hub.extract_window("shared", end - timedelta(seconds=8), end, tmp_path / "topic_a" / "shared", {"video", "telemetry"})
    t2 = hub.extract_window("shared", end - timedelta(seconds=4), end, tmp_path / "topic_b" / "shared", {"contact_sheet", "telemetry"})

    assert {a.path for a in t1} == {"clip.mp4", "meta.json"}
    assert {a.path for a in t2} == {"contact_sheet.jpg", "meta.json"}
    assert json.loads((tmp_path / "topic_a" / "shared" / "meta.json").read_text())["covered"]["sec"] > 6.5
    assert json.loads((tmp_path / "topic_b" / "shared" / "meta.json").read_text())["covered"]["sec"] <= 4.1
