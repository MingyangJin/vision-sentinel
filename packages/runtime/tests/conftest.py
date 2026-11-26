from datetime import datetime, timedelta, timezone

import pytest

from vs_runtime.fakes import FakeHarness, FakeHub, FakeNotifier
from vs_runtime.loop import TopicLoop
from vs_runtime.topic import init_topic


@pytest.fixture
def topic(tmp_path):
    return init_topic(tmp_path / "topics" / "porch", ["cam1"], "Tell me when a parcel arrives.",
                      cadence_sec=60, window_sec=10)


@pytest.fixture
def hub():
    return FakeHub()


@pytest.fixture
def harness():
    return FakeHarness()


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def loop(topic, harness, hub, notifier):
    return TopicLoop(topic, harness, hub, notifier)


@pytest.fixture
def t0():
    return datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def at(t0):
    """at(s): the fixed test start time plus s seconds."""
    return lambda seconds: t0 + timedelta(seconds=seconds)
