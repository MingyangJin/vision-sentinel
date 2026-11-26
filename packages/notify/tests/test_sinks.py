"""Webhook and desktop (dry-run) sinks, the registry, and fan-out isolation."""

from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from vs_runtime.contracts import AlertRecord, AlertRequest

from vs_notify import DesktopSink, NotifyService, Store, WebhookSink, build_notifier, build_sinks


def record(severity="warning", **kw) -> AlertRecord:
    return AlertRecord(id="a1", request=AlertRequest(topic_id="porch", severity=severity,
                                                     title='Gate "open"', body="5 min", **kw))


def test_webhook_posts_json_with_headers(monkeypatch):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200)

    monkeypatch.setenv("HOOK_SECRET", "s3cret")
    sink = WebhookSink("https://hooks.example/x", headers={"X-Key": "env:HOOK_SECRET", "X-A": "b"},
                       transport=httpx.MockTransport(handler))
    sink.send(record(clip_ref="cam1/clip.mp4", bundle_id="b-1"))
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://hooks.example/x"
    assert req.headers["X-Key"] == "s3cret" and req.headers["X-A"] == "b"
    body = json.loads(req.content)
    assert body["id"] == "a1" and body["severity"] == "warning" and body["title"] == 'Gate "open"'
    assert body["clip_ref"] == "cam1/clip.mp4" and body["bundle_id"] == "b-1"


def test_webhook_errors_raise():
    sink = WebhookSink("https://hooks.example/x",
                       transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(RuntimeError, match="HTTP 500"):
        sink.send(record())

    def timeout(req):
        raise httpx.ConnectTimeout("slow", request=req)

    with pytest.raises(httpx.ConnectTimeout):
        WebhookSink("https://h.example", transport=httpx.MockTransport(timeout)).send(record())
    with pytest.raises(RuntimeError, match="NOPE_UNSET"):
        WebhookSink("https://h.example", headers={"A": "env:NOPE_UNSET"},
                    transport=httpx.MockTransport(lambda r: httpx.Response(200))).send(record())


def test_webhook_to_a_real_loopback_listener():
    """A real HTTP POST over 127.0.0.1 to a tiny local listener."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        WebhookSink(f"http://127.0.0.1:{srv.server_port}/hook").send(record())
    finally:
        srv.shutdown()
    assert got[0]["id"] == "a1"


def test_desktop_dry_run_never_spawns(monkeypatch):
    def no_spawn(*a, **k):
        raise AssertionError("dry run must not run a process")

    monkeypatch.setattr(subprocess, "run", no_spawn)
    sink = DesktopSink(dry_run=True)
    sink.send(record())
    assert len(sink.sent) == 1


@pytest.mark.parametrize("platform,tool,expect", [
    ("darwin", "osascript", "osascript"),
    ("linux", "notify-send", "notify-send"),
    ("win32", None, None),
])
def test_desktop_command_per_platform(monkeypatch, platform, tool, expect):
    import vs_notify.sinks.desktop as d

    monkeypatch.setattr(d.sys, "platform", platform)
    monkeypatch.setattr(d.shutil, "which", lambda name: f"/usr/bin/{name}" if name == tool else None)
    cmd = DesktopSink().command(record(severity="urgent"))
    if expect is None:
        assert cmd is None
        return
    assert cmd[0] == expect
    # alert text travels as argv, never inside the script
    assert cmd[-2] == '[urgent] Gate "open"' and cmd[-1] == "5 min"
    assert not any('Gate "open"' in part for part in cmd[:-2])
    if expect == "notify-send":
        assert cmd[cmd.index("-u") + 1] == "critical"


def test_desktop_sound_only_when_enabled(monkeypatch):
    import vs_notify.sinks.desktop as d

    monkeypatch.setattr(d.sys, "platform", "darwin")
    monkeypatch.setattr(d.shutil, "which", lambda name: "/usr/bin/osascript")
    assert not any("sound name" in p for p in DesktopSink().command(record()))
    assert any("sound name" in p for p in DesktopSink(sound=True).command(record()))


def test_desktop_log_only_fallback_prints(monkeypatch, capsys):
    import vs_notify.sinks.desktop as d

    monkeypatch.setattr(d.sys, "platform", "win32")
    DesktopSink().send(record())
    assert 'Gate "open"' in capsys.readouterr().out


def test_desktop_failure_raises(monkeypatch):
    import vs_notify.sinks.desktop as d

    monkeypatch.setattr(d.sys, "platform", "linux")
    monkeypatch.setattr(d.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 1, "", "no display"))
    with pytest.raises(RuntimeError, match="no display"):
        DesktopSink().send(record())


def test_build_sinks_registry():
    sinks = build_sinks([{"type": "desktop", "dry_run": True},
                         {"type": "webhook", "url": "https://h.example", "name": "ntfy",
                          "min_severity": "urgent"}])
    assert [s.name for s in sinks] == ["desktop", "ntfy"]
    with pytest.raises(ValueError, match="unknown sink"):
        build_sinks([{"type": "pager"}])
    with pytest.raises(ValueError, match="unique"):
        build_sinks([{"type": "desktop"}, {"type": "desktop"}])


def test_fan_out_isolates_failures_and_honors_min_severity(clock):
    calls = []

    def ok(req):
        calls.append(json.loads(req.content)["id"])
        return httpx.Response(200)

    good = WebhookSink("https://ok.example", name="good", transport=httpx.MockTransport(ok))
    bad = WebhookSink("https://bad.example", name="bad",
                      transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    urgent_only = DesktopSink(name="desk", dry_run=True, min_severity="urgent")
    svc = NotifyService(Store(), [bad, good, urgent_only], clock=clock)

    rec = svc.send_alert(AlertRequest(topic_id="t", severity="warning", title="x"))
    assert rec.status == "sent"
    assert rec.deliveries["good"] == "ok" and "503" in rec.deliveries["bad"]
    assert "desk" not in rec.deliveries and urgent_only.sent == []
    assert calls == [rec.id]

    rec2 = svc.send_alert(AlertRequest(topic_id="t", severity="urgent", title="y"))
    assert rec2.deliveries["desk"] == "ok" and len(urgent_only.sent) == 1
    # the outcome is persisted, not only returned
    assert svc.get(rec.id).deliveries == rec.deliveries


def test_build_notifier_from_dict(tmp_path, topics):
    svc = build_notifier({"db": str(tmp_path / "n.sqlite"), "cooldown_sec": 10,
                          "sinks": [{"type": "desktop", "dry_run": True}]}, topics_root=topics)
    rec = svc.send_alert(AlertRequest(topic_id="porch", severity="info", title="hi",
                                      dedupe_key="k"))
    assert rec.deliveries == {"desktop": "ok"}
    assert svc.send_alert(rec.request).status == "deduped"
    assert svc.topic_dir_for("porch") == topics / "porch"
    assert svc.topic_dir_for("../porch") is None
    assert (tmp_path / "n.sqlite").exists()
