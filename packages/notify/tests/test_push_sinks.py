"""Offline tests for M3 APNs/FCM direct push sinks and M4 CameraSpeakerSink."""

from __future__ import annotations

import json
import urllib.parse
import wave

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from vs_runtime.contracts import AlertRecord, AlertRequest

from vs_notify import (
    ApnsSink,
    CameraSpeakerSink,
    FcmSink,
    NotifyService,
    Store,
    build_notifier,
    build_sinks,
)


@pytest.fixture(scope="module")
def ec_keypair():
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


@pytest.fixture(scope="module")
def rsa_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


def make_alert(**kw) -> AlertRecord:
    defaults = dict(
        topic_id="porch",
        severity="warning",
        title="Motion detected",
        body="Person near front door",
        bundle_id="b-001",
        camera_ids=["cam1"],
        dedupe_key="person-porch",
        requires_ack=True,
    )
    defaults.update(kw)
    return AlertRecord(id="alert-42", request=AlertRequest(**defaults))


# --- APNs (M3) ------------------------------------------------------------------


def test_apns_signs_es256_jwt_sends_http2_and_caches(ec_keypair, tmp_path, clock):
    priv_pem, pub_pem = ec_keypair
    key_file = tmp_path / "AuthKey_TEST123456.p8"
    key_file.write_text(priv_pem)

    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200)

    store = Store()
    store.register_device("ios", "ios-tok-1", "iPhone", clock())
    sink = ApnsSink(
        team_id="TEAM123456",
        key_id="KEY1234567",
        bundle_id="com.example.visionsentinel",
        key_path=key_file,
        sandbox=True,
        store=store,
        transport=httpx.MockTransport(handler),
        clock=clock,
    )
    sink.send(make_alert())

    assert len(seen) == 1
    req = seen[0]
    assert str(req.url) == "https://api.sandbox.push.apple.com/3/device/ios-tok-1"
    assert req.headers["apns-topic"] == "com.example.visionsentinel"
    assert req.headers["apns-push-type"] == "alert"
    assert req.headers["apns-priority"] == "10"
    assert req.headers["apns-collapse-id"] == "person-porch"

    auth = req.headers["authorization"]
    assert auth.startswith("bearer ")
    raw_jwt = auth.split(" ", 1)[1]
    hdr = jwt.get_unverified_header(raw_jwt)
    assert hdr["alg"] == "ES256" and hdr["kid"] == "KEY1234567"
    claims = jwt.decode(raw_jwt, pub_pem, algorithms=["ES256"], options={"verify_iat": False})
    assert claims["iss"] == "TEAM123456" and claims["iat"] == int(clock())

    body = json.loads(req.content)
    assert body["aps"]["category"] == "VISION_ALERT"
    assert body["aps"]["interruption-level"] == "time-sensitive"
    assert body["aps"]["alert"]["title"] == "Motion detected"
    assert body["aps"]["alert"]["subtitle"] == "porch"
    assert body["alert_id"] == "alert-42"
    assert body["topic_id"] == "porch"
    assert body["severity"] == "warning"
    assert body["bundle_id"] == "b-001"
    assert body["requires_ack"] is True

    # Cached JWT reused within 50 min, refreshed after
    clock.t += 600
    sink.send(make_alert(severity="info"))
    assert seen[1].headers["authorization"] == auth
    assert seen[1].headers["apns-priority"] == "5"
    assert json.loads(seen[1].content)["aps"]["interruption-level"] == "active"

    clock.t += 3001
    sink.send(make_alert())
    assert seen[2].headers["authorization"] != auth


