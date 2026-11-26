"""HTTP API: loopback guard, household token, dedupe/cooldown, ack vs feedback, pinning."""

from __future__ import annotations

import json
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

from vs_runtime.contracts import AlertRequest

from vs_notify import NotifyClient
from vs_notify.server import create_app

TOKEN = "test-household-token"  # matches conftest
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def client_from(service, host, token=TOKEN):
    return TestClient(create_app(service, token), base_url="http://127.0.0.1:8790", client=(host, 1234))


# --- POST /v1/alerts is loopback only -------------------------------------------


def test_post_alert_from_loopback_delivers(local, sink, alert_body):
    r = local.post("/v1/alerts", json=alert_body())
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "sent"
    assert rec["deliveries"] == {"rec": "ok", "desktop": "ok"}
    assert rec["request"]["tool_versions"] == {"tools/motion.py": "t9"}
    assert len(sink.got) == 1 and sink.got[0].id == rec["id"]


@pytest.mark.parametrize("host", ["192.168.1.50", "10.0.0.2", "100.64.0.1", "::ffff:192.168.1.5",
                                  "fe80::1", "testclient"])
def test_post_alert_rejects_non_loopback(service, host, sink, alert_body):
    c = client_from(service, host)
    r = c.post("/v1/alerts", json=alert_body(), headers=AUTH)  # a token does not help
    assert r.status_code == 403
    assert sink.got == []
    assert service.list() == []


def test_post_alert_rejects_relayed_through_local_proxy(local, sink, alert_body):
    """A tunnel or reverse proxy on this host connects from loopback but relays a device."""
    for h in ({"X-Forwarded-For": "203.0.113.9"}, {"Forwarded": "for=203.0.113.9"},
              {"X-Real-IP": "203.0.113.9"}):
        assert local.post("/v1/alerts", json=alert_body(), headers=h).status_code == 403
    assert sink.got == []


def test_post_alert_remote_rejected_before_body_parsing(remote):
    for kw in ({"content": b"{not json", "headers": {"content-type": "application/json"}},
               {"json": {}}):
        assert remote.post("/v1/alerts", **kw).status_code == 403
    assert remote.post("/v1/alerts/", json={}).status_code == 403


@pytest.mark.parametrize("host", ["::1", "127.0.0.2", "::ffff:127.0.0.1"])
def test_post_alert_other_loopback_forms_ok(service, host, alert_body):
    assert client_from(service, host).post("/v1/alerts", json=alert_body()).status_code == 200


def test_post_alert_validates_fields(local, alert_body):
    assert local.post("/v1/alerts", json=alert_body(severity="critical")).status_code == 422
    assert local.post("/v1/alerts", json=alert_body(source="device")).status_code == 422
    assert local.post("/v1/alerts", json=alert_body(unknown=1)).status_code == 422


# --- device endpoints need the household token ----------------------------------

BAD_HEADERS = [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN},
               {"Authorization": f"Basic {TOKEN}"}, {"Authorization": "Bearer "},
               {"Authorization": f"Bearer {TOKEN}x"}]


def device_calls(client, alert_id, headers):
    return [
        client.get("/v1/alerts", headers=headers),
        client.get(f"/v1/alerts/{alert_id}", headers=headers),
        client.post(f"/v1/alerts/{alert_id}/ack", json={}, headers=headers),
        client.post(f"/v1/alerts/{alert_id}/feedback", json={"rating": "up"}, headers=headers),
        client.post("/v1/devices/register", json={"platform": "ios", "token": "x"},
                    headers=headers),
    ]


@pytest.mark.parametrize("headers", BAD_HEADERS)
def test_device_endpoints_reject_missing_or_bad_token(local, remote, service, headers, alert_body):
    alert_id = local.post("/v1/alerts", json=alert_body()).json()["id"]
    for client in (local, remote):  # loopback gets no exemption
        assert [c.status_code for c in device_calls(client, alert_id, headers)] == [401] * 5
    rec = service.get(alert_id)
    assert rec.acked_at is None and rec.feedback == []
    assert service.store.devices() == []


