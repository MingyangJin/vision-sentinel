"""Bridges between the Web UI HTTP layer and ``vs_runtime`` / ``vs_notify``.

Works identically whether backed by ``vs_runtime.manager.TopicManager`` + in-process
``NotifyService`` (from ``vision-sentinel ui``) or standalone ``Topic`` dirs / loopback
``NotifyClient`` (in unit tests or split-process deployments).
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from vs_runtime.contracts import (
    AgentHarness,
    AlertRecord,
    ModelConfig,
    MuteState,
    ReviewPolicy,
    utcnow,
)
from vs_runtime.counters import TickLog, compute_counters
from vs_runtime.topic import GOAL, NOTES, Topic, TopicConfig, init_topic

from vs_ui.media import is_safe_id, resolve_bundle_asset
from vs_ui.presets import get_preset, list_presets

log = logging.getLogger("vs_ui.bridge")


class TopicNotFound(KeyError):
    pass


class AlertNotFound(KeyError):
    pass


class TopicBridge:
    """Unified CRUD, inspection, and chat adapter over ``TopicManager`` or raw ``Topic`` dirs."""

    def __init__(
        self,
        *,
        manager: Any = None,
        topics_root: Path | None = None,
        topics: list[Topic] | None = None,
        harness: AgentHarness | None = None,
        hub: Any = None,
        notifier: Any = None,
        loops: dict[str, Any] | None = None,
    ):
        self.manager = manager
        self.topics_root = Path(topics_root).resolve() if topics_root else None
        self._explicit: dict[str, Topic] = {}
        for t in topics or []:
            self._explicit[t.id] = t
        self.harness = harness
        self.hub = hub
        self.notifier = notifier
        self.loops: dict[str, Any] = dict(loops or {})

    # --- discovery ---------------------------------------------------------------
    def _all_topics(self) -> list[Topic]:
        found: dict[str, Topic] = {}
        if self.manager is not None and hasattr(self.manager, "list_topics"):
            for item in self.manager.list_topics():
                tid = item["id"] if isinstance(item, dict) else item.id
                try:
                    found[tid] = self.manager.get_topic(tid)
                except Exception:
                    if isinstance(item, dict) and item.get("dir"):
                        found[tid] = Topic(Path(item["dir"]))
        for tid, t in self._explicit.items():
            if tid not in found and (t.dir / "topic.toml").is_file():
                t.reload()
                found[tid] = t
        if self.topics_root and self.topics_root.is_dir():
            for child in sorted(self.topics_root.iterdir()):
                if child.is_dir() and (child / "topic.toml").is_file():
                    try:
                        t = Topic(child)
                        found.setdefault(t.id, t)
                    except Exception:
                        log.warning("skipping invalid topic dir %s", child)
        return sorted(found.values(), key=lambda t: t.id)

    def get_topic(self, topic_id: str) -> Topic:
        if not is_safe_id(topic_id):
            raise TopicNotFound(topic_id)
        if self.manager is not None and hasattr(self.manager, "get_topic"):
            try:
                return self.manager.get_topic(topic_id)
            except KeyError:
                pass
        if topic_id in self._explicit:
            t = self._explicit[topic_id]
            t.reload()
            return t
        if self.topics_root:
            cand = (self.topics_root / topic_id).resolve()
            if cand.parent == self.topics_root and (cand / "topic.toml").is_file():
                return Topic(cand)
        for t in self._all_topics():
            if t.id == topic_id:
                return t
        raise TopicNotFound(topic_id)

    # --- read helpers ------------------------------------------------------------
    def list_presets(self) -> list[dict[str, Any]]:
        return list_presets(self.manager)

    def list_topics(self) -> list[dict[str, Any]]:
        return [self.serialize_topic(t) for t in self._all_topics()]

    def serialize_topic(self, topic: Topic) -> dict[str, Any]:
        topic.reload()
        cfg = topic.config.model_dump(mode="json")
        paused = _read_paused(topic)
        speaker_enabled = _read_speaker_enabled(topic)
        records = TickLog(topic.ticks_path).records()
        ticks = [r for r in records if r.get("type") == "tick"]
        last_tick = ticks[-1] if ticks else None
        counters = compute_counters(records).model_dump(mode="json") if records else None
        cam_health = {}
        if self.hub is not None:
            for cid in topic.config.camera_ids:
                h = self.hub.health(cid)
                cam_health[cid] = h.status
        return {
            "id": topic.id,
            "camera_ids": list(topic.config.camera_ids),
            "cadence_sec": topic.config.cadence_sec,
            "window_sec": topic.config.window_sec,
            "main_model": topic.config.main_model.model_dump(mode="json"),
            "vision_model": topic.config.vision_model.model_dump(mode="json"),
            "asset_kinds": list(topic.config.asset_kinds),
            "review_policy": topic.config.review_policy.model_dump(mode="json"),
            "alert_defaults": dict(topic.config.alert_defaults),
            "paused": paused,
            "speaker_enabled": speaker_enabled,
            "mute": topic.load_mute().model_dump(mode="json"),
            "goal": topic.goal(),
            "rubric_version": topic.rubric_version(),
            "tool_count": len(topic.tools()),
            "ticks_total": len(ticks),
            "last_tick": last_tick,
            "counters": counters,
            "camera_health": cam_health,
            "config": cfg,
        }

    def set_mute(
        self,
        topic_id: str,
        *,
        mode: str,
        duration_min: float | None = None,
        anchor_alert_id: str | None = None,
        anchor_summary: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        topic = self.get_topic(topic_id)
        now = utcnow()
        if mode == "none":
            topic.clear_mute()
        elif mode == "timed":
            if duration_min is None or duration_min <= 0:
                raise ValueError("duration_min must be > 0 for timed mute")
            topic.set_mute(
                MuteState(
                    mode="timed",
                    muted_at=now,
                    muted_until=now + timedelta(minutes=float(duration_min)),
                    anchor_alert_id=anchor_alert_id,
                    anchor_summary=anchor_summary.strip(),
                    reason=reason.strip() or f"Muted for {duration_min:g} min",
                )
            )
        elif mode == "until_next_event":
            topic.set_mute(
                MuteState(
                    mode="until_next_event",
                    muted_at=now,
                    muted_until=None,
                    anchor_alert_id=anchor_alert_id,
                    anchor_summary=anchor_summary.strip(),
                    reason=reason.strip() or "Muted until next distinct event (AI-decided)",
                )
            )
        else:
            raise ValueError(f"unsupported mute mode {mode!r}")
        return self.serialize_topic(topic)

    def get_topic_detail(self, topic_id: str, tick_limit: int = 50) -> dict[str, Any]:
        topic = self.get_topic(topic_id)
        base = self.serialize_topic(topic)
        ticks_data = self.get_ticks(topic_id, limit=tick_limit)
        return {**base, **ticks_data}

    def get_ticks(self, topic_id: str, limit: int = 50) -> dict[str, Any]:
        topic = self.get_topic(topic_id)
        records = TickLog(topic.ticks_path).records()
        tick_recs = [r for r in records if r.get("type") == "tick"]
        review_recs = [r for r in records if r.get("type") == "review"]
        action_recs = [r for r in records if r.get("type") == "action"]
        alert_recs = [r for r in records if r.get("type") == "alert"]
        counters = compute_counters(records).model_dump(mode="json") if records else None

        # Aggregate token totals across recent ticks + reviews
        vision_tokens = {"input": 0, "cached": 0, "output": 0, "thinking": 0}
        main_tokens = {"input": 0, "cached": 0, "output": 0, "thinking": 0}
        for r in tick_recs[-limit:]:
            u = r.get("usage") or {}
            for k in vision_tokens:
                vision_tokens[k] += int(u.get(k, 0) or 0)
        for r in review_recs[-limit:]:
            u = r.get("usage") or {}
            for k in main_tokens:
                main_tokens[k] += int(u.get(k, 0) or 0)

        versions = topic.tool_versions()
        tools = []
        for p in topic.tools():
            rel = f"tools/{p.name}"
            try:
                code = p.read_text(encoding="utf-8", errors="replace")[:16000]
            except OSError:
                code = ""
            tools.append({
                "name": p.name,
                "path": rel,
                "version": versions.get(rel, ""),
                "code": code,
            })

        bundles = _list_topic_bundles(topic, limit=max(limit, 30))
        bundle_map = {b["bundle_id"]: b["assets"] for b in bundles}

        enriched_ticks = []
        for r in reversed(tick_recs[-limit:]):
            item = dict(r)
            bid = item.get("bundle_id")
            item["available_assets"] = bundle_map.get(bid, []) if bid else []
            enriched_ticks.append(item)

        session_meta = None
        active_harness = self.harness or getattr(self.manager, "harness", None)
        if active_harness is not None and hasattr(active_harness, "session_info"):
            try:
                session_meta = active_harness.session_info(topic.dir)
            except Exception:
                session_meta = None

        return {
            "topic_id": topic.id,
            "ticks": enriched_ticks,
            "reviews": list(reversed(review_recs[-limit:])),
            "actions": list(reversed(action_recs[-limit:])),
            "alerts": list(reversed(alert_recs[-limit:])),
            "counters": counters,
            "token_totals": {"vision": vision_tokens, "main": main_tokens},
            "session": session_meta,
            "vision_rubric": topic.rubric(),
            "rubric_version": topic.rubric_version(),
            "orchestrator_notes": topic.notes(),
            "tools": tools,
            "bundles": bundles,
        }

    # --- mutations (human-initiated via Web UI) ----------------------------------
    def create_topic(self, payload: dict[str, Any]) -> dict[str, Any]:
        topic_id = str(payload.get("id", "")).strip()
        if not is_safe_id(topic_id):
            raise ValueError("topic id must start with alphanumeric and contain only [A-Za-z0-9._-]")
        camera_ids = [str(c).strip() for c in (payload.get("camera_ids") or []) if str(c).strip()]
        if not camera_ids:
            raise ValueError("at least one camera_id is required")

        preset_id = payload.get("preset")
        preset = get_preset(preset_id, self.manager) if preset_id else None

        goal = payload.get("goal")
        if goal is None or not str(goal).strip():
            goal = (preset or {}).get("goal") or "Monitor subscribed cameras and alert on notable events."
        cadence_sec = payload.get("cadence_sec") or (preset or {}).get("cadence_sec") or 60
        window_sec = payload.get("window_sec") or (preset or {}).get("window_sec") or 20
        speaker_enabled = bool(payload.get("speaker_enabled", (preset or {}).get("speaker_enabled", False)))
        paused = bool(payload.get("paused", False))
        main_model = payload.get("main_model")
        vision_model = payload.get("vision_model")

        if self.manager is not None and hasattr(self.manager, "create_topic"):
            topic = self.manager.create_topic(
                topic_id,
                camera_ids,
                preset=preset_id,
                goal=goal,
                cadence_sec=int(cadence_sec),
                window_sec=int(window_sec),
                main_model=_coerce_model(main_model),
                vision_model=_coerce_model(vision_model),
                speaker_enabled=speaker_enabled,
                paused=paused,
            )
            return self.serialize_topic(topic)

        target_root = self.topics_root
        if target_root is None:
            existing = self._all_topics()
            if existing:
                target_root = existing[0].dir.parent
            else:
                raise RuntimeError("topics_root is not configured for creating new topics")
        target_dir = (target_root / topic_id).resolve()
        if target_dir.parent != target_root.resolve():
            raise ValueError("invalid topic id")

        cfg_kwargs: dict[str, Any] = {
            "cadence_sec": int(cadence_sec),
            "window_sec": int(window_sec),
        }
        if main_model:
            cfg_kwargs["main_model"] = _coerce_model(main_model)
        if vision_model:
            cfg_kwargs["vision_model"] = _coerce_model(vision_model)
        if "paused" in TopicConfig.model_fields:
            cfg_kwargs["paused"] = paused
        if "speaker_enabled" in TopicConfig.model_fields:
            cfg_kwargs["speaker_enabled"] = speaker_enabled
        elif speaker_enabled:
            cfg_kwargs["alert_defaults"] = {"speaker": True}

        topic = init_topic(target_dir, camera_ids, str(goal), topic_id=topic_id, **cfg_kwargs)
        if preset and preset.get("vision_rubric"):
            topic.write_rubric(preset["vision_rubric"])
        if "paused" not in TopicConfig.model_fields:
            _write_ui_flags(topic, paused=paused, speaker_enabled=speaker_enabled)
        self._explicit[topic.id] = topic
        return self.serialize_topic(topic)

    def update_topic(self, topic_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Mid-flow edit of Topic settings and human-owned ``goal.md``."""
        topic = self.get_topic(topic_id)

        if self.manager is not None and hasattr(self.manager, "update_topic"):
            kwargs: dict[str, Any] = {}
            for key in (
                "camera_ids", "cadence_sec", "window_sec", "review_policy",
                "paused", "speaker_enabled", "goal", "vision_rubric", "orchestrator_notes",
            ):
                if key in payload and payload[key] is not None:
                    kwargs[key] = payload[key]
            if payload.get("main_model") is not None:
                kwargs["main_model"] = _coerce_model(payload["main_model"])
            if payload.get("vision_model") is not None:
                kwargs["vision_model"] = _coerce_model(payload["vision_model"])
            updated = self.manager.update_topic(topic_id, **kwargs)
            if payload.get("alert_defaults") is not None:
                updated.update_config(alert_defaults=dict(payload["alert_defaults"]))
            return self.serialize_topic(updated)

        changes: dict[str, Any] = {}
        if payload.get("camera_ids") is not None:
            cams = [str(c).strip() for c in payload["camera_ids"] if str(c).strip()]
            if not cams:
                raise ValueError("camera_ids cannot be empty")
            changes["camera_ids"] = cams
        if payload.get("cadence_sec") is not None:
            changes["cadence_sec"] = int(payload["cadence_sec"])
        if payload.get("window_sec") is not None:
            changes["window_sec"] = int(payload["window_sec"])
        if payload.get("main_model") is not None:
            changes["main_model"] = _coerce_model(payload["main_model"]).model_dump(mode="json")
        if payload.get("vision_model") is not None:
            changes["vision_model"] = _coerce_model(payload["vision_model"]).model_dump(mode="json")
        if payload.get("review_policy") is not None:
            rp = ReviewPolicy.model_validate(payload["review_policy"])
            changes["review_policy"] = rp.model_dump(mode="json")
        if payload.get("alert_defaults") is not None:
            changes["alert_defaults"] = dict(payload["alert_defaults"])

        # paused & speaker_enabled: prefer TopicConfig fields if present, else state/ui_flags.json
        flag_updates: dict[str, Any] = {}
        if payload.get("paused") is not None:
            val = bool(payload["paused"])
            if "paused" in TopicConfig.model_fields:
                changes["paused"] = val
            else:
                flag_updates["paused"] = val
        if payload.get("speaker_enabled") is not None:
            val = bool(payload["speaker_enabled"])
            if "speaker_enabled" in TopicConfig.model_fields:
                changes["speaker_enabled"] = val
            else:
                flag_updates["speaker_enabled"] = val
                ad = dict(changes.get("alert_defaults", topic.config.alert_defaults))
                ad["speaker"] = val
                changes["alert_defaults"] = ad

        if changes:
            topic.update_config(**changes)
        if flag_updates:
            _write_ui_flags(topic, **flag_updates)

        # Human-owned goal.md edit — explicitly authorized for the human Web UI, never an agent tool
        if payload.get("goal") is not None:
            goal_text = str(payload["goal"]).strip() + "\n"
            (topic.dir / GOAL).write_text(goal_text, encoding="utf-8")
        if payload.get("vision_rubric") is not None:
            topic.write_rubric(str(payload["vision_rubric"]))
        if payload.get("orchestrator_notes") is not None:
            (topic.dir / NOTES).write_text(str(payload["orchestrator_notes"]), encoding="utf-8")

        return self.serialize_topic(topic)

    def resolve_asset(self, topic_id: str, bundle_id: str, asset_rel: str) -> Path:
        topic = self.get_topic(topic_id)
        if not is_safe_id(bundle_id):
            raise PermissionError(f"invalid bundle_id: {bundle_id!r}")
        if self.manager is not None and hasattr(self.manager, "resolve_bundle_asset"):
            try:
                p = Path(self.manager.resolve_bundle_asset(topic_id, bundle_id, asset_rel))
            except ValueError as exc:
                raise PermissionError(str(exc)) from None
            # Belt-and-suspenders confinement check in UI layer too
            state = topic.state_dir.resolve()
            if not p.resolve().is_relative_to(state) or not p.is_file():
                raise PermissionError("asset path outside topic state")
            return p.resolve()
        return resolve_bundle_asset(topic.dir, bundle_id, asset_rel)

    def chat(
        self,
        topic_id: str,
        message: str,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        topic = self.get_topic(topic_id)
        # Validate any provided attachment paths are confined to topic bundles/saved
        resolved_attachments: list[str] | None = None
        if attachments:
            resolved_attachments = []
            for att in attachments:
                att_str = str(att).strip()
                if not att_str:
                    continue
                # Accept either "bundle_id/camera_id/asset" or direct verified bundle asset
                parts = att_str.split("/", 1)
                if len(parts) == 2 and is_safe_id(parts[0]):
                    try:
                        p = resolve_bundle_asset(topic.dir, parts[0], parts[1])
                        resolved_attachments.append(str(p))
                        continue
                    except (FileNotFoundError, PermissionError):
                        pass
                # Also allow bundle_id alone -> pass bundle dir if exists
                if is_safe_id(att_str):
                    try:
                        bdir = topic.bundle_dir(att_str)
                        resolved_attachments.append(str(bdir))
                        continue
                    except FileNotFoundError:
                        pass
                raise PermissionError(f"invalid or unresolvable attachment: {att_str!r}")

        if self.manager is not None and hasattr(self.manager, "chat"):
            out = self.manager.chat(topic_id, message, attachments=resolved_attachments)
            return dict(out) if isinstance(out, dict) else out.model_dump(mode="json")

        loop = self.loops.get(topic_id)
        if loop is not None:
            res = loop.chat(message, attachments=resolved_attachments)
            return {
                "ok": res.ok,
                "reply": res.reply,
                "actions": [a.model_dump(mode="json") for a in res.actions],
                "usage": res.usage.model_dump(mode="json"),
                "error": res.error,
            }

        if self.harness is None:
            raise RuntimeError("no harness configured for chat")
        res = self.harness.chat_with_orchestrator(topic.context(), message, resolved_attachments)
        return {
            "ok": res.ok,
            "reply": res.reply,
            "actions": [a.model_dump(mode="json") for a in res.actions],
            "usage": res.usage.model_dump(mode="json"),
            "error": res.error,
        }


class AlertsBridge:
    """In-process ``NotifyService`` or loopback HTTP proxy for Alert Inbox routes."""

    def __init__(
        self,
        notifier: Any = None,
        *,
        topic_bridge: TopicBridge | None = None,
        notify_token: str | None = None,
    ):
        self.notifier = notifier
        self.topic_bridge = topic_bridge
        self.notify_token = notify_token

    def _is_service(self) -> bool:
        return self.notifier is not None and all(
            hasattr(self.notifier, m) for m in ("list", "get", "ack", "feedback")
        )

    def _http_client(self) -> httpx.Client | None:
        if self.notifier is None:
            return None
        if hasattr(self.notifier, "_http") and isinstance(self.notifier._http, httpx.Client):
            return self.notifier._http
        if isinstance(self.notifier, str):
            return httpx.Client(base_url=self.notifier, timeout=15.0, trust_env=False)
        return None

    def _headers(self) -> dict[str, str]:
        if self.notify_token:
            return {"Authorization": f"Bearer {self.notify_token}"}
        return {}

    def list_alerts(
        self,
        *,
        topic_id: str | None = None,
        severity: str | None = None,
        unacked: bool | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self._is_service():
            records = self.notifier.list(
                topic_id=topic_id, severity=severity, unacked=unacked, limit=limit
            )
            return [self._to_view(r) for r in records]
        client = self._http_client()
        if client is not None:
            params: dict[str, Any] = {"limit": limit}
            if topic_id:
                params["topic_id"] = topic_id
            if severity:
                params["severity"] = severity
            if unacked is not None:
                params["unacked"] = str(unacked).lower()
            resp = client.get("/v1/alerts", params=params, headers=self._headers())
            resp.raise_for_status()
            return list(resp.json())
        return []

    def get_alert(self, alert_id: str) -> dict[str, Any]:
        if self._is_service():
            try:
                rec = self.notifier.get(alert_id)
            except KeyError:
                raise AlertNotFound(alert_id) from None
            return self._to_view(rec)
        client = self._http_client()
        if client is not None:
            resp = client.get(f"/v1/alerts/{alert_id}", headers=self._headers())
            if resp.status_code == 404:
                raise AlertNotFound(alert_id)
            resp.raise_for_status()
            return dict(resp.json())
        raise AlertNotFound(alert_id)

    def ack(self, alert_id: str, by: str | None = None) -> dict[str, Any]:
        if self._is_service():
            try:
                rec = self.notifier.ack(alert_id, by=by)
            except KeyError:
                raise AlertNotFound(alert_id) from None
            return self._to_view(rec)
        client = self._http_client()
        if client is not None:
            body = {"by": by} if by else {}
            resp = client.post(f"/v1/alerts/{alert_id}/ack", json=body, headers=self._headers())
            if resp.status_code == 404:
                raise AlertNotFound(alert_id)
            resp.raise_for_status()
            return dict(resp.json())
        raise AlertNotFound(alert_id)

    def feedback(
        self,
        alert_id: str,
        rating: str,
        note: str = "",
        by: str = "",
    ) -> dict[str, Any]:
        if self._is_service():
            try:
                out = self.notifier.feedback(alert_id, rating, note=note, by=by)
            except KeyError:
                raise AlertNotFound(alert_id) from None
            return {
                "feedback": out["feedback"].model_dump(mode="json"),
                "pinned": bool(out.get("pinned", False)),
                "alert": self._to_view(out["alert"]),
            }
        client = self._http_client()
        if client is not None:
            resp = client.post(
                f"/v1/alerts/{alert_id}/feedback",
                json={"rating": rating, "note": note, "by": by},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                raise AlertNotFound(alert_id)
            resp.raise_for_status()
            return dict(resp.json())
        raise AlertNotFound(alert_id)

    def resolve_media(self, alert_id: str, kind: str) -> Path | None:
        if self._is_service():
            fn_name = "resolve_thumbnail" if kind == "thumbnail" else "resolve_clip"
            if hasattr(self.notifier, fn_name):
                p = getattr(self.notifier, fn_name)(alert_id)
                if p and Path(p).is_file():
                    return Path(p)
        try:
            view = self.get_alert(alert_id)
        except AlertNotFound:
            return None
        req = view.get("request") or {}
        tid = req.get("topic_id")
        bid = req.get("bundle_id")
        if not (self.topic_bridge and tid and bid and is_safe_id(bid)):
            return None
        try:
            topic = self.topic_bridge.get_topic(tid)
        except TopicNotFound:
            return None
        # Check clip_ref first for clips
        candidates: list[str] = []
        if kind == "clip" and req.get("clip_ref"):
            candidates.append(str(req["clip_ref"]))
        cams = list(req.get("camera_ids") or topic.config.camera_ids)
        filenames = (
            ("frame.jpg", "contact_sheet.jpg") if kind == "thumbnail" else ("clip.mp4",)
        )
        for cam in cams:
            for fn in filenames:
                candidates.append(f"{cam}/{fn}")
        for fn in filenames:
            candidates.append(fn)
        for rel in candidates:
            try:
                return resolve_bundle_asset(topic.dir, bid, rel)
            except (FileNotFoundError, PermissionError):
                continue
        return None

    def _to_view(self, record: AlertRecord) -> dict[str, Any]:
        if self._is_service() and hasattr(self.notifier, "alert_view"):
            return dict(self.notifier.alert_view(record))
        data = record.model_dump(mode="json")
        has_thumb = False
        has_clip = False
        if self._is_service():
            if hasattr(self.notifier, "resolve_thumbnail"):
                has_thumb = self.notifier.resolve_thumbnail(record.id) is not None
            if hasattr(self.notifier, "resolve_clip"):
                has_clip = self.notifier.resolve_clip(record.id) is not None
        if not has_thumb and not has_clip and self.topic_bridge and record.request.bundle_id:
            has_thumb = self._bundle_has(record.request.topic_id, record.request.bundle_id, "thumbnail")
            has_clip = self._bundle_has(record.request.topic_id, record.request.bundle_id, "clip")
        data["has_thumbnail"] = has_thumb
        data["has_clip"] = has_clip
        return data

    def _bundle_has(self, topic_id: str, bundle_id: str, kind: str) -> bool:
        if not (self.topic_bridge and is_safe_id(bundle_id)):
            return False
        try:
            topic = self.topic_bridge.get_topic(topic_id)
        except TopicNotFound:
            return False
        exts = (".jpg", ".jpeg", ".png") if kind == "thumbnail" else (".mp4",)
        for root in (topic.saved_dir, topic.bundles_dir):
            bdir = (root / bundle_id).resolve()
            if bdir.parent == root.resolve() and bdir.is_dir():
                for p in bdir.rglob("*"):
                    if p.is_file() and p.suffix.lower() in exts:
                        return True
        return False


# --- helpers --------------------------------------------------------------------


def _coerce_model(val: Any) -> ModelConfig | None:
    if val is None:
        return None
    if isinstance(val, ModelConfig):
        return val
    if isinstance(val, str):
        return ModelConfig(model=val)
    return ModelConfig.model_validate(val)


def _ui_flags_path(topic: Topic) -> Path:
    return topic.state_dir / "ui_flags.json"


def _read_ui_flags(topic: Topic) -> dict[str, Any]:
    p = _ui_flags_path(topic)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write_ui_flags(topic: Topic, **updates: Any) -> None:
    topic.state_dir.mkdir(parents=True, exist_ok=True)
    flags = _read_ui_flags(topic) | updates
    _ui_flags_path(topic).write_text(json.dumps(flags, indent=2) + "\n", encoding="utf-8")


def _read_paused(topic: Topic) -> bool:
    if hasattr(topic.config, "paused"):
        return bool(getattr(topic.config, "paused"))
    return bool(_read_ui_flags(topic).get("paused", False))


def _read_speaker_enabled(topic: Topic) -> bool:
    if hasattr(topic.config, "speaker_enabled"):
        return bool(getattr(topic.config, "speaker_enabled"))
    flags = _read_ui_flags(topic)
    if "speaker_enabled" in flags:
        return bool(flags["speaker_enabled"])
    return bool(topic.config.alert_defaults.get("speaker", False))


def _list_topic_bundles(topic: Topic, limit: int = 30) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for saved_flag, root in ((True, topic.saved_dir), (False, topic.bundles_dir)):
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir(), reverse=True):
            if not entry.is_dir() or not is_safe_id(entry.name):
                continue
            if entry.name in seen:
                continue
            assets: list[str] = []
            for f in sorted(entry.rglob("*")):
                if f.is_file() and f.name != "bundle.json":
                    rel = f.relative_to(entry).as_posix()
                    assets.append(rel)
            seen[entry.name] = {
                "bundle_id": entry.name,
                "saved": saved_flag,
                "assets": assets,
            }
            if len(seen) >= limit:
                break
    return sorted(seen.values(), key=lambda b: b["bundle_id"], reverse=True)[:limit]