def test_apns_prod_url_critical_level_and_env_key(ec_keypair, monkeypatch, clock):
    priv_pem, _ = ec_keypair
    monkeypatch.setenv("TEST_APNS_PEM", priv_pem)
    seen: list[httpx.Request] = []

    sink = ApnsSink(
        team_id="TEAM1",
        key_id="KEY1",
        bundle_id="com.example.app",
        private_key="env:TEST_APNS_PEM",
        sandbox=False,
        critical_alerts=True,
        device_tokens=["dev-a"],
        transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200))[1]),
        clock=clock,
    )
    sink.send(make_alert(severity="urgent"))
    assert str(seen[0].url) == "https://api.push.apple.com/3/device/dev-a"
    aps = json.loads(seen[0].content)["aps"]
    assert aps["interruption-level"] == "critical"
    assert aps["sound"] == {"critical": 1, "name": "default", "volume": 1.0}


def test_apns_prunes_dead_tokens_and_handles_failures(ec_keypair, clock):
    priv_pem, _ = ec_keypair
    store = Store()
    store.register_device("ios", "dead-410", "Old Phone", clock())
    store.register_device("ios", "bad-400", "Corrupt", clock())
    store.register_device("ios", "good-200", "Active", clock())

    def handler(req: httpx.Request) -> httpx.Response:
        tok = req.url.path.rsplit("/", 1)[-1]
        if tok == "dead-410":
            return httpx.Response(410, json={"reason": "Unregistered"})
        if tok == "bad-400":
            return httpx.Response(400, json={"reason": "BadDeviceToken"})
        return httpx.Response(200)

    sink = ApnsSink(
        team_id="T", key_id="K", bundle_id="com.example.app", private_key=priv_pem,
        store=store, transport=httpx.MockTransport(handler), clock=clock,
    )
    # One device succeeded, so overall delivery succeeds while dead tokens are removed
    sink.send(make_alert())
    assert [d["token"] for d in store.devices("ios")] == ["good-200"]

    # When all remaining devices fail, raises RuntimeError
    fail_sink = ApnsSink(
        team_id="T", key_id="K", bundle_id="com.example.app", private_key=priv_pem,
        store=store,
        transport=httpx.MockTransport(lambda r: httpx.Response(503, json={"reason": "Unavailable"})),
        clock=clock,
    )
    with pytest.raises(RuntimeError, match="APNs delivery failed"):
        fail_sink.send(make_alert())

    # Zero devices is a clean no-op
    empty_sink = ApnsSink(
        team_id="T", key_id="K", bundle_id="com.example.app", private_key=priv_pem,
        store=Store(),
        transport=httpx.MockTransport(lambda r: pytest.fail("should not hit network")),
    )
    empty_sink.send(make_alert())


# --- FCM (M3) -------------------------------------------------------------------


