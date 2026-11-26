import json
import os
import signal
import subprocess
import threading
import time

import pytest

from vs_runtime import cli
from vs_runtime.fakes import FakeHarness, FakeHub, FakeNotifier


class StartableHub(FakeHub):
    started = stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


@pytest.fixture
def config(tmp_path):
    assert cli.main(["init-topic", str(tmp_path / "topics" / "porch"), "--camera", "porch",
                     "--goal", "Tell me when a parcel arrives.", "--cadence", "30"]) == 0
    cfg = tmp_path / "vs.toml"
    cfg.write_text(
        'topics = ["topics/porch"]\nharness = "fake"\n'
        '[[cameras]]\ncamera_id = "porch"\nadapter = "rtsp"\n'
        'source = "rtsp://admin:hunter2@192.0.2.10:554/stream1"\n'
        '[notify]\ndb = ":memory:"\n[[notify.sinks]]\ntype = "webhook"\n'
        'url = "https://user:pw@example.invalid/hook"\nheaders = { Authorization = "Bearer abc" }\n'
    )
    return cfg


def test_init_topic_cli(config, tmp_path):
    t = (tmp_path / "topics" / "porch")
    assert "cadence_sec = 30" in (t / "topic.toml").read_text()
    assert (t / "goal.md").read_text().startswith("Tell me")


def test_check_dry_run_is_redacted(config, capsys):
    assert cli.main(["check", "--config", str(config), "--check"]) == 0
    out = capsys.readouterr().out
    plan = json.loads(out)
    assert "hunter2" not in out and "Bearer abc" not in out and "user:pw" not in out
    assert plan["topics"][0]["id"] == "porch" and plan["topics"][0]["cadence_sec"] == 30
    assert plan["harness"]["name"] == "fake"


def test_check_runs_one_cycle(config, capsys, monkeypatch):
    hub, notifier = StartableHub(), FakeNotifier()
    monkeypatch.setattr(cli.App, "hub", lambda self: hub)
    monkeypatch.setattr(cli.App, "notifier", lambda self, hub=None: notifier)
    assert cli.main(["check", "--config", str(config), "--settle", "0"]) == 0
    [result] = json.loads(capsys.readouterr().out)
    assert hub.started and hub.stopped
    assert result["topic"] == "porch" and result["status"] == "clear" and result["delivered"]
    assert result["assets"] == ["porch/clip.mp4", "porch/contact_sheet.jpg", "porch/audio.mp3", "porch/meta.json"]


def test_redact():
    assert cli.redact("rtsp://u:p@h/s") == "rtsp://***@h/s"
    assert cli.redact({"headers": {"X": "1"}, "credential_ref": "cam-pass", "url": "http://a:b@c"}) == {
        "headers": "***", "credential_ref": "cam-pass", "url": "http://***@c"}


class SlowHarness(FakeHarness):
    """A harness whose worker blocks on a child process until close() kills it."""

    def __init__(self):
        super().__init__()
        self.procs: list[subprocess.Popen] = []
        self.spawned = threading.Event()

    def run_vision_worker(self, bundle, rubric, model):
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.procs.append(proc)
        self.spawned.set()
        proc.wait()
        return super().run_vision_worker(bundle, rubric, model)

    def close(self):
        for p in self.procs:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)


def test_run_stops_promptly_and_leaves_no_harness_child(config, monkeypatch, tmp_path):
    t = tmp_path / "topics" / "porch" / "topic.toml"
    t.write_text(t.read_text().replace("window_sec = 20", "window_sec = 1"))
    harness = SlowHarness()
    monkeypatch.setattr(cli.App, "hub", lambda self: StartableHub())
    monkeypatch.setattr(cli.App, "notifier", lambda self, hub=None: FakeNotifier())
    monkeypatch.setattr(cli.App, "harness", lambda self: harness)
    app, stop = cli.App(config), threading.Event()
    th = threading.Thread(target=cli.run_app, args=(app, stop))
    th.start()
    assert harness.spawned.wait(10)
    t0 = time.monotonic()
    stop.set()
    th.join(10)
    assert not th.is_alive() and time.monotonic() - t0 < 5
    assert all(p.poll() is not None for p in harness.procs)
    # The interrupted tick stays unreviewed, for late replay on the next start.
    from vs_runtime.counters import TickLog, unreviewed_ticks

    assert len(unreviewed_ticks(TickLog(app.topics[0].ticks_path).records())) == 1


