"""Tests for Topic timed mute and AI-decided until-next-event mute."""

from __future__ import annotations

from datetime import timedelta

from vs_runtime.contracts import MuteState, OrchestratorAction
from vs_runtime.loop import TopicLoop


def test_timed_mute_suppresses_until_expiry(topic, hub, notifier, harness, t0):
    now = [t0]
    loop = TopicLoop(topic, harness, hub, notifier, clock=lambda: now[0])

    topic.set_mute(MuteState(mode="timed", muted_at=t0, muted_until=t0 + timedelta(minutes=15)))

    harness.reports.append("notable")
    harness.scripted_actions.append(
        [OrchestratorAction(name="send_notification", args={"title": "Move", "body": "x"})]
    )
    out1 = loop.tick(now[0])
    assert out1.actions[0]["result"]["suppressed"] == "muted_timed"
    assert len(notifier.sent) == 0
    assert harness.events[-1].mute is not None
    assert harness.events[-1].mute.mode == "timed"

    # Advance past expiry -> mute clears automatically and alerts resume
    now[0] = t0 + timedelta(minutes=16)
    harness.reports.append("notable")
    harness.scripted_actions.append(
        [OrchestratorAction(name="send_notification", args={"title": "Move 2", "body": "y"})]
    )
    out2 = loop.tick(now[0])
    assert out2.actions[0]["result"]["status"] == "sent"
    assert len(notifier.sent) == 1
    assert topic.load_mute(now[0]).mode == "none"


def test_until_next_event_mute_broken_only_by_agent_decision(topic, hub, notifier, harness, t0):
    loop = TopicLoop(topic, harness, hub, notifier, clock=lambda: t0)
    topic.set_mute(
        MuteState(
            mode="until_next_event",
            muted_at=t0,
            anchor_alert_id="a1",
            anchor_summary="Person in dark shirt moving",
        )
    )

    # Routine notification without breaks_mute is suppressed while mute stays active
    harness.reports.append("notable")
    harness.scripted_actions.append(
        [OrchestratorAction(name="send_notification", args={"title": "Still moving", "body": "same"})]
    )
    out1 = loop.tick(t0)
    assert out1.actions[0]["result"]["suppressed"] == "muted_until_next_event"
    assert len(notifier.sent) == 0
    assert topic.load_mute(t0).mode == "until_next_event"

    # Agent judges a genuinely new event began and passes breaks_mute=True -> unmutes & sends
    harness.reports.append("notable")
    harness.scripted_actions.append(
        [
            OrchestratorAction(
                name="send_notification",
                args={"title": "New visitor", "body": "Second person entered", "breaks_mute": True},
            )
        ]
    )
    out2 = loop.tick(t0 + timedelta(seconds=30))
    assert out2.actions[0]["result"]["status"] == "sent"
    assert out2.actions[0]["result"]["unmuted_by_next_event"] is True
    assert len(notifier.sent) == 1
    assert topic.load_mute(t0 + timedelta(seconds=30)).mode == "none"


def test_clear_mute_action_lifts_mute_without_alerting(topic, hub, notifier, harness, t0):
    loop = TopicLoop(topic, harness, hub, notifier, clock=lambda: t0)
    topic.set_mute(MuteState(mode="until_next_event", muted_at=t0, anchor_summary="Ongoing episode"))
    harness.reports.append("clear")
    harness.scripted_actions.append(
        [OrchestratorAction(name="clear_mute", args={"reason": "room empty again"})]
    )
    out = loop.tick(t0)
    assert out.actions[0]["ok"] is True
    assert topic.load_mute(t0).mode == "none"
    assert len(notifier.sent) == 0
