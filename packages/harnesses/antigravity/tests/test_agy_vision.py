import time
from pathlib import Path

from vs_runtime.contracts import ModelConfig

from vs_harness_antigravity import AntigravityHarness
from vs_harness_antigravity._agy import AgyRun, parse_stream
from vs_harness_antigravity.cli_probe import probe
from vs_harness_antigravity.vision import build_vision_prompt

GOOD = {"status": "clear", "summary": "gate closed", "observations": ["gate closed"],
        "conditions": [], "confidence": 0.9, "suggest_review": False, "details": {"gate": "closed"}}
HYDROGEN = ModelConfig(model="gemini-4-hydrogen-low")
ALL_READ = ["cam/contact_sheet.jpg", "cam/frame.jpg", "cam/audio.mp3", "cam/meta.json"]


def test_vision_worker_isolation_flags_cwd_and_env(fake, bundle, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "nope")
    monkeypatch.setenv("CAMERA_PASSWORD", "nope")
    fake.scenario([{"output": GOOD, "reads": ALL_READ}])
    report = fake.harness().run_vision_worker(bundle, "rubric", HYDROGEN)

    (call,) = fake.calls()
    argv = call["argv"]
    assert argv[argv.index("--mode") + 1] == "plan"
    assert argv[argv.index("--model") + 1] == "gemini-4-hydrogen-low"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--json-schema" in argv and "--print" in argv
    for forbidden in ("--dangerously-skip-permissions", "--add-dir", "--sandbox", "--resume"):
        assert forbidden not in argv
    assert Path(call["cwd"]) == Path(bundle.path).resolve()
    assert "GEMINI_API_KEY" not in call["env"] and "CAMERA_PASSWORD" not in call["env"]

    assert report.status == "clear" and not report.degraded and not report.suggest_review
    assert report.assets_read == ALL_READ
    assert report.raw["details"] == {"gate": "closed"}
    assert report.usage.input == 100 and report.usage.cached == 50
    assert report.usage.output == 10 and report.usage.thinking == 5 and report.usage.duration_s > 0


def test_vision_offers_only_ingestible_assets(fake, bundle):
    prompt = build_vision_prompt(bundle, "my rubric", fake.harness().media_capabilities())
    assert "cam/frame.jpg" in prompt and "cam/audio.mp3" in prompt and "cam/meta.json" in prompt
    assert "clip.mp4" not in prompt
    assert "my rubric" in prompt and '"score": 0.1' in prompt
    fake.scenario([{"output": GOOD, "reads": ALL_READ}])
    report = fake.harness().run_vision_worker(bundle, "r", HYDROGEN)
    assert report.raw["_harness"]["assets_unsupported"] == ["cam/clip.mp4"]


def test_vision_unread_asset_marks_degraded(fake, bundle):
    fake.scenario([{"output": GOOD, "reads": ["cam/frame.jpg"]}])
    report = fake.harness().run_vision_worker(bundle, "r", HYDROGEN)
    assert report.status == "clear" and report.degraded
    assert report.assets_read == ["cam/frame.jpg"]
    assert "cam/audio.mp3" in report.raw["_harness"]["assets_unread"]


def test_vision_outside_read_and_blocked_tools_are_flagged(fake, bundle):
    fake.scenario([{"output": GOOD, "reads": ALL_READ + ["/etc/hosts"], "tools": ["run_command"],
                    "denied": ["read_file"]}])
    report = fake.harness().run_vision_worker(bundle, "r", HYDROGEN)
    h = report.raw["_harness"]
    assert h["outside_reads"] == ["/etc/hosts"] and h["blocked_tools"] == ["run_command"]
    assert h["denied"] == ["read_file"]
    assert report.degraded and report.suggest_review
    assert "/etc/hosts" not in report.assets_read


def test_agy_plan_artifact_is_not_an_escape(fake, bundle):
    fake.scenario([{"output": GOOD, "reads": ALL_READ, "writes": ["$brain/plan.md"]}])
    report = fake.harness().run_vision_worker(bundle, "r", HYDROGEN)
    assert not report.degraded and report.raw["_harness"]["agy_artifacts"][0].endswith("plan.md")
    fake.scenario([{"output": GOOD, "reads": ALL_READ, "writes": [str(Path(bundle.path) / "made.txt")]}])
    report = fake.harness().run_vision_worker(bundle, "r", HYDROGEN)
    assert report.degraded and report.raw["_harness"]["blocked_tools"] == ["write_file"]


def test_vision_timeout_returns_error_report_quickly(fake, bundle):
    fake.scenario([{"output": GOOD, "sleep": 10}])
    t0 = time.monotonic()
    report = fake.harness(vision_timeout_s=0.5).run_vision_worker(bundle, "r", HYDROGEN)
    assert time.monotonic() - t0 < 5
    assert report.status == "error" and report.degraded and "timed out" in report.summary


def test_vision_failures_become_error_reports(fake, bundle):
    h = fake.harness()
    for spec in ({"output": None, "response": ""}, {"output": GOOD, "exit": 1, "stderr": "quota"},
                 {"no_result": True}, {"output": GOOD, "status": "ERROR"},
                 {"output": {"status": "maybe"}}):
        fake.scenario([spec])
        report = h.run_vision_worker(bundle, "r", HYDROGEN)
        assert report.status == "error" and report.degraded, spec
    gone = bundle.model_copy(update={"path": str(Path(bundle.path) / "missing")})
    assert h.run_vision_worker(gone, "r", HYDROGEN).status == "error"


def test_vision_effort_flag_passed_only_when_set(fake, bundle):
    fake.scenario([{"output": GOOD, "reads": ALL_READ}])
    fake.harness().run_vision_worker(bundle, "r", ModelConfig(model="m", effort="low"))
    argv = fake.calls()[0]["argv"]
    assert argv[argv.index("--effort") + 1] == "low"


def test_media_capabilities(fake):
    caps = fake.harness().media_capabilities()
    assert caps.frames and caps.audio and not caps.video


def test_parse_stream_sums_steps_not_cumulative_result():
    lines = [
        '{"event":"init","conversation_id":"c"}',
        '{"event":"step_update","step_update":{"step_index":1,"state":"ACTIVE","step_type":"agent_response"}}',
        '{"event":"step_update","step_update":{"step_index":1,"state":"DONE","step_type":"agent_response",'
        '"usage":{"input_tokens":10,"cache_read_tokens":100,"output_tokens":1,"thinking_tokens":2}}}',
        'not json',
        '{"event":"step_update","step_update":{"step_index":3,"state":"DONE","step_type":"agent_response",'
        '"usage":{"input_tokens":5,"cache_read_tokens":50,"output_tokens":3,"thinking_tokens":0}}}',
        '{"event":"result","result":{"conversation_id":"c","status":"SUCCESS","response":"hi",'
        '"usage":{"input_tokens":99999,"cache_read_tokens":99999}}}',
    ]
    run = parse_stream("\n".join(lines), AgyRun(returncode=0, wall_s=1.5, stderr=""))
    assert run.ok and run.conversation_id == "c" and run.response == "hi"
    u = run.usage
    assert (u.input, u.cached, u.output, u.thinking, u.duration_s) == (15, 150, 4, 2, 1.5)


def test_cli_probe_with_fake(fake):
    report = probe(str(fake.binary))
    assert report["ok"] and report["version"] == "1.5.17"
    assert report["media"] == {"frames": True, "video": False, "audio": True}
    assert not probe("/nonexistent/agy")["ok"]