def test_standalone_watchdog_alerts_on_old_review(config, monkeypatch, tmp_path, capsys):
    from datetime import datetime, timedelta, timezone

    from vs_runtime.counters import TickLog

    old = datetime.now(timezone.utc) - timedelta(hours=2)
    log = TickLog(tmp_path / "topics" / "porch" / "state" / "ticks.jsonl")
    log.append({"type": "tick", "at": old.isoformat(), "bundle_id": "b0", "status": "clear"})
    log.append({"type": "review", "ok": True, "bundle_ids": ["b0"]})
    notifier = FakeNotifier()
    monkeypatch.setattr(cli.App, "hub", lambda self: StartableHub())
    monkeypatch.setattr(cli.App, "notifier", lambda self, hub=None: notifier)
    assert cli.main(["watchdog", "--config", str(config), "--once"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["alerts"] == ["porch"] and out["topics"]["porch"]["stale"]
    assert len(notifier.sent) == 1 and notifier.sent[0].source == "watchdog"


def test_ui_subcommand_invokes_serve_ui_and_cleans_up(config, monkeypatch):
    import sys
    import types

    hub, notifier = StartableHub(), FakeNotifier()
    called = {}

    fake_ui_pkg = types.ModuleType("vs_ui")
    fake_ui_srv = types.ModuleType("vs_ui.server")

    def fake_serve_ui(*, manager, hub, notifier, host, port, allowed_hosts=None):
        called.update(manager=manager, hub=hub, notifier=notifier, host=host, port=port)

    fake_ui_srv.serve_ui = fake_serve_ui
    fake_ui_pkg.server = fake_ui_srv
    monkeypatch.setitem(sys.modules, "vs_ui", fake_ui_pkg)
    monkeypatch.setitem(sys.modules, "vs_ui.server", fake_ui_srv)
    monkeypatch.setattr(cli.App, "hub", lambda self: hub)
    monkeypatch.setattr(cli.App, "notifier", lambda self, hub=None: notifier)

    rc = cli.main(["ui", "--config", str(config), "--host", "127.0.0.1", "--port", "9091", "--no-loop"])
    assert rc == 0
    assert hub.started and hub.stopped
    assert called["host"] == "127.0.0.1" and called["port"] == 9091
    assert called["hub"] is hub and called["notifier"] is notifier
    assert [t["id"] for t in called["manager"].list_topics()] == ["porch"]


def test_multi_topic_check_and_run_share_one_hub(tmp_path, capsys, monkeypatch):
    assert cli.main(["init-topic", str(tmp_path / "topics" / "t1"), "--camera", "cam_a",
                     "--camera", "cam_b", "--goal", "Watch both.", "--window", "1"]) == 0
    assert cli.main(["init-topic", str(tmp_path / "topics" / "t2"), "--camera", "cam_a",
                     "--goal", "Watch cam_a only.", "--window", "1"]) == 0
    cfg = tmp_path / "mn.toml"
    cfg.write_text(
        'topics = ["topics/t1", "topics/t2"]\nharness = "fake"\n'
        '[[cameras]]\ncamera_id = "cam_a"\nadapter = "file"\nsource = "a.mp4"\n'
        '[[cameras]]\ncamera_id = "cam_b"\nadapter = "file"\nsource = "b.mp4"\n'
    )
    hub, notifier = StartableHub(), FakeNotifier()
    hub_creates = 0

    def make_hub(self):
        nonlocal hub_creates
        hub_creates += 1
        return hub

    monkeypatch.setattr(cli.App, "hub", make_hub)
    monkeypatch.setattr(cli.App, "notifier", lambda self, hub=None: notifier)
    capsys.readouterr()
    assert cli.main(["check", "--config", str(cfg), "--settle", "0"]) == 0
    results = json.loads(capsys.readouterr().out)
    assert hub_creates == 1
    assert [r["topic"] for r in results] == ["t1", "t2"]
    assert results[0]["camera_status"] == {"cam_a": "ok", "cam_b": "ok"}
    assert results[1]["camera_status"] == {"cam_a": "ok"}
    # cam_a was sliced by both t1 and t2 via the single shared hub instance.
    cams_called = [c for c, _, _ in hub.calls]
    assert cams_called.count("cam_a") == 2 and cams_called.count("cam_b") == 1



def test_cameras_accept_tables_keyed_by_camera_id(tmp_path, capsys):
    assert cli.main(["init-topic", str(tmp_path / "topics" / "desk"), "--camera", "desk", "--goal", "Watch."]) == 0
    capsys.readouterr()  # drop init-topic's own output
    cfg = tmp_path / "vs.toml"
    cfg.write_text(
        'topics = ["topics/desk"]\nharness = "fake"\n'
        '[cameras.desk]\nadapter = "http_ingest"\n[cameras.desk.options]\nbind = "127.0.0.1"\nport = 0\n'
        '[notify]\ndb = ":memory:"\n'
    )
    assert cli.main(["check", "--config", str(cfg), "--check"]) == 0
    [cam] = json.loads(capsys.readouterr().out)["cameras"]["cameras"]
    assert cam["camera_id"] == "desk" and cam["bearer_required"] is False


def test_port_in_use_is_a_clear_error_not_a_traceback(config, monkeypatch, caplog):
    import errno

    def busy(self):
        raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr(cli.App, "hub", busy)
    assert cli.main(["check", "--config", str(config), "--settle", "0"]) == 2
    assert "already in use" in caplog.text
