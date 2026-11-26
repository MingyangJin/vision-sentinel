"""Contracts shared by every Vision Sentinel package.

Data crosses package boundaries as these Pydantic models; components meet through
these Protocols. Nothing here knows a camera address or a credential: cameras are
logical ``camera_id``s, media are files on disk (ARCHITECTURE.md §5).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

CameraStatus = Literal["ok", "degraded", "offline"]
AssetKind = Literal["video", "frame", "contact_sheet", "audio", "telemetry"]
Severity = Literal["info", "warning", "urgent"]
ReportStatus = Literal["clear", "notable", "urgent", "error"]
EventKind = Literal["report", "digest", "camera_drop", "camera_restored", "feedback", "system"]
MuteMode = Literal["none", "timed", "until_next_event"]
ActionName = Literal[
    "inspect_media",
    "send_notification",
    "update_vision_rubric",
    "update_schedule",
    "update_review_policy",
    "edit_topic_tool",
    "test_topic_tool",
    "spawn_subagent",
    "clear_mute",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Layer 1: cameras and the Stream Hub (§5.1) ---------------------------------


class CameraConfig(_Model):
    """One camera as the Stream Hub sees it. Only adapters resolve ``credential_ref``."""

    camera_id: str
    adapter: str  # "file", "rtsp", "onvif", "usb", "rtmp", ...
    source: str = ""  # file path, or URL template WITHOUT credentials
    credential_ref: str | None = None  # name in OS secret storage, never a value
    options: dict[str, Any] = Field(default_factory=dict)
    speaker_enabled: bool = False  # one-way play_alert opt-in, off by default
    volume_cap: float = Field(default=0.3, ge=0.0, le=1.0)


class StreamHealth(_Model):
    camera_id: str
    status: CameraStatus
    checked_at: datetime = Field(default_factory=utcnow)
    last_segment_at: datetime | None = None
    detail: str = ""  # human-readable, already redacted


@runtime_checkable
class CameraAdapter(Protocol):
    """Lifecycle every adapter implements. May also define
    ``play_alert(audio: Path, volume: float) -> None`` (opt-in, volume-capped)."""

    camera_id: str

    def probe(self) -> StreamHealth: ...
    def start(self) -> None: ...
    def health(self) -> StreamHealth: ...
    def stop(self) -> None: ...


@runtime_checkable
class StreamHubClient(Protocol):
    """What the runtime and watchdog need from the Stream Hub, by camera_id only."""

    def health(self, camera_id: str) -> StreamHealth: ...

    def extract_window(
        self,
        camera_id: str,
        start: datetime,
        end: datetime,
        out_dir: Path,
        kinds: set[AssetKind],
    ) -> list[ObservationAsset]:
        """Write the requested assets for [start, end) into ``out_dir`` and return
        them with paths relative to ``out_dir``. Missing media yields fewer assets,
        never an exception for an offline camera."""
        ...


# --- Layer 2: bundles, reports, the harness (§5.2, §5.3) ------------------------


class ObservationAsset(_Model):
    camera_id: str
    kind: AssetKind
    path: str  # relative to the bundle directory
    start: datetime | None = None
    end: datetime | None = None
    mime: str = ""


class ObservationBundle(_Model):
    """Immutable window for one Vision Sub-Agent pass, across M cameras."""

    bundle_id: str
    topic_id: str
    camera_ids: list[str]
    camera_status: dict[str, CameraStatus] = Field(default_factory=dict)
    window_start: datetime
    window_end: datetime
    created_at: datetime = Field(default_factory=utcnow)
    path: str  # absolute bundle directory
    assets: list[ObservationAsset] = Field(default_factory=list)
    tool_outputs: dict[str, Any] = Field(default_factory=dict)  # tool name -> parsed stdout
    degraded: bool = False
    notes: list[str] = Field(default_factory=list)  # why degraded, etc.


class MediaCapabilities(_Model):
    """Which bundle assets a harness's Vision Sub-Agent can actually ingest."""

    frames: bool = True
    video: bool = False
    audio: bool = False

    def allows(self, kind: AssetKind) -> bool:
        return {
            "frame": self.frames,
            "contact_sheet": self.frames,
            "video": self.video,
            "audio": self.audio,
            "telemetry": True,
        }[kind]


class ModelConfig(_Model):
    model: str
    effort: str | None = None


class TokenUsage(_Model):
    input: int = 0
    cached: int = 0
    output: int = 0
    thinking: int = 0
    duration_s: float = 0.0


class ConditionStreak(_Model):
    count: int  # consecutive ticks the condition (or status) has held, including the latest
    since: datetime  # tick time the streak started
    duration_s: float  # latest tick time minus since


class RuntimeCounters(_Model):
    """Deterministic figures from state/ticks.jsonl attached to every report (§5.3)."""

    computed_at: datetime = Field(default_factory=utcnow)
    ticks_total: int = 0
    status: str | None = None
    status_streak: ConditionStreak | None = None
    conditions: dict[str, ConditionStreak] = Field(default_factory=dict)
    seconds_since_last_alert: dict[str, float] = Field(default_factory=dict)  # dedupe_key -> s
    last_reviewed_at: datetime | None = None