def test_no_configured_token_denies_everything(service, local, alert_body):
    alert_id = local.post("/v1/alerts", json=alert_body()).json()["id"]
    c = client_from(service, "127.0.0.1", token=None)
    for h in ({"Authorization": "Bearer "}, {"Authorization": "Bearer None"}, {}):
        assert [r.status_code for r in device_calls(c, alert_id, h)] == [401] * 5


def test_device_endpoints_accept_token_from_lan(local, remote, alert_body):
    alert_id = local.post("/v1/alerts", json=alert_body()).json()["id"]
    codes = [c.status_code for c in device_calls(remote, alert_id, AUTH)]
    assert codes == [200] * 5
    assert remote.get("/v1/alerts/nope", headers=AUTH).status_code == 404
    assert remote.post("/v1/alerts/nope/ack", headers=AUTH).status_code == 404
    assert remote.post("/v1/alerts/nope/feedback", json={"rating": "up"},
                       headers=AUTH).status_code == 404


def test_healthz_open(remote):
    r = remote.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


# --- dedupe and cooldown --------------------------------------------------------


def test_dedupe_within_cooldown_then_resend_after(local, sink, clock, alert_body):
    body = alert_body(dedupe_key="pkg", cooldown_sec=60)
    first = local.post("/v1/alerts", json=body).json()
    clock.t += 30
    second = local.post("/v1/alerts", json=body).json()
    assert second["status"] == "deduped" and second["id"] == first["id"]
    assert len(sink.got) == 1
    # other keys and other topics are independent
    assert local.post("/v1/alerts", json={**body, "dedupe_key": "other"}).json()["status"] == "sent"
    assert local.post("/v1/alerts", json={**body, "topic_id": "garage"}).json()["status"] == "sent"
    clock.t += 31
    third = local.post("/v1/alerts", json=body).json()
    assert third["status"] == "sent" and third["id"] != first["id"]
    assert len(sink.got) == 4


def test_default_cooldown_and_zero_cooldown(local, sink, clock, alert_body):
    body = alert_body(dedupe_key="k")
    local.post("/v1/alerts", json=body)
    clock.t += 299
    assert local.post("/v1/alerts", json=body).json()["status"] == "deduped"  # default 300 s
    clock.t += 2
    assert local.post("/v1/alerts", json=body).json()["status"] == "sent"
    zero = alert_body(dedupe_key="z", cooldown_sec=0)
    assert [local.post("/v1/alerts", json=zero).json()["status"] for _ in range(2)] == ["sent"] * 2


def test_no_dedupe_key_never_deduped(local, sink, alert_body):
    assert [local.post("/v1/alerts", json=alert_body()).json()["status"]
            for _ in range(3)] == ["sent"] * 3


def test_failed_alert_does_not_suppress_retry(service, sink, clock, alert_body):
    sink.fail = True
    service.sinks = [sink]  # only the failing sink
    c = client_from(service, "127.0.0.1")
    first = c.post("/v1/alerts", json=alert_body(dedupe_key="k")).json()
    assert first["status"] == "failed" and first["deliveries"]["rec"].startswith("error")
    sink.fail = False
    assert c.post("/v1/alerts", json=alert_body(dedupe_key="k")).json()["status"] == "sent"


# --- ack and feedback are separate ----------------------------------------------


def test_ack_marks_seen_without_feedback(local, remote, clock, alert_body):
    alert_id = local.post("/v1/alerts", json=alert_body(requires_ack=True)).json()["id"]
    assert len(remote.get("/v1/alerts?unacked=true", headers=AUTH).json()) == 1
    clock.t += 5
    rec = remote.post(f"/v1/alerts/{alert_id}/ack", json={"by": "phone-a"}, headers=AUTH).json()
    assert rec["acked_by"] == "phone-a" and rec["acked_at"]
    assert rec["feedback"] == []
    # a second ack keeps the first
    clock.t += 5
    again = remote.post(f"/v1/alerts/{alert_id}/ack", json={"by": "phone-b"}, headers=AUTH).json()
    assert again["acked_by"] == "phone-a" and again["acked_at"] == rec["acked_at"]
    assert remote.get("/v1/alerts?unacked=true", headers=AUTH).json() == []
    assert len(remote.get("/v1/alerts?unacked=false", headers=AUTH).json()) == 1


