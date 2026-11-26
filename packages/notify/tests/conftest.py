from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vs_notify import DesktopSink, NotifyService, Store
from vs_notify.server import create_app
from vs_notify.service import topics_root_resolver

TOKEN = "test-household-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class RecordingSink:
    def __init__(self, name: str = "rec", fail: bool = False, min_severity: str = "info"):
        self.name = name
        self.fail = fail
        self.min_severity = min_severity
        self.got = []

    def send(self, alert) -> None:
        self.got.append(alert)
        if self.fail:
            raise RuntimeError("boom")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def topics(tmp_path):
    root = tmp_path / "topics"
    bundle = root / "porch" / "state" / "bundles" / "b-001"
    (bundle / "cam1").mkdir(parents=True)
    (bundle / "bundle.json").write_text('{"bundle_id": "b-001"}')
    (bundle / "cam1" / "contact_sheet.jpg").write_bytes(b"\xff\xd8fake")
    for f in bundle.rglob("*"):
        if f.is_file():
            f.chmod(0o444)  # the runtime makes bundles immutable
    return root


@pytest.fixture
def sink():
    return RecordingSink()


@pytest.fixture
def service(tmp_path, topics, sink, clock):
    return NotifyService(
        Store(tmp_path / "notify.sqlite"),
        [sink, DesktopSink(dry_run=True)],
        topic_dir_for=topics_root_resolver(topics),
        clock=clock,
    )


@pytest.fixture
def local(service):
    """A client connecting from loopback."""
    return TestClient(create_app(service, TOKEN), base_url="http://127.0.0.1:8790", client=("127.0.0.1", 50000))


@pytest.fixture
def remote(service):
    """A client connecting from another machine on the LAN."""
    return TestClient(create_app(service, TOKEN), client=("192.168.1.50", 50000))


@pytest.fixture
def alert_body():
    def make(**kw) -> dict:
        body = {"topic_id": "porch", "severity": "warning", "title": "Package left",
                "body": "At door", "bundle_id": "b-001", "rubric_version": "r1abc",
                "tool_versions": {"tools/motion.py": "t9"}}
        body.update(kw)
        return body

    return make
