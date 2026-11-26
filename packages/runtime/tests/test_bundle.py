import json
import os
import time

from vs_runtime.bundle import BundleBuilder, prune_bundles, run_tools
from vs_runtime.contracts import MediaCapabilities
from vs_runtime.topic import TopicConfig, init_topic

CAPS = MediaCapabilities(frames=True, audio=True)


def test_default_tool_timeout_is_30s():
    assert TopicConfig(id="x", camera_ids=["c"]).tool_timeout_sec == 30.0


def test_bundle_is_immutable_and_per_camera(topic, hub, t0):
    b = BundleBuilder(topic, hub, CAPS).build(t0)
    bdir = topic.bundles_dir / b.bundle_id
    assert b.path == str(bdir) and b.window_end == t0 and b.window_start.timestamp() == t0.timestamp() - 10
    assert {a.path for a in b.assets} == {
        "cam1/clip.mp4", "cam1/contact_sheet.jpg", "cam1/audio.mp3", "cam1/meta.json",
    }
    assert b.camera_status == {"cam1": "ok"} and not b.degraded
    saved = json.loads((bdir / "bundle.json").read_text())
    assert saved["bundle_id"] == b.bundle_id
    files = [p for p in bdir.rglob("*") if p.is_file()]
    assert files and all(not os.access(p, os.W_OK) for p in files)


def test_capabilities_filter_assets(topic, hub, t0):
    b = BundleBuilder(topic, hub, MediaCapabilities(frames=True, video=False, audio=False)).build(t0)
    paths = {a.path for a in b.assets}
    assert "cam1/audio.mp3" not in paths
    # clip.mp4 is still preserved for human alert playback even when the vision harness has video=False
    assert "cam1/clip.mp4" in paths


def test_multi_camera_with_one_offline(tmp_path, hub, t0):
    t = init_topic(tmp_path / "gate", ["cam1", "cam2"], "watch the gate")
    hub.status["cam2"] = "offline"
    b = BundleBuilder(t, hub, CAPS).build(t0)
    assert b.camera_status == {"cam1": "ok", "cam2": "offline"}
    assert b.degraded and any("cam2" in n for n in b.notes)
    assert all(a.camera_id == "cam1" for a in b.assets)


def test_tool_output_and_scrubbed_env(topic, hub, t0, monkeypatch):
    monkeypatch.setenv("CAMERA_PASSWORD", "hunter2")
    monkeypatch.setenv("RTSP_URL", "rtsp://admin:hunter2@192.0.2.1/stream")
    topic.write_tool("env.py", (
        "import json, os, pathlib\n"
        "print(json.dumps({'env': dict(os.environ), 'cwd': os.getcwd(),"
        " 'files': sorted(str(p) for p in pathlib.Path('.').rglob('*'))}))\n"
    ))
    b = BundleBuilder(topic, hub, CAPS).build(t0)
    out = b.tool_outputs["env.py"]
    assert "hunter2" not in json.dumps(out)
    assert not {"CAMERA_PASSWORD", "RTSP_URL"} & set(out["env"])
    assert out["env"]["VS_BUNDLE_ID"] == b.bundle_id and out["env"]["VS_CAMERA_IDS"] == "cam1"
    assert os.path.realpath(out["cwd"]) == os.path.realpath(b.path)
    assert "cam1/contact_sheet.jpg" in out["files"]


def test_hung_tool_times_out_and_degrades_tick(topic, hub, t0):
    topic.update_config(tool_timeout_sec=1)
    # The shell spawns a grandchild sleeper that holds stdout: the whole group must die.
    topic.write_tool("hang.sh", "sleep 60 &\nsleep 60\n")
    topic.write_tool("ok.py", "print('{\"motion\": 0.1}')")
    start = time.monotonic()
    b = BundleBuilder(topic, hub, CAPS).build(t0)
    assert time.monotonic() - start < 10
    assert b.degraded and any("hang.sh" in n and "timed out" in n for n in b.notes)
    assert b.tool_outputs == {"ok.py": {"motion": 0.1}}
    results = json.loads((topic.bundles_dir / b.bundle_id / "tool_outputs.json").read_text())
    assert results["hang.sh"]["ok"] is False


def test_failing_tool_degrades(topic, hub, t0):
    topic.write_tool("bad.py", "import sys; print('boom', file=sys.stderr); sys.exit(3)")
    b = BundleBuilder(topic, hub, CAPS).build(t0)
    assert b.degraded and any("exit 3" in n and "boom" in n for n in b.notes)


def test_tools_rerun_on_frozen_bundle(topic, hub, t0):
    b = BundleBuilder(topic, hub, CAPS).build(t0)
    topic.write_tool("count.py", "import os; print(len(os.listdir('cam1')))")
    res = run_tools(topic, topic.bundles_dir / b.bundle_id, b)
    assert res["count.py"]["ok"] and res["count.py"]["output"] == 4


def test_prune_keeps_saved(topic, hub, t0):
    b = BundleBuilder(topic, hub, CAPS).build(t0)
    (topic.saved_dir / "old").mkdir(parents=True)
    old = time.time() - 48 * 3600
    os.utime(topic.bundles_dir / b.bundle_id, (old, old))
    os.utime(topic.saved_dir / "old", (old, old))
    assert prune_bundles(topic) == 1
    assert not (topic.bundles_dir / b.bundle_id).exists()
    assert (topic.saved_dir / "old").exists()
