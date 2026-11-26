"""``AntigravityHarness``: the ``AgentHarness`` driver for the official ``agy`` CLI.

Vendor terms checked 2026-11-25 (ARCHITECTURE §5.3): every model request goes
through the official ``agy`` binary, which stays the only OAuth client; this driver
only launches it. It never reads, copies, replays or proxies agy's credentials.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vs_runtime.contracts import (
    MediaCapabilities,
    ModelConfig,
    ObservationBundle,
    OrchestratorEvent,
    OrchestratorResult,
    TopicContext,
    VisionReport,
)

from ._agy import ProcessRegistry, child_env, find_agy
from .orchestrator import Orchestrator, format_chat, format_event, reset_session, session_info
from .vision import run_vision_worker

# Verified 2026-11-25 on agy 1.5.17 inside the bundle cwd: JPEG frames and MP3 audio
# are read; video files are not ingested.
AGY_MEDIA = MediaCapabilities(frames=True, video=False, audio=True)


class AntigravityHarness:
    def __init__(
        self,
        binary: str | None = None,
        vision_timeout_s: float = 120.0,
        main_timeout_s: float = 300.0,
        main_full_permissions: bool = True,
        extra_env: dict[str, str] | None = None,
    ):
        self.binary = find_agy(binary)
        self.vision_timeout_s = vision_timeout_s
        self.env = child_env(extra_env)
        self.registry = ProcessRegistry()
        self.orchestrator = Orchestrator(
            self.binary, timeout_s=main_timeout_s, env=self.env,
            full_permissions=main_full_permissions, registry=self.registry,
        )

    def close(self) -> None:
        """End every running agy process (SIGTERM, then SIGKILL) and refuse new ones.
        Interrupted calls return an error report/result. Idempotent, thread-safe."""
        self.registry.close()

    def media_capabilities(self) -> MediaCapabilities:
        return AGY_MEDIA.model_copy()

    def run_vision_worker(
        self, bundle: ObservationBundle, rubric: str, model: ModelConfig
    ) -> VisionReport:
        return run_vision_worker(
            self.binary, bundle, rubric, model, AGY_MEDIA,
            timeout_s=self.vision_timeout_s, env=self.env, registry=self.registry,
        )

    def deliver_to_orchestrator(
        self, topic: TopicContext, event: OrchestratorEvent
    ) -> OrchestratorResult:
        return self.orchestrator.turn(topic, format_event(event))

    def chat_with_orchestrator(
        self, topic: TopicContext, message: str, attachments: list[str] | None = None
    ) -> OrchestratorResult:
        return self.orchestrator.turn(
            topic, format_chat(message, attachments, topic_dir=topic.topic_dir)
        )

    def session_info(self, topic_dir: str | Path) -> dict[str, Any]:
        """Inspect persisted Main Agent session metadata (id, turns, cumulative tokens)."""
        return session_info(topic_dir)

    def reset_session(self, topic_dir: str | Path, reason: str = "manual reset") -> bool:
        """Clear the active session id so the next turn starts fresh from the Topic dir."""
        return reset_session(topic_dir, reason=reason)
