import os
import threading
import time

from vs_runtime.contracts import ModelConfig, OrchestratorEvent

from vs_harness_antigravity.orchestrator import load_session

GOOD = {"status": "clear", "summary": "s", "observations": [], "conditions": [], "confidence": 1,
        "suggest_review": False}
REPLY = {"reply": "ok", "actions": []}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_for_pids(fake, n: int) -> list[int]:
    deadline = time.monotonic() + 5
    while len(fake.pids()) < n and time.monotonic() < deadline:
        time.sleep(0.05)
    return fake.pids()


def run_in_thread(fn, *args):
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("r", fn(*args)))
    t.start()
    return t, box


def test_close_kills_running_vision_worker_and_its_children(fake, bundle):
    fake.scenario([{"output": GOOD, "sleep": 60, "grandchild": True}])
    h = fake.harness()
    t, box = run_in_thread(h.run_vision_worker, bundle, "r", ModelConfig(model="m"))
    pids = wait_for_pids(fake, 2)
    t0 = time.monotonic()
    h.close()
    h.close()  # idempotent
    t.join(5)
    assert not t.is_alive() and time.monotonic() - t0 < 5
    report = box["r"]
    assert report.status == "error" and "closed" in report.summary
    time.sleep(0.2)
    assert len(pids) == 2 and not any(alive(p) for p in pids)


def test_close_cancels_main_agent_turn_without_rebuild(fake, topic):
    fake.scenario([{"cid": "c1", "output": REPLY}, {"cid": "$resume", "output": REPLY, "sleep": 60}])
    h = fake.harness()
    h.deliver_to_orchestrator(topic, OrchestratorEvent(topic_id="t1", kind="system", message="hi"))
    t, box = run_in_thread(h.deliver_to_orchestrator, topic,
                           OrchestratorEvent(topic_id="t1", kind="system", message="again"))
    wait_for_pids(fake, 2)
    h.close()
    t.join(5)
    assert not t.is_alive()
    res = box["r"]
    assert not res.ok and "closed" in res.error
    assert len(fake.pids()) == 2  # no fresh-session rebuild after a cancelled turn
    assert load_session(topic.topic_dir)["conversation_id"] == "c1"


def test_calls_after_close_fail_fast(fake, bundle, topic):
    h = fake.harness()
    h.close()
    assert h.run_vision_worker(bundle, "r", ModelConfig(model="m")).status == "error"
    assert not h.chat_with_orchestrator(topic, "hi").ok
    assert fake.pids() == []
