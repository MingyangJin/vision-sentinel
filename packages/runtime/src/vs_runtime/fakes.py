"""In-memory fakes for tests and for a zero-account demo (``harness = "fake"``).

FakeHarness never calls a model: its Vision Sub-Agent returns scripted reports and its
Main Agent returns scripted actions (by default: notify on any urgent report).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from vs_runtime.contracts import (
    AlertRecord,
    AlertRequest,
    AssetKind,
    MediaCapabilities,
    ModelConfig,
    ObservationAsset,
    ObservationBundle,
    OrchestratorAction,
    OrchestratorEvent,
    OrchestratorResult,
    StreamHealth,
    TokenUsage,
    TopicContext,
    VisionReport,
)


class FakeHub:
    """StreamHubClient writing tiny placeholder assets; set ``status[cam]`` to simulate drops."""

    def __init__(self, default: str = "ok"):
        self.default = default
        self.status: dict[str, str] = {}
        self.calls: list[tuple[str, datetime, datetime]] = []

    def health(self, camera_id: str) -> StreamHealth:
        return StreamHealth(camera_id=camera_id, status=self.status.get(camera_id, self.default))

    def extract_window(self, camera_id: str, start: datetime, end: datetime, out_dir: Path,
                       kinds: set[AssetKind]) -> list[ObservationAsset]:
        self.calls.append((camera_id, start, end))
        out_dir.mkdir(parents=True, exist_ok=True)
        status = self.health(camera_id).status
        (out_dir / "meta.json").write_text(json.dumps({"status": status}))
        if status == "offline":
            return []
        assets = []
        for kind, name in (("video", "clip.mp4"), ("contact_sheet", "contact_sheet.jpg"),
                           ("audio", "audio.mp3"), ("telemetry", "meta.json")):
            if kind in kinds:
                if kind != "telemetry":
                    (out_dir / name).write_bytes(b"fake")
                assets.append(ObservationAsset(camera_id=camera_id, kind=kind, path=name,
                                               start=start, end=end))
        return assets


class FakeHarness:
    def __init__(self, reports: list[str | dict[str, Any]] | None = None, auto_alert: bool = True):
        self.reports = list(reports or [])  # each a status or VisionReport fields; then "clear"
        self.auto_alert = auto_alert
        self.available = True
        self.scripted_actions: list[list[OrchestratorAction]] = []
        self.events: list[OrchestratorEvent] = []
        self.chats: list[str] = []
        self.bundles: list[ObservationBundle] = []

    def media_capabilities(self) -> MediaCapabilities:
        return MediaCapabilities(frames=True, video=False, audio=True)

    def run_vision_worker(self, bundle: ObservationBundle, rubric: str,
                          model: ModelConfig) -> VisionReport:
        self.bundles.append(bundle)
        spec = self.reports.pop(0) if self.reports else "clear"
        spec = {"status": spec} if isinstance(spec, str) else spec
        return VisionReport.model_validate({
            "bundle_id": bundle.bundle_id, "topic_id": bundle.topic_id,
            "summary": f"fake worker: {spec['status']}",
            "assets_read": [a.path for a in bundle.assets],
            "usage": TokenUsage(input=100, output=10), **spec,
        })

    def deliver_to_orchestrator(self, topic: TopicContext,
                                event: OrchestratorEvent) -> OrchestratorResult:
        self.events.append(event)
        if not self.available:
            return OrchestratorResult(ok=False, error="main agent unavailable (fake)")
        if self.scripted_actions:
            actions = self.scripted_actions.pop(0)
        elif self.auto_alert and any(r.status == "urgent" for r in event.reports):
            r = next(r for r in event.reports if r.status == "urgent")
            actions = [OrchestratorAction(name="send_notification", args={
                "severity": "urgent", "title": f"{topic.topic_id}: {r.summary}",
                "body": "; ".join(r.observations), "bundle_id": r.bundle_id,
                "dedupe_key": "urgent",
            })]
        else:
            actions = []
        return OrchestratorResult(ok=True, actions=actions, reply="noted",
                                  usage=TokenUsage(input=200, output=20))

    def chat_with_orchestrator(self, topic: TopicContext, message: str,
                               attachments: list[str] | None = None) -> OrchestratorResult:
        self.chats.append(message)
        actions = self.scripted_actions.pop(0) if self.scripted_actions else []
        return OrchestratorResult(ok=self.available, actions=actions, reply=f"fake: {message}")


class FakeNotifier:
    def __init__(self, fail: bool = False):
        self.sent: list[AlertRequest] = []
        self.fail = fail

    def send_alert(self, request: AlertRequest) -> AlertRecord:
        if self.fail:
            raise ConnectionError("notifier down (fake)")
        self.sent.append(request)
        return AlertRecord(id=f"alert-{len(self.sent)}", request=request,
                           deliveries={"fake": "ok"})
