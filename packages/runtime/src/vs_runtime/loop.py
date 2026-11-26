"""The Topic loop: bundle -> Vision Sub-Agent -> Main Agent review, every tick by default
(ARCHITECTURE.md §4, §5.3, §5.6).

- Every report reaches the Main Agent. Under a Main-Agent-set quiet policy, consecutive
  all-clear reports are held and delivered together as one digest per interval; any other
  report flushes the digest and is delivered at once.
- A report the Main Agent could not review (turn failed: crash, timeout, quota) stays
  unreviewed in ticks.jsonl. Each later tick first replays that backlog in order, marked
  late, so an event during an outage is still reviewed. After a restart the backlog is
  rebuilt from ticks.jsonl, since the Topic directory is the state.
- The Main Agent acts only through returned actions, dispatched here; results it needs to
  see (inspect_media paths, tool test output, errors) go back as a follow-up turn.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from vs_runtime.bundle import BundleBuilder, prune_bundles, run_tools
from vs_runtime.contracts import (
    AgentHarness,
    AlertRequest,
    Notifier,
    ObservationBundle,
    OrchestratorAction,
    OrchestratorEvent,
    OrchestratorResult,
    ReportStatus,
    ReviewPolicy,
    StreamHubClient,
    TokenUsage,
    VisionReport,
    utcnow,
)
from vs_runtime.counters import TickLog, compute_counters, unreviewed_ticks
from vs_runtime.topic import Topic

log = logging.getLogger(__name__)

FOLLOWUP_ACTIONS = {"inspect_media", "test_topic_tool"}
ALERT_ARG_KEYS = {
    "severity", "title", "body", "clip_ref", "dedupe_key", "cooldown_sec", "requires_ack",
    "bundle_id", "camera_ids", "speaker", "breaks_mute",
}
_STATUS_RANK: dict[ReportStatus, int] = {"clear": 0, "error": 1, "notable": 2, "urgent": 3}


@dataclass
class TickOutcome:
    bundle: ObservationBundle
    report: VisionReport
    delivered: bool = False  # reached the Main Agent this tick (alone or in a digest)
    held: bool = False  # held for the next digest under the quiet policy
    vision_skipped: bool = False  # deterministic tool bypassed the Vision Sub-Agent
    late_replayed: int = 0  # backlog reports delivered late before this one
    actions: list[dict[str, Any]] = field(default_factory=list)


class TopicLoop:
    def __init__(
        self,
        topic: Topic,
        harness: AgentHarness,
        hub: StreamHubClient,
        notifier: Notifier,
        *,
        clock: Callable[[], datetime] = utcnow,
        max_followups: int = 2,
        late_batch: int = 20,
        speaker_cameras: set[str] | Callable[[], set[str]] | None = None,
    ):
        self.topic = topic
        self.harness = harness
        self.hub = hub
        self.notifier = notifier
        self.clock = clock
        self.max_followups = max_followups
        self.late_batch = late_batch
        self._speaker_cams = speaker_cameras
        self.log = TickLog(topic.ticks_path)
        self.builder = BundleBuilder(topic, hub, harness.media_capabilities())
        self.available = True  # did the last Main Agent turn succeed
        self._held: list[tuple[datetime, VisionReport]] = []
        self._pending_events: list[OrchestratorEvent] = []  # system events missed in an outage
        self._events: queue.Queue[OrchestratorEvent] = queue.Queue()
        self._turn = threading.Lock()  # one Main Agent turn at a time
        self._stop: threading.Event | None = None  # set by run(); checked between stages

    # --- one tick ---------------------------------------------------------------
    def tick(self, now: datetime | None = None) -> TickOutcome:
        now = now or self.clock()
        self.topic.reload()  # the agent or the user may have changed the config
        bundle = self.builder.build(now)
        det = self._deterministic_tool_report(bundle)
        if det is not None:
            report, triggered, wake_main = det
            vision_skipped = True
        else:
            report = self._run_vision(bundle)
            triggered = False
            wake_main = True
            vision_skipped = False
        stopping = self._stop is not None and self._stop.is_set()
        tick_rec: dict[str, Any] = {
            "type": "tick", "at": bundle.window_end.isoformat(), "bundle_id": bundle.bundle_id,
            "status": report.status, "conditions": report.conditions,
            "degraded": report.degraded, "usage": report.usage.model_dump(),
            "report": report.model_dump(mode="json", exclude={"counters", "late"}),
        }
        if vision_skipped:
            tick_rec["vision_skipped"] = True
        self.log.append(tick_rec)
        report.counters = compute_counters(self.log.records(), now)
        out = TickOutcome(bundle=bundle, report=report, vision_skipped=vision_skipped)
        if stopping:  # shutting down: leave the report unreviewed, replayed late on restart
            return out

        if vision_skipped and not wake_main:
            # Deterministic trigger cleared the tick without waking either agent; record a
            # zero-token tool_bypass review so the watchdog sees liveness and backlog stays empty.
            self.log.append({
                "type": "review", "kind": "tool_bypass", "late": False, "ok": True,
                "bundle_ids": [bundle.bundle_id], "error": "",
                "usage": TokenUsage().model_dump(), "actions": [],
            })
            prune_bundles(self.topic, now)
            return out

        held_ids = {r.bundle_id for _, r in self._held} | {report.bundle_id}
        replayed = self._replay_backlog(held_ids, report)
        if replayed is None:  # Main Agent still unavailable: this report joins the backlog
            return out
        out.late_replayed = replayed

        policy = self.topic.config.review_policy
        if not triggered and policy.is_all_clear(report):
            self._held.append((bundle.window_end, report))
            if (now - self._held[0][0]).total_seconds() >= policy.digest_interval_sec:
                out.delivered = self._flush_digest(report)
            out.held = any(r is report for _, r in self._held)
        else:
            if self._held and not self._flush_digest(report):
                return out
            msg = (
                "Woken by deterministic trigger tool(s); Vision Sub-Agent was skipped."
                if vision_skipped else ""
            )
            out.delivered, out.actions = self._deliver(OrchestratorEvent(
                topic_id=self.topic.id, kind="report", reports=[report],
                counters=report.counters, message=msg,
            ))
        prune_bundles(self.topic, now)
        return out

    def _deterministic_tool_report(
        self, bundle: ObservationBundle
    ) -> tuple[VisionReport, bool, bool] | None:
        """If any tool emitted ``{"skip_vision": true, ...}``, synthesize a zero-token
        VisionReport directly from tool outputs and return ``(report, triggered, wake_main)``.
        Returns ``None`` when the Vision Sub-Agent should run normally."""
        trigger_tools = [
            (name, val)
            for name, val in bundle.tool_outputs.items()
            if isinstance(val, dict) and val.get("skip_vision") is True
        ]
        if not trigger_tools:
            return None
        triggered = any(bool(v.get("trigger")) for _, v in trigger_tools)
        explicit_wake = any(bool(v.get("wake_main")) for _, v in trigger_tools)
        wake_main = triggered or explicit_wake or bundle.degraded

        status: ReportStatus = "notable" if triggered else "clear"
        for _, v in trigger_tools:
            cand = v.get("status")
            if cand in _STATUS_RANK and _STATUS_RANK[cand] > _STATUS_RANK[status]:
                status = cand

        summaries = [
            str(v["summary"]).strip()
            for _, v in trigger_tools
            if isinstance(v.get("summary"), str) and v["summary"].strip()
        ]
        summary = "; ".join(summaries) or (
            "Deterministic trigger fired." if triggered else "Deterministic trigger: all clear."
        )
        observations: list[str] = []
        conditions: list[str] = []
        seen_conds: set[str] = set()
        for name, v in trigger_tools:
            obs = v.get("observations")
            if isinstance(obs, list):
                observations.extend(f"[{name}] {item}" for item in obs if str(item).strip())
            conds = v.get("conditions")
            if isinstance(conds, list):
                for c in conds:
                    cs = str(c).strip()
                    if cs and cs not in seen_conds:
                        seen_conds.add(cs)
                        conditions.append(cs)
        if bundle.notes:
            observations.extend(f"[runtime] {n}" for n in bundle.notes)

        report = VisionReport(
            bundle_id=bundle.bundle_id,
            topic_id=self.topic.id,
            status=status,
            summary=summary,
            observations=observations,
            conditions=conditions,
            suggest_review=wake_main,
            camera_status=dict(bundle.camera_status),
            degraded=bundle.degraded,
            raw={
                "deterministic_trigger": True,
                "triggered": triggered,
                "tool_outputs": bundle.tool_outputs,
            },
            usage=TokenUsage(),
        )
        return report, triggered, wake_main

    def _run_vision(self, bundle: ObservationBundle) -> VisionReport:
        try:
            report = self.harness.run_vision_worker(
                bundle, self.topic.rubric(), self.topic.config.vision_model
            )
        except Exception as e:
            log.exception("vision worker failed for %s", bundle.bundle_id)
            report = VisionReport(
                bundle_id=bundle.bundle_id, topic_id=self.topic.id, status="error",
                summary=f"vision worker failed: {type(e).__name__}: {e}", degraded=True,
            )
        report.camera_status = report.camera_status or dict(bundle.camera_status)
        report.degraded = report.degraded or bundle.degraded
        if bundle.notes:
            report.observations = report.observations + [f"[runtime] {n}" for n in bundle.notes]
        return report

    def _flush_digest(self, latest: VisionReport) -> bool:
        reports = [r for _, r in self._held]
        ok, _ = self._deliver(OrchestratorEvent(
            topic_id=self.topic.id, kind="digest", reports=reports, counters=latest.counters,
            message=f"{len(reports)} all-clear report(s) batched under your review policy.",
        ))
        if ok:
            self._held.clear()
        return ok

    def _replay_backlog(self, exclude: set[str], current: VisionReport) -> int | None:
        """Deliver unreviewed reports from earlier ticks, oldest first, marked late.
        Returns how many were delivered, or None if the Main Agent is still unavailable."""
        for ev in list(self._pending_events):
            ok, _ = self._deliver(ev.model_copy(update={"late": True}))
            if not ok:
                return None
            self._pending_events.remove(ev)
        backlog = [r for r in unreviewed_ticks(self.log.records()) if r["bundle_id"] not in exclude]
        for i in range(0, len(backlog), self.late_batch):
            chunk = [
                VisionReport.model_validate(r["report"] | {"late": True})
                for r in backlog[i : i + self.late_batch]
            ]
            ok, _ = self._deliver(OrchestratorEvent(
                topic_id=self.topic.id, kind="report" if len(chunk) == 1 else "digest",
                reports=chunk, counters=current.counters, late=True,
                message=f"{len(chunk)} report(s) from while the Main Agent was unavailable, "
                        "delivered late in their original order.",
            ))
            if not ok:
                return None
        return len(backlog)

    # --- Main Agent turns ----------------------------------------------------------
    def _deliver(self, event: OrchestratorEvent, depth: int = 0) -> tuple[bool, list[dict]]:
        now = self.clock()
        mute = self.topic.load_mute(now)
        if event.mute is None and mute.is_active(now):
            event = event.model_copy(update={"mute": mute})
        with self._turn:
            try:
                result = self.harness.deliver_to_orchestrator(self.topic.context(), event)
            except Exception as e:
                result = OrchestratorResult(ok=False, error=f"{type(e).__name__}: {e}")
            self.log.append({
                "type": "review", "kind": event.kind, "late": event.late, "ok": result.ok,
                "bundle_ids": [r.bundle_id for r in event.reports], "error": result.error,
                "usage": result.usage.model_dump(), "actions": [a.name for a in result.actions],
            })
        self.available = result.ok
        if not result.ok:
            log.warning("Main Agent turn failed (%s): %s", event.kind, result.error)
            self._held.clear()  # held reports are unreviewed in ticks.jsonl: replayed in order
            return False, []
        results = self.dispatch(result.actions, event)
        followup = [r for r in results if r["name"] in FOLLOWUP_ACTIONS or not r["ok"]]
        if followup and depth < self.max_followups:
            self._deliver(OrchestratorEvent(
                topic_id=self.topic.id, kind="system",
                message="Results of your actions:\n" + json.dumps(followup, default=str),
            ), depth + 1)
        return True, results

    def chat_turn(
        self, message: str, attachments: list[str] | None = None
    ) -> tuple[OrchestratorResult, list[dict[str, Any]]]:
        """Run one user chat turn, dispatch any returned actions, and return both."""
        self.topic.reload()
        with self._turn:
            result = self.harness.chat_with_orchestrator(self.topic.context(), message, attachments)
        actions = self.dispatch(result.actions, None) if result.ok else []
        return result, actions

    def chat(self, message: str, attachments: list[str] | None = None) -> OrchestratorResult:
        result, _ = self.chat_turn(message, attachments)
        return result

    def post_event(self, event: OrchestratorEvent) -> None:
        """Thread-safe: queue a system event (e.g. a camera drop from the watchdog)."""
        self._events.put(event)

    def drain_events(self) -> None:
        while True:
            try:
                ev = self._events.get_nowait()
            except queue.Empty:
                return
            self._deliver_event(ev)

    def _deliver_event(self, ev: OrchestratorEvent) -> None:
        """A system event (camera drop, feedback); kept for late replay if it fails."""
        ok, _ = self._deliver(ev)
        if not ok:
            self._pending_events.append(ev)

    # --- action dispatcher ------------------------------------------------------
    def dispatch(
        self, actions: list[OrchestratorAction], event: OrchestratorEvent | None
    ) -> list[dict[str, Any]]:
        results = []
        for a in actions:
            try:
                res = {"name": a.name, "ok": True, "result": self._act(a, event)}
            except Exception as e:
                res = {"name": a.name, "ok": False, "error": f"{type(e).__name__}: {e}"}
            self.log.append({"type": "action", **res, "args": _brief(a.args)})
            results.append(res)
        return results

    def _act(self, a: OrchestratorAction, event: OrchestratorEvent | None) -> Any:
        topic, args = self.topic, a.args
        match a.name:
            case "inspect_media":
                bdir = topic.bundle_dir(args["bundle_id"])
                if args.get("asset"):
                    rel = args["asset"]
                    if args.get("camera_id") and not rel.startswith(f"{args['camera_id']}/"):
                        rel = f"{args['camera_id']}/{rel}"
                    p = (bdir / rel).resolve()
                    if not p.is_relative_to(bdir) or not p.is_file():
                        raise FileNotFoundError(f"no asset {rel!r} in bundle {bdir.name}")
                    return {"paths": [str(p)]}
                root = bdir / args["camera_id"] if args.get("camera_id") else bdir
                return {"paths": sorted(str(p) for p in root.rglob("*") if p.is_file())}
            case "send_notification":
                return self._send_notification(args, event)
            case "update_vision_rubric":
                return {"rubric_version": topic.write_rubric(args["content"])}
            case "update_schedule":
                changes = {k: int(args[k]) for k in ("cadence_sec", "window_sec") if args.get(k)}
                cfg = topic.update_config(**changes)
                return {"cadence_sec": cfg.cadence_sec, "window_sec": cfg.window_sec}
            case "update_review_policy":
                interval = args.get("digest_interval_sec")
                policy = ReviewPolicy(
                    all_clear_definition=args.get("all_clear_definition", ""),
                    all_clear_statuses=args.get("all_clear_statuses") or ["clear"],
                    digest_interval_sec=None if interval in (None, "none", 0) else int(interval),
                )
                topic.update_config(review_policy=policy.model_dump())
                return policy.model_dump()
            case "edit_topic_tool":
                p = topic.write_tool(args["path"], args["code"])
                return {"path": str(p), "version": topic.tool_versions().get(f"tools/{p.name}")}
            case "test_topic_tool":
                bdir = topic.bundle_dir(args["bundle_id"])
                bundle = ObservationBundle.model_validate_json((bdir / "bundle.json").read_text())
                return run_tools(topic, bdir, bundle, only=args.get("path"))
            case "spawn_subagent":
                return {"note": "coding sub-agents are spawned by the harness itself"}
            case "clear_mute":
                self.topic.clear_mute()
                return {"muted": False, "reason": str(args.get("reason", ""))}
        raise ValueError(f"unknown action {a.name}")

    def _speaker_cameras(self) -> set[str]:
        if callable(self._speaker_cams):
            return set(self._speaker_cams())
        if self._speaker_cams is not None:
            return set(self._speaker_cams)
        adapters = getattr(self.hub, "adapters", None)
        if isinstance(adapters, dict):
            return {
                cid
                for cid, ad in adapters.items()
                if getattr(getattr(ad, "config", None), "speaker_enabled", False)
            }
        return set()

    def _send_notification(self, args: dict[str, Any], event: OrchestratorEvent | None):
        unknown = set(args) - ALERT_ARG_KEYS
        if unknown:
            raise ValueError(f"unknown send_notification args: {sorted(unknown)}")
        raw = dict(args)
        breaks_mute = bool(raw.pop("breaks_mute", False))
        now = self.clock()
        mute = self.topic.load_mute(now)
        unmuted_by_next_event = False
        if mute.is_active(now):
            if mute.mode == "timed":
                return {
                    "status": "suppressed",
                    "suppressed": "muted_timed",
                    "muted_until": mute.muted_until.isoformat() if mute.muted_until else None,
                }
            if mute.mode == "until_next_event":
                if breaks_mute:
                    self.topic.clear_mute()
                    unmuted_by_next_event = True
                else:
                    return {
                        "status": "suppressed",
                        "suppressed": "muted_until_next_event",
                        "anchor_summary": mute.anchor_summary,
                    }
        latest = event.reports[-1] if event and event.reports else None
        data = self.topic.config.alert_defaults | raw
        data.setdefault("severity", "warning")
        data.setdefault("camera_ids", list(self.topic.config.camera_ids))
        if latest:
            data.setdefault("bundle_id", latest.bundle_id)
        speaker_suppressed: str | None = None
        if data.get("speaker"):
            if not self.topic.config.speaker_enabled:
                data["speaker"] = False
                speaker_suppressed = "speaker disabled for topic (speaker_enabled=false)"
            elif not (set(data["camera_ids"]) & self._speaker_cameras()):
                data["speaker"] = False
                speaker_suppressed = "speaker disabled for target camera(s) (speaker_enabled=false)"
        req = AlertRequest(
            topic_id=self.topic.id, source="main_agent",
            rubric_version=self.topic.rubric_version(), tool_versions=self.topic.tool_versions(),
            **data,
        )
        record = self.notifier.send_alert(req)
        self.log.append({
            "type": "alert", "alert_id": record.id, "dedupe_key": req.dedupe_key,
            "severity": req.severity, "status": record.status, "bundle_id": req.bundle_id,
            "title": req.title, "speaker": req.speaker,
        })
        out: dict[str, Any] = {
            "alert_id": record.id, "status": record.status,
            "deliveries": record.deliveries, "speaker": req.speaker,
        }
        if speaker_suppressed:
            out["speaker_suppressed"] = speaker_suppressed
        if unmuted_by_next_event:
            out["unmuted_by_next_event"] = True
        return out

    # --- continuous run ------------------------------------------------------------
    def run(self, stop: threading.Event) -> None:
        self._stop = stop
        next_at = self.clock()
        while not stop.is_set():
            now = self.clock()
            if now >= next_at:
                try:
                    self.topic.reload()
                except Exception:
                    log.exception("config reload failed for topic %s", self.topic.id)
                if self.topic.config.paused:
                    next_at = now + timedelta(seconds=1)
                else:
                    try:
                        self.tick(now)
                    except Exception:
                        log.exception("tick failed for topic %s", self.topic.id)
                    next_at = now + timedelta(seconds=self.topic.config.cadence_sec)
            wait = max(0.0, min(1.0, (next_at - self.clock()).total_seconds()))
            try:
                ev = self._events.get(timeout=wait)
            except queue.Empty:
                continue
            try:
                self._deliver_event(ev)
            except Exception:
                log.exception("event delivery failed for topic %s", self.topic.id)


def _brief(args: dict[str, Any]) -> dict[str, Any]:
    return {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in args.items()}