class VisionReport(_Model):
    bundle_id: str
    topic_id: str
    status: ReportStatus
    summary: str = ""
    observations: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)  # rubric-defined labels active now
    confidence: float | None = None
    suggest_review: bool = False
    assets_read: list[str] = Field(default_factory=list)  # bundle-relative paths
    camera_status: dict[str, CameraStatus] = Field(default_factory=dict)
    degraded: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)  # full schema output from the worker
    usage: TokenUsage = Field(default_factory=TokenUsage)
    created_at: datetime = Field(default_factory=utcnow)
    late: bool = False  # set on replay after a Main Agent outage
    counters: RuntimeCounters | None = None


class ReviewPolicy(_Model):
    """Main-Agent-set quiet policy; digest_interval_sec None means every-tick review."""

    all_clear_definition: str = ""  # the agent's own words, for the record
    all_clear_statuses: list[str] = Field(default_factory=lambda: ["clear"])
    digest_interval_sec: int | None = None

    def is_all_clear(self, report: VisionReport) -> bool:
        return (
            self.digest_interval_sec is not None
            and report.status in self.all_clear_statuses
            and not report.suggest_review
            and not report.degraded
        )


class MuteState(_Model):
    """Per-Topic alert mute state stored in state/mute.json (survives restarts)."""

    mode: MuteMode = "none"
    muted_at: datetime | None = None
    muted_until: datetime | None = None
    anchor_alert_id: str | None = None
    anchor_summary: str = ""
    reason: str = ""

    def is_active(self, now: datetime | None = None) -> bool:
        if self.mode == "none":
            return False
        if self.mode == "timed":
            t = now or utcnow()
            return self.muted_until is not None and self.muted_until > t
        return True


class OrchestratorEvent(_Model):
    """One turn delivered to a Topic's Main Agent."""

    topic_id: str
    kind: EventKind
    reports: list[VisionReport] = Field(default_factory=list)  # 1 for report, N for digest
    counters: RuntimeCounters | None = None
    mute: MuteState | None = None
    message: str = ""  # system text for camera_drop / feedback / system events
    late: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class OrchestratorAction(_Model):
    name: ActionName
    args: dict[str, Any] = Field(default_factory=dict)


class OrchestratorResult(_Model):
    ok: bool = True
    actions: list[OrchestratorAction] = Field(default_factory=list)
    reply: str = ""
    usage: TokenUsage = Field(default_factory=TokenUsage)
    error: str = ""  # set with ok=False when the turn failed (timeout, quota, crash)


class TopicContext(_Model):
    """What a harness needs to open or resume a Topic's Main Agent session.
    The Topic directory is the state; the session is disposable (§5.2)."""

    topic_id: str
    topic_dir: str
    goal: str  # goal.md, pinned, read-only to every agent
    vision_rubric: str
    orchestrator_notes: str
    main_model: ModelConfig
    vision_model: ModelConfig


@runtime_checkable
class AgentHarness(Protocol):
    """Thin adapter over an official coding-agent CLI (§5.3)."""

    def media_capabilities(self) -> MediaCapabilities: ...

    def run_vision_worker(
        self, bundle: ObservationBundle, rubric: str, model: ModelConfig
    ) -> VisionReport:
        """Stateless: fresh worker, read-only inside bundle.path, then discarded."""
        ...

    def deliver_to_orchestrator(
        self, topic: TopicContext, event: OrchestratorEvent
    ) -> OrchestratorResult:
        """Stateful: one turn in the Topic's persistent Main Agent session."""
        ...

    def chat_with_orchestrator(
        self, topic: TopicContext, message: str, attachments: list[str] | None = None
    ) -> OrchestratorResult: ...


# --- Layer 3: notifications (§5.4) ----------------------------------------------


class AlertRequest(_Model):
    topic_id: str
    severity: Severity
    title: str
    body: str = ""
    clip_ref: str | None = None  # bundle-relative or saved-clip path
    dedupe_key: str | None = None
    cooldown_sec: float | None = None  # None = service default; dedupe vs last sent alert
    requires_ack: bool = False
    bundle_id: str | None = None
    camera_ids: list[str] = Field(default_factory=list)
    source: Literal["main_agent", "watchdog"] = "main_agent"
    rubric_version: str | None = None  # sha256 prefix of vision_rubric.md
    tool_versions: dict[str, str] = Field(default_factory=dict)  # tool path -> sha256 prefix
    speaker: bool = False  # request camera speaker sink (still needs camera opt-in)


class FeedbackRequest(_Model):
    alert_id: str
    rating: Literal["up", "down"]
    note: str = ""
    by: str = ""
    at: datetime = Field(default_factory=utcnow)


class AlertRecord(_Model):
    id: str
    request: AlertRequest
    created_at: datetime = Field(default_factory=utcnow)
    status: Literal["sent", "deduped", "failed"] = "sent"
    deliveries: dict[str, str] = Field(default_factory=dict)  # sink name -> "ok" | error
    acked_at: datetime | None = None
    acked_by: str | None = None
    feedback: list[FeedbackRequest] = Field(default_factory=list)


@runtime_checkable
class NotificationSink(Protocol):
    name: str

    def send(self, alert: AlertRecord) -> None:
        """Deliver one alert; raise on failure (the service records it)."""
        ...


@runtime_checkable
class Notifier(Protocol):
    """What the runtime and watchdog call: the Notification Service (in-process or over
    loopback ``POST /v1/alerts``). Applies dedupe/cooldown and fans out to sinks."""

    def send_alert(self, request: AlertRequest) -> AlertRecord: ...
