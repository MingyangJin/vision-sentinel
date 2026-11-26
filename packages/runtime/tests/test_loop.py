import json

from vs_runtime.contracts import OrchestratorAction
from vs_runtime.counters import unreviewed_ticks
from vs_runtime.fakes import FakeHarness
from vs_runtime.loop import TopicLoop


def kinds(harness):
    return [(e.kind, [r.bundle_id for r in e.reports], e.late) for e in harness.events]


def test_every_tick_is_reviewed_with_counters(loop, harness, at):
    harness.reports = [{"status": "notable", "conditions": ["door_open"]}] * 3
    outs = [loop.tick(at(i * 60)) for i in range(3)]
    assert all(o.delivered and not o.held for o in outs)
    assert [k for k, _, _ in kinds(harness)] == ["report"] * 3
    last = harness.events[-1]
    assert last.reports[0].counters.conditions["door_open"].count == 3
    assert last.reports[0].counters.conditions["door_open"].duration_s == 120
    assert last.counters.ticks_total == 3
    assert not unreviewed_ticks(loop.log.records())
    usage = [r["usage"] for r in loop.log.records() if r["type"] in ("tick", "review")]
    assert all(u["input"] > 0 for u in usage)  # token usage recorded for both tiers


def test_quiet_policy_digests_all_clear_and_wakes_on_other(loop, harness, topic, at):
    topic.update_config(review_policy={"digest_interval_sec": 180, "all_clear_definition": "no parcel"})
    harness.reports = ["clear", "clear", "clear", "clear", "notable", "clear"]
    outs = [loop.tick(at(i * 60)) for i in range(6)]
    assert [o.held for o in outs] == [True, True, True, False, False, True]
    assert [o.delivered for o in outs] == [False, False, False, True, True, False]
    ids = [o.bundle.bundle_id for o in outs]
    assert kinds(harness) == [
        ("digest", ids[0:4], False),  # interval elapsed at tick 3: one digest turn
        ("report", [ids[4]], False),  # non-clear: delivered immediately
    ]
    # Revoking the policy restores every-tick review; the held report is flushed first.
    loop.dispatch([OrchestratorAction(name="update_review_policy",
                                      args={"digest_interval_sec": None})], None)
    harness.reports = ["clear"]
    out = loop.tick(at(360))
    assert out.delivered and kinds(harness)[-2:] == [("digest", [ids[5]], False),
                                                     ("report", [out.bundle.bundle_id], False)]


def test_quiet_policy_never_holds_degraded_or_flagged(loop, harness, topic, at):
    topic.update_config(review_policy={"digest_interval_sec": 600})
    harness.reports = [{"status": "clear", "suggest_review": True}, {"status": "clear", "degraded": True}]
    assert loop.tick(at(0)).delivered and loop.tick(at(60)).delivered


def test_outage_queues_and_replays_late_in_order(loop, harness, at):
    assert loop.tick(at(0)).delivered
    harness.available = False
    down = [loop.tick(at(60 * i)) for i in (1, 2, 3)]
    assert not any(o.delivered for o in down) and not loop.available
    assert len(unreviewed_ticks(loop.log.records())) == 3
    harness.available = True
    harness.events.clear()
    out = loop.tick(at(240))
    assert out.delivered and out.late_replayed == 3
    late = harness.events[0]
    assert late.late and late.kind == "digest"
    assert [r.bundle_id for r in late.reports] == [o.bundle.bundle_id for o in down]
    assert all(r.late for r in late.reports)
    assert harness.events[1].kind == "report" and not harness.events[1].late
    assert not unreviewed_ticks(loop.log.records())


def test_restart_replays_backlog_from_topic_dir(topic, hub, notifier, at):
    h1 = FakeHarness()
    h1.available = False
    first = TopicLoop(topic, h1, hub, notifier)
    lost = [first.tick(at(i * 60)).bundle.bundle_id for i in range(2)]
    # A new process: fresh loop and a fresh session, same Topic directory.
    h2 = FakeHarness()
    second = TopicLoop(topic, h2, hub, notifier)
    out = second.tick(at(120))
    assert out.late_replayed == 2
    assert [r.bundle_id for r in h2.events[0].reports] == lost and h2.events[0].late
    assert h2.events[1].reports[0].bundle_id == out.bundle.bundle_id


def test_urgent_report_notifies_through_main_agent(loop, harness, notifier, topic, at):
    harness.reports = [{"status": "urgent", "summary": "parcel taken", "conditions": ["person"]}]
    out = loop.tick(at(0))
    assert out.actions[0]["name"] == "send_notification" and out.actions[0]["ok"]
    req = notifier.sent[0]
    assert req.topic_id == topic.id and req.source == "main_agent" and req.severity == "urgent"
    assert req.bundle_id == out.bundle.bundle_id and req.camera_ids == ["cam1"]
    assert req.rubric_version == topic.rubric_version()
    harness.reports = ["clear"]
    loop.tick(at(90))
    assert harness.events[-1].counters.seconds_since_last_alert["urgent"] >= 90 - 1


def test_runtime_never_notifies_on_its_own(loop, harness, notifier, at):
    harness.auto_alert = False
    harness.reports = ["urgent", "urgent"]
    loop.tick(at(0))
    loop.tick(at(60))
    assert notifier.sent == []


def test_vision_failure_becomes_error_report(loop, harness, at):
    def boom(*a, **k):
        raise RuntimeError("worker crashed")

    harness.run_vision_worker = boom
    out = loop.tick(at(0))
    assert out.report.status == "error" and out.report.degraded and out.delivered
    assert "worker crashed" in harness.events[0].reports[0].summary


def test_action_dispatch_and_followups(loop, harness, topic, at):
    out = loop.tick(at(0))
    bid = out.bundle.bundle_id
    harness.scripted_actions = [[
        OrchestratorAction(name="inspect_media", args={"bundle_id": bid, "camera_id": "cam1",
                                                       "asset": "contact_sheet.jpg"}),
        OrchestratorAction(name="update_schedule", args={"cadence_sec": 30, "window_sec": 8}),
        OrchestratorAction(name="edit_topic_tool", args={"path": "size.py",
                                                         "code": "print('{\"n\": 1}')"}),
        OrchestratorAction(name="test_topic_tool", args={"bundle_id": bid}),
        OrchestratorAction(name="send_notification", args={"title": "x", "bogus": 1}),
        OrchestratorAction(name="inspect_media", args={"bundle_id": bid, "asset": "../../../topic.toml"}),
    ]]
    harness.events.clear()
    loop.tick(at(60))
    followup = harness.events[-1]
    assert followup.kind == "system"
    results = json.loads(followup.message.split("\n", 1)[1])
    by_name = [(r["name"], r["ok"]) for r in results]
    assert by_name == [("inspect_media", True), ("test_topic_tool", True),
                       ("send_notification", False), ("inspect_media", False)]
    assert results[0]["result"]["paths"][0].endswith(f"{bid}/cam1/contact_sheet.jpg")
    assert results[1]["result"]["size.py"]["output"] == {"n": 1}
    assert topic.config.cadence_sec == 30 and topic.config.window_sec == 8


def test_camera_drop_event_is_delivered_and_kept_through_outage(loop, harness, at):
    from vs_runtime.contracts import OrchestratorEvent

    harness.available = False
    loop.post_event(OrchestratorEvent(topic_id="porch", kind="camera_drop", message="cam1 offline"))
    loop.drain_events()
    harness.available = True
    harness.events.clear()
    loop.tick(at(0))
    assert harness.events[0].kind == "camera_drop" and harness.events[0].late
