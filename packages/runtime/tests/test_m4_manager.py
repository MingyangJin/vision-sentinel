"""Tests for M4 M:N engine, speaker opt-in gating, pause/resume, TopicManager CRUD, and templates."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from vs_runtime.bundle import BundleBuilder
from vs_runtime.contracts import MediaCapabilities, OrchestratorAction
from vs_runtime.fakes import FakeHarness
from vs_runtime.loop import TopicLoop
from vs_runtime.manager import TOPIC_PRESETS, TopicManager
from vs_runtime.topic import ReadOnlyError, Topic, init_topic
from vs_runtime.watchdog import Watchdog

CAPS = MediaCapabilities(frames=True, audio=True)
REPO_ROOT = Path(__file__).resolve().parents[3]


# --- M:N multi-camera synchronized bundles & 1-cam -> N-topics concurrent execution ---------


def test_multi_cam_synchronized_wall_clock_and_statuses(tmp_path, hub, t0):
    t = init_topic(tmp_path / "topics" / "perimeter", ["north", "east", "west"],
                   "Watch all three perimeter cameras.", window_sec=15)
    hub.status["east"] = "degraded"
    hub.status["west"] = "offline"

    b = BundleBuilder(t, hub, CAPS).build(t0)
    assert list(b.camera_status.keys()) == ["north", "east", "west"]
    assert b.camera_status == {"north": "ok", "east": "degraded", "west": "offline"}
    assert b.degraded
    assert any("east: degraded" in n for n in b.notes)
    assert any("west: offline" in n for n in b.notes)
    # Every camera got the exact same synchronized wall-clock [start, end) window.
    by_cam = {c: (s, e) for c, s, e in hub.calls}
    assert set(by_cam) == {"north", "east", "west"}
    assert len(set(by_cam.values())) == 1
    assert by_cam["north"] == (b.window_start, b.window_end)
    assert {a.camera_id for a in b.assets} == {"north", "east"}


def test_one_camera_fans_out_to_n_concurrent_topics(tmp_path, hub, notifier):
    t1 = init_topic(tmp_path / "topics" / "packages", ["porch"], "Watch for parcels.",
                    cadence_sec=1, window_sec=5)
    t2 = init_topic(tmp_path / "topics" / "door", ["porch"], "Watch if door is open.",
                    cadence_sec=1, window_sec=5)
    h1 = FakeHarness(reports=["urgent"])
    h2 = FakeHarness(reports=["notable"])
    l1 = TopicLoop(t1, h1, hub, notifier)
    l2 = TopicLoop(t2, h2, hub, notifier)

    stop = threading.Event()
    th1 = threading.Thread(target=l1.run, args=(stop,))
    th2 = threading.Thread(target=l2.run, args=(stop,))
    th1.start()
    th2.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (h1.events and h2.events):
        time.sleep(0.05)
    stop.set()
    th1.join(5)
    th2.join(5)

    assert h1.events and h2.events
    assert all(e.topic_id == "packages" for e in h1.events)
    assert all(e.topic_id == "door" for e in h2.events)
    # Only t1 alerted (urgent); both shared the single FakeHub & FakeNotifier cleanly.
    assert [a.topic_id for a in notifier.sent] == ["packages"]
    assert all(c == "porch" for c, _, _ in hub.calls)


# --- Double opt-in camera speaker gating ----------------------------------------------------


def test_speaker_alert_requires_both_topic_and_camera_opt_in(topic, hub, notifier, at):
    def send_speaker(loop: TopicLoop):
        return loop.dispatch([OrchestratorAction(
            name="send_notification",
            args={"severity": "warning", "title": "Step back", "speaker": True},
        )], None)[0]

    # 1. Default: neither topic nor camera opted in -> suppressed.
    assert topic.config.speaker_enabled is False
    lp = TopicLoop(topic, FakeHarness(), hub, notifier, speaker_cameras=set())
    r1 = send_speaker(lp)
    assert r1["ok"] and r1["result"]["speaker"] is False
    assert "topic" in r1["result"]["speaker_suppressed"]
    assert notifier.sent[-1].speaker is False

    # 2. Camera opted in, topic NOT opted in -> suppressed.
    lp_cam_only = TopicLoop(topic, FakeHarness(), hub, notifier, speaker_cameras={"cam1"})
    r2 = send_speaker(lp_cam_only)
    assert r2["result"]["speaker"] is False
    assert "topic" in r2["result"]["speaker_suppressed"]
    assert notifier.sent[-1].speaker is False

    # 3. Topic opted in, camera NOT opted in -> suppressed.
    topic.update_config(speaker_enabled=True)
    lp_topic_only = TopicLoop(topic, FakeHarness(), hub, notifier, speaker_cameras=set())
    r3 = send_speaker(lp_topic_only)
    assert r3["result"]["speaker"] is False
    assert "camera" in r3["result"]["speaker_suppressed"]
    assert notifier.sent[-1].speaker is False

    # 4. BOTH topic and camera opted in -> allowed!
    lp_both = TopicLoop(topic, FakeHarness(), hub, notifier, speaker_cameras={"cam1"})
    r4 = send_speaker(lp_both)
    assert r4["result"]["speaker"] is True
    assert "speaker_suppressed" not in r4["result"]
    assert notifier.sent[-1].speaker is True

    # 5. Even alert_defaults with speaker=True is gated when topic disables speaker.
    topic.update_config(speaker_enabled=False, alert_defaults={"speaker": True})
    r5 = lp_both.dispatch([OrchestratorAction(
        name="send_notification", args={"severity": "info", "title": "Hi"},
    )], None)[0]
    assert r5["result"]["speaker"] is False
    assert notifier.sent[-1].speaker is False


# --- Pause / resume in loop & watchdog ------------------------------------------------------


def test_paused_topic_skips_ticks_and_watchdog_stale(topic, hub, notifier, t0, at):
    topic.update_config(cadence_sec=1, paused=True)
    harness = FakeHarness()
    lp = TopicLoop(topic, harness, hub, notifier)
    stop = threading.Event()
    th = threading.Thread(target=lp.run, args=(stop,))
    th.start()
    time.sleep(0.4)
    assert harness.events == []  # paused: zero ticks

    # Watchdog never alerts stale while paused, and suppresses camera_drop events.
    events = []
    now = [t0]
    wd = Watchdog([topic], hub, notifier, clock=lambda: now[0],
                  on_event=lambda tid, ev: events.append((tid, ev)))
    now[0] = at(3600)
    hub.status["cam1"] = "offline"
    out = wd.check_once()
    assert out["topics"][topic.id]["paused"] is True
    assert out["topics"][topic.id]["stale"] is False
    assert out["alerts"] == [] and notifier.sent == [] and events == []

    # Unpausing live: watchdog grants fresh grace window and loop immediately ticks!
    hub.status["cam1"] = "ok"
    topic.update_config(paused=False)
    out2 = wd.check_once()
    assert out2["topics"][topic.id]["paused"] is False and out2["topics"][topic.id]["stale"] is False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not harness.events:
        time.sleep(0.05)
    stop.set()
    th.join(5)
    assert len(harness.events) >= 1


# --- TopicManager CRUD, presets, human goal edit, path confinement, chat --------------------


def test_topic_manager_crud_presets_and_human_goal_edit(tmp_path, hub, notifier, t0):
    harness = FakeHarness()
    created = []
    mgr = TopicManager(
        tmp_path / "topics", harness=harness, hub=hub, notifier=notifier,
        on_topic_created=lambda t: created.append(t.id),
    )

    presets = {p["id"]: p for p in mgr.list_presets()}
    assert {"package-watch", "door-left-open", "pet-watch", "multi-cam-watch", "custom"} <= set(presets)

    # Create from preset
    t = mgr.create_topic("front-porch", ["porch"], preset="package-watch")
    assert created == ["front-porch"]
    assert t.config.cadence_sec == TOPIC_PRESETS["package-watch"].cadence_sec
    assert "parcel" in t.goal().lower()
    assert t.config.speaker_enabled is False and t.config.paused is False

    # Reject bad ids, duplicates, unknown preset
    with pytest.raises(ValueError):
        mgr.create_topic("../escape", ["porch"], goal="bad")
    with pytest.raises(FileExistsError):
        mgr.create_topic("front-porch", ["porch"], goal="dup")
    with pytest.raises(ValueError):
        mgr.create_topic("other", ["porch"], preset="nonexistent")

    # Mid-flow human edit including goal.md, pause, speaker_enabled, cameras
    updated = mgr.update_topic(
        "front-porch",
        camera_ids=["porch", "driveway"],
        cadence_sec=45,
        paused=True,
        speaker_enabled=True,
        goal="Only alert for large boxes on the porch.",
        orchestrator_notes="Ignore shadows from the oak tree.",
    )
    assert updated.config.camera_ids == ["porch", "driveway"]
    assert updated.config.cadence_sec == 45
    assert updated.config.paused is True and updated.config.speaker_enabled is True
    assert updated.goal().strip() == "Only alert for large boxes on the porch."
    assert "oak tree" in updated.notes()

    # Pause/resume shortcuts
    assert mgr.resume_topic("front-porch").config.paused is False
    assert mgr.pause_topic("front-porch").config.paused is True

    # Agent actions STILL cannot write goal.md even after TopicManager edited it for the human!
    lp = TopicLoop(updated, harness, hub, notifier)
    res = lp.dispatch([OrchestratorAction(name="edit_topic_tool",
                                          args={"path": "../goal.md", "code": "hijack"})], None)
    assert res[0]["ok"] is False and "goal.md" in res[0]["error"]
    assert updated.goal().strip() == "Only alert for large boxes on the porch."
    with pytest.raises(ReadOnlyError):
        updated._agent_path("goal.md")


def test_topic_manager_detail_bundles_confinement_and_chat(tmp_path, hub, notifier, t0):
    harness = FakeHarness()
    mgr = TopicManager(tmp_path / "topics", harness=harness, hub=hub, notifier=notifier)
    t = mgr.create_topic("entry", ["cam1"], goal="Watch entry.")
    t.write_tool("count.py", "print('{\"ok\": true}')")

    # Build a bundle & simulate a pinned copy under state/saved/
    b = BundleBuilder(t, hub, CAPS).build(t0)
    shutil.copytree(t.bundles_dir / b.bundle_id, t.saved_dir / b.bundle_id)

    # Topic detail includes tools with code & bundles with saved=True
    detail = mgr.get_topic_detail("entry")
    assert detail["id"] == "entry"
    assert detail["tools"][0]["name"] == "count.py" and "print" in detail["tools"][0]["code"]
    assert detail["bundles"][0]["bundle_id"] == b.bundle_id
    assert detail["bundles"][0]["saved"] is True

    # Path confinement on resolve_bundle_asset
    ok_asset = mgr.resolve_bundle_asset("entry", b.bundle_id, "cam1/contact_sheet.jpg")
    assert ok_asset.is_file() and ok_asset.name == "contact_sheet.jpg"

    for bad_path in ("../goal.md", "../../topic.toml", "/etc/passwd", "cam1/../../goal.md"):
        with pytest.raises((ValueError, FileNotFoundError)):
            mgr.resolve_bundle_asset("entry", b.bundle_id, bad_path)
    with pytest.raises(ValueError):
        mgr.resolve_bundle_asset("entry", "../other", "bundle.json")

    # Symlink escape inside a saved bundle is rejected
    escape_dir = t.saved_dir / "sym-bundle"
    escape_dir.mkdir(parents=True)
    (escape_dir / "leak.txt").symlink_to(t.dir / "goal.md")
    with pytest.raises(FileNotFoundError):
        mgr.resolve_bundle_asset("entry", "sym-bundle", "leak.txt")

    # Chat invokes harness + dispatches actions + returns usage
    harness.scripted_actions = [[
        OrchestratorAction(name="update_vision_rubric", args={"content": "# Updated rubric\n"}),
    ]]
    reply = mgr.chat("entry", "Tighten the rubric please.", attachments=[str(ok_asset)])
    assert reply["ok"] is True
    assert "Tighten the rubric" in reply["reply"]
    assert reply["actions"][0]["name"] == "update_vision_rubric" and reply["actions"][0]["ok"]
    assert t.rubric() == "# Updated rubric\n"


def test_example_topic_templates_are_valid():
    examples_dir = REPO_ROOT / "examples" / "topics"
    expected = {"demo-replay", "package-watch", "door-left-open", "multi-cam-demo"}
    found = {p.name for p in examples_dir.iterdir() if p.is_dir()}
    assert expected <= found
    for name in expected:
        t = Topic(examples_dir / name)
        assert t.id == name
        assert len(t.config.camera_ids) >= 1
        assert t.config.speaker_enabled is False
        assert t.goal().strip() and t.rubric().strip() and t.notes().strip()
    assert len(Topic(examples_dir / "multi-cam-demo").config.camera_ids) == 2