def test_feedback_logs_jsonl_pins_bundle_and_leaves_ack(local, remote, topics, clock, alert_body):
    alert_id = local.post("/v1/alerts", json=alert_body()).json()["id"]
    r = remote.post(f"/v1/alerts/{alert_id}/feedback",
                    json={"rating": "down", "note": "tree shadow", "by": "parent"}, headers=AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["pinned"] is True
    assert out["alert"]["acked_at"] is None  # feedback is not an ack
    assert out["alert"]["feedback"][0]["rating"] == "down"

    state = topics / "porch" / "state"
    lines = (state / "feedback.jsonl").read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["alert_id"] == alert_id
    assert rec["rating"] == "down" and rec["note"] == "tree shadow" and rec["who"] == "parent"
    assert rec["bundle_id"] == "b-001" and rec["rubric_version"] == "r1abc"
    assert rec["tool_versions"] == {"tools/motion.py": "t9"}
    assert rec["timestamp"].startswith("2027-01-15")  # the injected clock, ISO 8601 UTC
    assert rec["saved_bundle"] == "state/saved/b-001"
    saved = state / "saved" / "b-001"
    assert (saved / "cam1" / "contact_sheet.jpg").read_bytes() == b"\xff\xd8fake"
    assert (saved / "bundle.json").exists()

    # the rolling buffer may now expire the original; the saved copy stays
    orig = state / "bundles" / "b-001"
    for f in orig.rglob("*"):
        if f.is_file():
            f.chmod(stat.S_IWUSR | stat.S_IRUSR)
    import shutil
    shutil.rmtree(orig)
    clock.t += 3600
    later = remote.post(f"/v1/alerts/{alert_id}/feedback", json={"rating": "up"}, headers=AUTH)
    assert later.json()["pinned"] is True  # already saved
    assert len((state / "feedback.jsonl").read_text().splitlines()) == 2
    assert [f["rating"] for f in later.json()["alert"]["feedback"]] == ["down", "up"]


def test_feedback_without_bundle_or_topic_dir_still_logged(local, remote, topics, alert_body):
    no_bundle = local.post("/v1/alerts", json=alert_body(bundle_id=None)).json()["id"]
    out = remote.post(f"/v1/alerts/{no_bundle}/feedback", json={"rating": "up"},
                      headers=AUTH).json()
    assert out["pinned"] is False
    assert json.loads((topics / "porch/state/feedback.jsonl").read_text())["bundle_id"] is None

    unknown = local.post("/v1/alerts", json=alert_body(topic_id="ghost")).json()["id"]
    out = remote.post(f"/v1/alerts/{unknown}/feedback", json={"rating": "up"}, headers=AUTH).json()
    assert out["pinned"] is False and len(out["alert"]["feedback"]) == 1


def test_feedback_rejects_path_traversal_bundle(local, remote, topics, alert_body):
    alert_id = local.post("/v1/alerts", json=alert_body(bundle_id="../../etc")).json()["id"]
    out = remote.post(f"/v1/alerts/{alert_id}/feedback", json={"rating": "up"},
                      headers=AUTH).json()
    assert out["pinned"] is False
    assert not (topics / "porch" / "state" / "saved").exists()


def test_feedback_validation(local, remote, alert_body):
    alert_id = local.post("/v1/alerts", json=alert_body()).json()["id"]
    for bad in ({"rating": "meh"}, {}, {"rating": "up", "note": "x" * 1001}):
        assert remote.post(f"/v1/alerts/{alert_id}/feedback", json=bad,
                           headers=AUTH).status_code == 422


def test_on_feedback_hook(service, local, remote, alert_body):
    seen = []
    service.on_feedback = lambda fb, rec: seen.append((fb.rating, rec.id))
    alert_id = local.post("/v1/alerts", json=alert_body()).json()["id"]
    remote.post(f"/v1/alerts/{alert_id}/feedback", json={"rating": "up"}, headers=AUTH)
    assert seen == [("up", alert_id)]


# --- listing and devices --------------------------------------------------------


def test_list_filters(local, remote, clock, alert_body):
    local.post("/v1/alerts", json=alert_body(severity="info"))
    clock.t += 10
    local.post("/v1/alerts", json=alert_body(severity="urgent", topic_id="garage"))
    all_ = remote.get("/v1/alerts", headers=AUTH).json()
    assert [a["request"]["severity"] for a in all_] == ["urgent", "info"]  # newest first
    assert len(remote.get("/v1/alerts?topic_id=garage", headers=AUTH).json()) == 1
    assert len(remote.get("/v1/alerts?severity=info", headers=AUTH).json()) == 1
    since = all_[0]["created_at"]
    assert len(remote.get("/v1/alerts", params={"since": since}, headers=AUTH).json()) == 1
    assert len(remote.get("/v1/alerts?limit=1", headers=AUTH).json()) == 1
    assert remote.get("/v1/alerts?limit=0", headers=AUTH).status_code == 422


def test_register_device_upserts(remote, service):
    a = remote.post("/v1/devices/register", json={"platform": "ios", "token": "apns-1",
                                                  "name": "Phone"}, headers=AUTH).json()
    b = remote.post("/v1/devices/register", json={"platform": "ios", "token": "apns-1",
                                                  "name": "Phone 2"}, headers=AUTH).json()
    assert a["id"] == b["id"] and b["name"] == "Phone 2"
    assert "token" not in a  # never echoed back
    remote.post("/v1/devices/register", json={"platform": "android", "token": "fcm-1"},
                headers=AUTH)
    assert len(service.store.devices()) == 2
    assert remote.post("/v1/devices/register", json={"platform": "web", "token": "x"},
                       headers=AUTH).status_code == 422


# --- clients --------------------------------------------------------------------


def test_http_client_over_real_loopback(service, sink):
    """NotifyClient against a real uvicorn server bound to 127.0.0.1 (offline)."""
    import socket
    import threading
    import time

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(service, TOKEN), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    client = NotifyClient(base_url=f"http://127.0.0.1:{port}")
    try:
        for _ in range(100):
            if client.healthy():
                break
            time.sleep(0.05)
        rec = client.send_alert(AlertRequest(topic_id="porch", severity="urgent", title="t",
                                             source="watchdog"))
        assert rec.status == "sent" and rec.request.source == "watchdog"
        assert sink.got[0].id == rec.id
        listed = httpx.get(f"http://127.0.0.1:{port}/v1/alerts", headers=AUTH, trust_env=False)
        assert listed.json()[0]["id"] == rec.id
    finally:
        client.close()
        server.should_exit = True
        thread.join(5)


def test_service_is_an_in_process_notifier(service, sink):
    from vs_runtime.contracts import Notifier

    assert isinstance(service, Notifier)
    rec = service.send_alert(AlertRequest(topic_id="porch", severity="info", title="t"))
    assert rec.status == "sent" and sink.got[0].id == rec.id


def test_post_alert_rejects_dns_rebinding_host(service, sink, alert_body):
    """A web page that rebinds its own name to 127.0.0.1 connects from loopback, but the
    browser still sends the attacker's host name."""
    for base in ("http://evil.example:8790", "http://testserver"):
        c = TestClient(create_app(service, TOKEN), base_url=base, client=("127.0.0.1", 1234))
        assert c.post("/v1/alerts", json=alert_body()).status_code == 403
    for base in ("http://localhost:8790", "http://[::1]:8790", "http://127.0.0.1"):
        c = TestClient(create_app(service, TOKEN), base_url=base, client=("127.0.0.1", 1234))
        assert c.post("/v1/alerts", json=alert_body()).status_code == 200
    assert len(sink.got) == 3


def test_load_config_shares_one_file_with_the_runtime(tmp_path):
    """`vs-notify --config` reads the runtime's vision-sentinel.toml: it ignores the runtime's
    `url` pointer and resolves a relative `db` next to the config file, as the runtime does."""
    from vs_notify.server import load_config

    cfg = tmp_path / "conf" / "vision-sentinel.toml"
    cfg.parent.mkdir()
    cfg.write_text('[notify]\nurl = "http://127.0.0.1:8790"\ndb = "../data/notify.sqlite"\nport = 8791\n')
    loaded = load_config(cfg)
    assert loaded.port == 8791
    assert loaded.db_path() == str((tmp_path / "data" / "notify.sqlite").resolve())
    assert load_config(None).db is None
