import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from vs_runtime.contracts import ModelConfig, ObservationAsset, ObservationBundle, TopicContext

from vs_harness_antigravity import AntigravityHarness

FAKE = Path(__file__).with_name("fake_agy.py")


def pytest_configure(config):
    config.addinivalue_line("markers", "live_agy: runs the real agy CLI (opt in with VS_LIVE_AGY=1)")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("VS_LIVE_AGY") == "1":
        return
    skip = pytest.mark.skip(reason="live agy test; set VS_LIVE_AGY=1 to run")
    for item in items:
        if "live_agy" in item.keywords:
            item.add_marker(skip)


class FakeAgy:
    def __init__(self, root: Path):
        self.dir = root / "fake_agy"
        self.dir.mkdir()
        self.binary = self.dir / "agy"
        self.binary.write_text(f"#!{sys.executable}\n" + FAKE.read_text())
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IEXEC)
        self.scenario([{}])

    def scenario(self, specs: list[dict]) -> None:
        (self.dir / "scenario.json").write_text(json.dumps(specs))
        (self.dir / "counter").unlink(missing_ok=True)
        (self.dir / "calls.jsonl").unlink(missing_ok=True)
        (self.dir / "pids").unlink(missing_ok=True)

    def pids(self) -> list[int]:
        p = self.dir / "pids"
        return [int(x) for x in p.read_text().split()] if p.exists() else []

    def calls(self) -> list[dict]:
        p = self.dir / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def harness(self, **kw) -> AntigravityHarness:
        return AntigravityHarness(binary=str(self.binary), extra_env={"FAKE_AGY_DIR": str(self.dir)}, **kw)


@pytest.fixture
def fake(tmp_path):
    return FakeAgy(tmp_path)


T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def bundle(tmp_path) -> ObservationBundle:
    d = tmp_path / "topic" / "state" / "bundles" / "b1"
    (d / "cam").mkdir(parents=True)
    for name in ("contact_sheet.jpg", "frame.jpg", "audio.mp3", "clip.mp4", "meta.json"):
        (d / "cam" / name).write_bytes(b"x")
    kinds = {"contact_sheet.jpg": "contact_sheet", "frame.jpg": "frame", "audio.mp3": "audio",
             "clip.mp4": "video", "meta.json": "telemetry"}
    return ObservationBundle(
        bundle_id="b1", topic_id="t1", camera_ids=["cam"], camera_status={"cam": "ok"},
        window_start=T0, window_end=T0 + timedelta(seconds=10), path=str(d),
        assets=[ObservationAsset(camera_id="cam", kind=k, path=f"cam/{n}") for n, k in kinds.items()],
        tool_outputs={"motion": {"score": 0.1}},
    )


@pytest.fixture
def topic(tmp_path) -> TopicContext:
    d = tmp_path / "topic"
    (d / "state").mkdir(parents=True, exist_ok=True)
    return TopicContext(
        topic_id="t1", topic_dir=str(d), goal="Tell me when the gate is left open.",
        vision_rubric="Report gate_open when the gate is open.", orchestrator_notes="- shadows are not gates",
        main_model=ModelConfig(model="gemini-4-neon-medium"),
        vision_model=ModelConfig(model="gemini-4-hydrogen-low"),
    )
