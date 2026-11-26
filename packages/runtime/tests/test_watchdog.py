from vs_runtime.counters import TickLog
from vs_runtime.fakes import FakeNotifier
from vs_runtime.watchdog import Watchdog, stale_limit_sec


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def make(topic, hub, notifier, t0, events=None):
    clock = Clock(t0)
    wd = Watchdog([topic], hub, notifier, clock=clock,
                  on_event=(lambda tid, ev: events.append((tid, ev))) if events is not None else None)
    return wd, clock


def test_stale_limit_includes_cadence_and_digest(topic):
    assert stale_limit_sec(topic) == 600 + 60
    topic.update_config(review_policy={"digest_interval_sec": 900})
    assert stale_limit_sec(topic) == 600 + 60 + 900


def test_review_progress_not_liveness_drives_alert(topic, hub, notifier, t0, at):
    wd, clock = make(topic, hub, notifier, t0)
    log = TickLog(topic.ticks_path)
    # Ticks keep coming (the process is alive) but no turn succeeds.
    for i in range(12):
        log.append({"type": "tick", "at": at(i * 60).isoformat(), "bundle_id": f"b{i}",
                    "status": "clear"})
        log.append({"type": "review", "ok": False, "bundle_ids": [f"b{i}"]})
    clock.t = at(600)
    assert wd.check_once()["alerts"] == [] and notifier.sent == []
    clock.t = at(661)
    out = wd.check_once()
    assert out["alerts"] == [topic.id] and out["topics"][topic.id]["stale"]
    req = notifier.sent[0]
    assert req.source == "watchdog" and req.severity == "urgent" and req.requires_ack
    assert req.dedupe_key == f"watchdog:review-stale:{topic.id}"
    clock.t = at(900)
    wd.check_once()
    assert len(notifier.sent) == 1  # one alert per stale spell
    # Review resumes: fresh again, and a later stall alerts again.
    log.append({"type": "tick", "at": at(890).isoformat(), "bundle_id": "b99", "status": "clear"})
    log.append({"type": "review", "ok": True, "bundle_ids": ["b99"]})
    assert not wd.check_once()["topics"][topic.id]["stale"]
    clock.t = at(890 + 661)
    wd.check_once()
    assert len(notifier.sent) == 2
    assert (topic.state_dir / "health.json").exists()


def test_healthy_reviews_never_alert(topic, hub, notifier, t0, at):
    wd, clock = make(topic, hub, notifier, t0)
    log = TickLog(topic.ticks_path)
    for i in range(30):
        log.append({"type": "tick", "at": at(i * 60).isoformat(), "bundle_id": f"b{i}",
                    "status": "clear"})
        log.append({"type": "review", "ok": True, "bundle_ids": [f"b{i}"]})
        clock.t = at(i * 60 + 30)
        assert not wd.check_once()["alerts"]
    assert notifier.sent == []


def test_camera_drop_goes_to_main_agent_not_user(topic, hub, notifier, t0, at):
    events = []
    wd, clock = make(topic, hub, notifier, t0, events)
    wd.check_once()
    hub.status["cam1"] = "offline"
    clock.t = at(15)
    wd.check_once()
    wd.check_once()  # no repeat while it stays down
    hub.status["cam1"] = "ok"
    wd.check_once()
    assert [(tid, ev.kind) for tid, ev in events] == [(topic.id, "camera_drop"),
                                                      (topic.id, "camera_restored")]
    assert notifier.sent == []


def test_failed_stale_alert_is_retried(topic, hub, t0, at):
    bad = FakeNotifier(fail=True)
    wd, clock = make(topic, hub, bad, t0)
    clock.t = at(700)
    assert wd.check_once()["alerts"] == []
    bad.fail = False
    assert wd.check_once()["alerts"] == [topic.id]
