from vs_runtime.contracts import OrchestratorAction
from vs_runtime.counters import unreviewed_ticks
from vs_runtime.watchdog import Watchdog


def test_deterministic_tool_skips_vision_and_main_when_quiet(loop, harness, hub, notifier, topic, at):
    topic.write_tool(
        "sound_gate.py",
        (
            "import json\n"
            "print(json.dumps({\n"
            "    'skip_vision': True,\n"
            "    'trigger': False,\n"
            "    'status': 'clear',\n"
            "    'summary': 'Quiet room: 38.2 dB SPL (below 55 dB).',\n"
            "    'conditions': ['quiet'],\n"
            "    'observations': ['peak -55.8 dBFS'],\n"
            "}))\n"
        ),
    )

    out1 = loop.tick(at(0))
    out2 = loop.tick(at(60))

    assert out1.vision_skipped and not out1.delivered and not out1.held
    assert out2.vision_skipped and not out2.delivered and not out2.held
    assert len(harness.bundles) == 0
    assert len(harness.events) == 0
    assert not unreviewed_ticks(loop.log.records())
    assert out2.report.usage.input == 0 and out2.report.usage.output == 0
    assert out2.report.counters.ticks_total == 2
    assert out2.report.counters.conditions["quiet"].count == 2

    # Watchdog sees the tool_bypass review timestamps and does not fire a stale alert.
    wd = Watchdog([topic], hub, notifier, clock=lambda: at(90), startup_grace=False)
    check = wd.check_once(at(90))
    assert not check["topics"][topic.id]["stale"]
    assert check["alerts"] == []


def test_deterministic_tool_wakes_main_agent_without_vision_when_triggered(loop, harness, notifier, topic, at):
    topic.write_tool(
        "sound_gate.py",
        (
            "import json\n"
            "print(json.dumps({\n"
            "    'skip_vision': True,\n"
            "    'trigger': True,\n"
            "    'status': 'notable',\n"
            "    'summary': 'Loud noise detected: 68.4 dB SPL (above 55 dB).',\n"
            "    'conditions': ['loud_noise'],\n"
            "    'observations': ['peak -25.6 dBFS'],\n"
            "}))\n"
        ),
    )
    harness.scripted_actions = [[OrchestratorAction(
        name="send_notification",
        args={"severity": "warning", "title": "Loud noise (68.4 dB)", "body": "Exceeded 55 dB threshold."},
    )]]

    out = loop.tick(at(0))

    assert out.vision_skipped and out.delivered and not out.held
    assert len(harness.bundles) == 0
    assert len(harness.events) == 1
    ev = harness.events[0]
    assert "deterministic trigger" in ev.message.lower()
    assert ev.reports[0].raw["deterministic_trigger"] is True
    assert ev.reports[0].raw["triggered"] is True
    assert "68.4 dB SPL" in ev.reports[0].summary
    assert len(notifier.sent) == 1
    assert notifier.sent[0].title == "Loud noise (68.4 dB)"


def test_deterministic_tool_hybrid_mode_runs_vision_when_skip_vision_false(loop, harness, topic, at):
    topic.write_tool(
        "hybrid_gate.py",
        (
            "import json\n"
            "print(json.dumps({'skip_vision': False, 'trigger': True, 'peak_db': 64.0}))\n"
        ),
    )

    out = loop.tick(at(0))

    assert not out.vision_skipped and out.delivered
    assert len(harness.bundles) == 1
    assert len(harness.events) == 1


def test_deterministic_tool_wakes_main_when_bundle_is_degraded(loop, harness, hub, topic, at):
    topic.write_tool(
        "sound_gate.py",
        "import json\nprint(json.dumps({'skip_vision': True, 'trigger': False, 'summary': 'quiet'}))\n",
    )
    hub.status["cam1"] = "offline"

    out = loop.tick(at(0))

    assert out.vision_skipped and out.delivered
    assert len(harness.bundles) == 0
    assert len(harness.events) == 1
    assert harness.events[0].reports[0].degraded is True
