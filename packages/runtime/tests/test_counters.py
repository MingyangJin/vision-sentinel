from vs_runtime.counters import (
    TickLog,
    compute_counters,
    last_reviewed_at,
    unreviewed_ticks,
)


def tick(at, bid, status="clear", conditions=()):
    return {"type": "tick", "at": at.isoformat(), "bundle_id": bid, "status": status,
            "conditions": list(conditions)}


def test_status_and_condition_streaks(at):
    recs = [
        tick(at(0), "b0", "notable", ["door_open"]),
        tick(at(60), "b1", "clear"),
        tick(at(120), "b2", "notable", ["door_open"]),
        tick(at(180), "b3", "notable", ["door_open", "person"]),
        tick(at(240), "b4", "error"),  # no observation: neither extends nor breaks conditions
        tick(at(300), "b5", "notable", ["door_open"]),
    ]
    c = compute_counters(recs, at(310))
    assert c.ticks_total == 6
    assert c.status == "notable" and c.status_streak.count == 1
    door = c.conditions["door_open"]
    assert door.count == 3 and door.since == at(120) and door.duration_s == 180
    assert "person" not in c.conditions  # not active in the latest judged tick


def test_status_streak_counts_consecutive(at):
    recs = [tick(at(i * 60), f"b{i}", "clear") for i in range(5)]
    c = compute_counters(recs, at(300))
    assert c.status_streak.count == 5 and c.status_streak.duration_s == 240


def test_seconds_since_last_alert_per_dedupe_key(at):
    recs = [
        {"type": "alert", "at": at(0).isoformat(), "dedupe_key": "door", "status": "sent"},
        {"type": "alert", "at": at(100).isoformat(), "dedupe_key": "door", "status": "sent"},
        {"type": "alert", "at": at(200).isoformat(), "dedupe_key": "door", "status": "deduped"},
        {"type": "alert", "at": at(150).isoformat(), "dedupe_key": "smoke", "status": "sent"},
    ]
    c = compute_counters(recs, at(300))
    assert c.seconds_since_last_alert == {"door": 200, "smoke": 150, "*": 150}


def test_review_bookkeeping(at, tmp_path):
    log = TickLog(tmp_path / "ticks.jsonl")
    for i in range(3):
        log.append(tick(at(i * 60), f"b{i}"))
    log.append({"type": "review", "ok": True, "bundle_ids": ["b0"]})
    log.append({"type": "review", "ok": False, "bundle_ids": ["b1"]})
    with log.path.open("a") as f:
        f.write('{"type": "tick", "at": "torn')  # crash mid-write
    recs = log.records()
    assert [r["bundle_id"] for r in unreviewed_ticks(recs)] == ["b1", "b2"]
    assert last_reviewed_at(recs) == at(0)
    assert compute_counters(recs, at(200)).last_reviewed_at == at(0)