def test_fcm_oauth2_exchange_sends_v1_data_payload_and_caches(rsa_keypair, tmp_path, clock):
    priv_pem, pub_pem = rsa_keypair
    sa = {
        "type": "service_account",
        "project_id": "demo-project-123",
        "private_key_id": "pk-id-9",
        "private_key": priv_pem,
        "client_email": "fcm-sender@demo-project-123.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    sa_file = tmp_path / "fcm-sa.json"
    sa_file.write_text(json.dumps(sa))

    oauth_calls: list[httpx.Request] = []
    fcm_calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(req.url):
            oauth_calls.append(req)
            return httpx.Response(200, json={"access_token": "ya29.mock-token", "expires_in": 3600})
        fcm_calls.append(req)
        return httpx.Response(200, json={"name": "projects/demo-project-123/messages/1"})

    store = Store()
    store.register_device("android", "fcm-tok-1", "Pixel", clock())
    sink = FcmSink(
        service_account_path=sa_file,
        store=store,
        transport=httpx.MockTransport(handler),
        clock=clock,
    )
    sink.send(make_alert(severity="urgent"))

    assert len(oauth_calls) == 1
    form = urllib.parse.parse_qs(oauth_calls[0].content.decode())
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    assertion = form["assertion"][0]
    hdr = jwt.get_unverified_header(assertion)
    assert hdr["alg"] == "RS256" and hdr["kid"] == "pk-id-9"
    claims = jwt.decode(
        assertion,
        pub_pem,
        algorithms=["RS256"],
        audience="https://oauth2.googleapis.com/token",
        options={"verify_iat": False},
    )
    assert claims["iss"] == sa["client_email"]
    assert claims["iat"] == int(clock()) and claims["exp"] == int(clock()) + 3600
    assert claims["scope"] == "https://www.googleapis.com/auth/firebase.messaging"

    assert len(fcm_calls) == 1
    req = fcm_calls[0]
    assert str(req.url) == "https://fcm.googleapis.com/v1/projects/demo-project-123/messages:send"
    assert req.headers["Authorization"] == "Bearer ya29.mock-token"
    msg = json.loads(req.content)["message"]
    assert msg["token"] == "fcm-tok-1"
    assert msg["android"] == {"priority": "HIGH", "collapse_key": "person-porch"}
    assert "notification" not in msg  # data-only for background channel routing + Wear OS actions
    assert msg["data"]["alert_id"] == "alert-42"
    assert msg["data"]["topic_id"] == "porch"
    assert msg["data"]["severity"] == "urgent"
    assert msg["data"]["channel_id"] == "sentinel_urgent"
    assert msg["data"]["requires_ack"] == "true"
    assert msg["data"]["bundle_id"] == "b-001"

    # Reuses cached OAuth token; non-urgent maps to sentinel_default
    clock.t += 600
    sink.send(make_alert(severity="info", requires_ack=False))
    assert len(oauth_calls) == 1
    assert json.loads(fcm_calls[1].content)["message"]["data"]["channel_id"] == "sentinel_default"
    assert json.loads(fcm_calls[1].content)["message"]["data"]["requires_ack"] == "false"

    # Refreshes OAuth token once past expiry window
    clock.t += 3100
    sink.send(make_alert())
    assert len(oauth_calls) == 2


def test_fcm_prunes_unregistered_tokens_and_env_sa(rsa_keypair, monkeypatch, clock):
    priv_pem, _ = rsa_keypair
    sa = {
        "project_id": "proj-1",
        "private_key": priv_pem,
        "client_email": "bot@proj-1.iam.gserviceaccount.com",
    }
    monkeypatch.setenv("TEST_FCM_SA", json.dumps(sa))
    store = Store()
    store.register_device("android", "stale-tok", "Old", clock())
    store.register_device("android", "live-tok", "New", clock())

    def handler(req: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(req.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        tok = json.loads(req.content)["message"]["token"]
        if tok == "stale-tok":
            return httpx.Response(404, json={
                "error": {"code": 404, "status": "NOT_FOUND",
                          "details": [{"errorCode": "UNREGISTERED"}]}
            })
        return httpx.Response(200, json={"name": "ok"})

    sink = FcmSink(
        service_account_json="env:TEST_FCM_SA",
        store=store,
        transport=httpx.MockTransport(handler),
        clock=clock,
    )
    sink.send(make_alert())
    assert [d["token"] for d in store.devices("android")] == ["live-tok"]

    # All fail -> raises
    fail_sink = FcmSink(
        service_account_json=sa,
        device_tokens=["bad-tok"],
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            if "oauth2" in str(r.url)
            else httpx.Response(500, json={"error": {"status": "INTERNAL"}})
        ),
        clock=clock,
    )
    with pytest.raises(RuntimeError, match="FCM delivery failed"):
        fail_sink.send(make_alert())


def test_notify_service_binds_store_to_push_sinks(ec_keypair, topics, clock):
    priv_pem, _ = ec_keypair
    store = Store()
    store.register_device("ios", "bound-ios-tok", "Phone", clock())
    seen: list[str] = []
    apns = ApnsSink(
        team_id="T", key_id="K", bundle_id="com.example.app", private_key=priv_pem,
        transport=httpx.MockTransport(lambda r: (seen.append(str(r.url)), httpx.Response(200))[1]),
        clock=clock,
    )
    svc = NotifyService(store, [apns], clock=clock)
    rec = svc.send_alert(AlertRequest(topic_id="porch", severity="warning", title="Hi"))
    assert rec.deliveries == {"apns": "ok"}
    assert seen == ["https://api.push.apple.com/3/device/bound-ios-tok"]


# --- CameraSpeakerSink (M4) -----------------------------------------------------


def test_camera_speaker_requires_double_opt_in_and_clamps_volume(tmp_path, clock):
    played: list[tuple[str, str, float]] = []

    def fake_play(cam_id, audio_path, volume):
        played.append((cam_id, str(audio_path), volume))

    sink = CameraSpeakerSink(
        play_alert=fake_play,
        enabled_cameras=["porch_cam"],
        volume=0.8,  # requested higher than cap -> must clamp!
        max_volume=0.3,
        volume_caps={"porch_cam": 0.25},
    )
    svc = NotifyService(Store(), [sink], clock=clock)

    # 1. speaker=False -> never plays, not even in deliveries
    r1 = svc.send_alert(AlertRequest(
        topic_id="porch", severity="urgent", title="No speaker",
        camera_ids=["porch_cam"], speaker=False,
    ))
    assert played == []
    assert "camera_speaker" not in r1.deliveries

    # Direct send() call also enforces speaker=False gate
    sink.send(make_alert(camera_ids=["porch_cam"], speaker=False))
    assert played == []

    # 2. speaker=True, but camera is NOT opted-in -> never plays
    r2 = svc.send_alert(AlertRequest(
        topic_id="study", severity="urgent", title="Quiet room",
        camera_ids=["quiet_cam"], speaker=True,
    ))
    assert played == []
    assert "camera_speaker" not in r2.deliveries

    # 3. speaker=True with mixed cameras -> plays ONLY on opted-in camera, clamped to 0.25
    r3 = svc.send_alert(AlertRequest(
        topic_id="porch", severity="urgent", title="Step back",
        camera_ids=["quiet_cam", "porch_cam"], speaker=True,
    ))
    assert r3.deliveries == {"camera_speaker": "ok"}
    assert len(played) == 1
    cam_id, wav_path, vol = played[0]
    assert cam_id == "porch_cam" and vol == 0.25
    with wave.open(wav_path, "rb") as wf:
        assert wf.getnchannels() == 1 and wf.getframerate() == 16000 and wf.getnframes() > 1000


def test_camera_speaker_wired_via_build_notifier_and_registry(tmp_path):
    played = []
    custom_wav = tmp_path / "warn.wav"
    custom_wav.write_bytes(b"RIFFfake")

    svc = build_notifier(
        {
            "db": ":memory:",
            "sinks": [
                {
                    "type": "camera_speaker",
                    "enabled_cameras": ["driveway"],
                    "audio_path": str(custom_wav),
                    "volume": 0.2,
                }
            ],
        },
        play_alert=lambda cam, path, vol: played.append((cam, path, vol)),
    )
    rec = svc.send_alert(AlertRequest(
        topic_id="driveway", severity="warning", title="Car",
        camera_ids=["driveway"], speaker=True,
    ))
    assert rec.deliveries == {"camera_speaker": "ok"}
    assert played == [("driveway", custom_wav, 0.2)]


def test_registry_supports_all_sink_types(ec_keypair, rsa_keypair):
    ec_priv, _ = ec_keypair
    rsa_priv, _ = rsa_keypair
    sinks = build_sinks([
        {"type": "desktop", "dry_run": True},
        {"type": "webhook", "url": "https://hooks.example/a"},
        {"type": "apns", "team_id": "T", "key_id": "K", "bundle_id": "com.example.app",
         "private_key": ec_priv},
        {"type": "fcm", "service_account_json": {
            "project_id": "p", "private_key": rsa_priv, "client_email": "a@p.iam.gserviceaccount.com"
        }},
        {"type": "camera_speaker", "enabled_cameras": ["cam1"]},
    ])
    assert [s.name for s in sinks] == ["desktop", "webhook", "apns", "fcm", "camera_speaker"]
