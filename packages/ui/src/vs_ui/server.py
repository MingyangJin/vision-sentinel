"""Unified Web UI FastAPI server (ARCHITECTURE.md §5.5, §5.7).

- Serves the built React/Tailwind SPA from ``vs_ui/static/`` with zero Node runtime required.
- No login in MVP (§5.7); binds ``127.0.0.1`` by default and logs a prominent security warning
  if bound to ``0.0.0.0`` or any non-loopback address.
- On a loopback bind, requests must carry a loopback ``Host`` header (or one named with
  ``--allow-host``), which stops DNS-rebinding pages from reaching the UI through the browser.
- Never opens cameras directly or exposes camera URLs/credentials ("The One Rule", §5.1).
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from vs_notify.auth import is_loopback_host_header
from vs_runtime.contracts import Severity

from vs_ui.bridge import AlertNotFound, AlertsBridge, TopicBridge, TopicNotFound
from vs_ui.media import (
    camera_summary,
    default_clip_window,
    extract_camera_clip,
    guess_media_type,
    is_safe_id,
    iter_fmp4_stream,
    iter_mjpeg_frames,
    latest_snapshot,
    list_camera_ids,
    list_camera_segments,
    resolve_segment_path,
)

log = logging.getLogger("vs_ui.server")

DEFAULT_STATIC_DIR = Path(__file__).resolve().parent / "static"


def is_loopback_host(host: str) -> bool:
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def warn_if_non_loopback(host: str) -> bool:
    """Emit a loud security warning when binding to a non-loopback interface (§5.7)."""
    if is_loopback_host(host):
        return False
    log.warning(
        "SECURITY WARNING: Vision Sentinel Web UI has NO login in MVP (§5.7) and is binding "
        "to non-loopback address %r. Anyone who can reach this port can view cameras and chat "
        "with Main Agents. Do NOT expose this port to the public internet; keep it on a trusted "
        "LAN or private tunnel (e.g., Tailscale).",
        host,
    )
    return True


# --- Pydantic request schemas ---------------------------------------------------


class ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200)
    effort: str | None = Field(default=None, max_length=50)


class ReviewPolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all_clear_definition: str = ""
    all_clear_statuses: list[str] = Field(default_factory=lambda: ["clear"])
    digest_interval_sec: int | None = Field(default=None, ge=1)


class CreateTopicIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    camera_ids: list[str] = Field(min_length=1)
    preset: str | None = None
    goal: str | None = None
    cadence_sec: int | None = Field(default=None, ge=1)
    window_sec: int | None = Field(default=None, ge=1)
    main_model: ModelIn | str | None = None
    vision_model: ModelIn | str | None = None
    speaker_enabled: bool = False
    paused: bool = False


class UpdateTopicIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera_ids: list[str] | None = None
    cadence_sec: int | None = Field(default=None, ge=1)
    window_sec: int | None = Field(default=None, ge=1)
    main_model: ModelIn | str | None = None
    vision_model: ModelIn | str | None = None
    review_policy: ReviewPolicyIn | None = None
    alert_defaults: dict[str, Any] | None = None
    paused: bool | None = None
    speaker_enabled: bool | None = None
    goal: str | None = None
    vision_rubric: str | None = None
    orchestrator_notes: str | None = None


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=20000)
    attachments: list[str] | None = None


class MuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["none", "timed", "until_next_event"]
    duration_min: float | None = Field(default=None, gt=0, le=10080)
    anchor_alert_id: str | None = Field(default=None, max_length=200)
    anchor_summary: str = Field(default="", max_length=1000)
    reason: str = Field(default="", max_length=500)


class AckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: str | None = Field(default="web-ui", max_length=100)


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["up", "down"]
    note: str = Field(default="", max_length=1000)
    by: str = Field(default="web-ui", max_length=100)


# --- App factory ----------------------------------------------------------------


def create_app(
    *,
    manager: Any = None,
    hub: Any = None,
    notifier: Any = None,
    topics_root: Path | str | None = None,
    topics: list[Any] | None = None,
    harness: Any = None,
    loops: dict[str, Any] | None = None,
    notify_token: str | None = None,
    static_dir: Path | str | None = None,
    bind_host: str = "127.0.0.1",
    enforce_host: bool = False,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    """``enforce_host`` (set by ``serve_ui`` for a loopback bind) rejects requests whose
    ``Host`` header is neither a loopback name nor in ``allowed_hosts``. The UI has no login,
    so without this a web page could reach it from the user's own browser by DNS rebinding."""
    app = FastAPI(title="Vision Sentinel Unified Web UI", version="0.1.0")
    extra_hosts = {h.strip().lower() for h in allowed_hosts or () if h.strip()}

    @app.middleware("http")
    async def loopback_host_only(request, call_next):
        if enforce_host:
            host = request.headers.get("host")
            name = (host or "").strip().lower()
            if not is_loopback_host_header(host) and name not in extra_hosts \
                    and name.rpartition(":")[0] not in extra_hosts:
                return JSONResponse({"detail": "unrecognized Host header"}, status_code=403)
        return await call_next(request)

    topic_bridge = TopicBridge(
        manager=manager,
        topics_root=Path(topics_root) if topics_root else None,
        topics=topics,
        harness=harness,
        hub=hub,
        notifier=notifier,
        loops=loops,
    )
    alerts_bridge = AlertsBridge(
        notifier,
        topic_bridge=topic_bridge,
        notify_token=notify_token,
    )
    static_root = Path(static_dir).resolve() if static_dir else DEFAULT_STATIC_DIR.resolve()

    app.state.hub = hub
    app.state.topics = topic_bridge
    app.state.alerts = alerts_bridge
    app.state.static_dir = static_root
    app.state.bind_host = bind_host

    def _require_camera(camera_id: str) -> str:
        if not is_safe_id(camera_id) or camera_id not in list_camera_ids(hub):
            raise HTTPException(404, f"unknown camera {camera_id!r}")
        return camera_id

    # --- System & Presets -------------------------------------------------------

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        cams = [camera_summary(hub, cid) for cid in list_camera_ids(hub)]
        topics_list = topic_bridge.list_topics()
        return {
            "ok": True,
            "bind_host": bind_host,
            "non_loopback_warning": not is_loopback_host(bind_host),
            "cameras_total": len(cams),
            "cameras_ok": sum(1 for c in cams if c["health"]["status"] == "ok"),
            "topics_total": len(topics_list),
            "topics_active": sum(1 for t in topics_list if not t.get("paused")),
        }

    @app.get("/api/presets")
    def api_presets() -> list[dict[str, Any]]:
        return topic_bridge.list_presets()

    # --- Cameras & Stream Hub (strictly logical camera_id, zero credentials) ----

    @app.get("/api/cameras")
    def api_list_cameras() -> list[dict[str, Any]]:
        return [camera_summary(hub, cid) for cid in list_camera_ids(hub)]

    @app.get("/api/cameras/{camera_id}/snapshot.jpg")
    def api_camera_snapshot(camera_id: str) -> Response:
        _require_camera(camera_id)
        jpg = latest_snapshot(hub, camera_id)
        if not jpg:
            raise HTTPException(503, f"no snapshot available for camera {camera_id!r}")
        return Response(
            content=jpg,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/api/cameras/{camera_id}/live.mp4")
    async def api_camera_live_mp4(
        camera_id: str,
        max_chunks: int | None = Query(default=None, ge=1, le=500),
    ) -> StreamingResponse:
        _require_camera(camera_id)
        if not hasattr(hub, "iter_fmp4"):
            raise HTTPException(501, "live fMP4 streaming not supported by configured hub")
        return StreamingResponse(
            iter_fmp4_stream(hub, camera_id, max_chunks=max_chunks),
            media_type="video/mp4",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/cameras/{camera_id}/stream")
    async def api_camera_stream(
        camera_id: str,
        fps: float = Query(2.0, ge=0.2, le=15.0),
        max_frames: int | None = Query(default=None, ge=1, le=500),
    ) -> StreamingResponse:
        _require_camera(camera_id)
        return StreamingResponse(
            iter_mjpeg_frames(hub, camera_id, fps=fps, max_frames=max_frames),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.get("/api/cameras/{camera_id}/segments")
    def api_camera_segments(camera_id: str) -> dict[str, Any]:
        _require_camera(camera_id)
        segs = list_camera_segments(hub, camera_id)
        return {"camera_id": camera_id, "segments": segs}

    @app.get("/api/cameras/{camera_id}/segments/{segment_name}")
    def api_camera_segment_file(camera_id: str, segment_name: str) -> FileResponse:
        _require_camera(camera_id)
        try:
            path = resolve_segment_path(hub, camera_id, segment_name)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, f"segment {segment_name!r} not found") from None
        return FileResponse(path, media_type="video/mp4")

    @app.get("/api/cameras/{camera_id}/clip")
    def api_camera_clip(
        camera_id: str,
        background_tasks: BackgroundTasks,
        start: datetime | None = None,
        end: datetime | None = None,
        seconds: float = Query(30.0, ge=1.0, le=600.0),
    ) -> FileResponse:
        _require_camera(camera_id)
        if start is None or end is None:
            start, end = default_clip_window(seconds)
        if end <= start:
            raise HTTPException(400, "end must be after start")
        tmp_dir = Path(tempfile.mkdtemp(prefix="vs-ui-clip-"))
        clip = extract_camera_clip(hub, camera_id, start, end, tmp_dir)
        if clip is None or not clip.is_file():
            _cleanup_dir(tmp_dir)
            raise HTTPException(404, f"no clip available for camera {camera_id!r} in window")
        background_tasks.add_task(_cleanup_dir, tmp_dir)
        return FileResponse(clip, media_type="video/mp4", filename=f"{camera_id}-clip.mp4")

    # --- Topics, Config, goal.md, Ticks, Bundles & Main Agent Chat --------------

    @app.get("/api/topics")
    def api_list_topics() -> list[dict[str, Any]]:
        return topic_bridge.list_topics()

    @app.get("/api/topics/{topic_id}")
    def api_get_topic(topic_id: str, tick_limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        try:
            return topic_bridge.get_topic_detail(topic_id, tick_limit=tick_limit)
        except TopicNotFound:
            raise HTTPException(404, f"unknown topic {topic_id!r}") from None

    @app.post("/api/topics", status_code=201)
    def api_create_topic(body: CreateTopicIn) -> dict[str, Any]:
        try:
            return topic_bridge.create_topic(body.model_dump())
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from None

    @app.patch("/api/topics/{topic_id}")
    def api_update_topic(topic_id: str, body: UpdateTopicIn) -> dict[str, Any]:
        try:
            return topic_bridge.update_topic(topic_id, body.model_dump(exclude_unset=True))
        except TopicNotFound:
            raise HTTPException(404, f"unknown topic {topic_id!r}") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/topics/{topic_id}/mute")
    def api_mute_topic(topic_id: str, body: MuteIn) -> dict[str, Any]:
        try:
            return topic_bridge.set_mute(
                topic_id,
                mode=body.mode,
                duration_min=body.duration_min,
                anchor_alert_id=body.anchor_alert_id,
                anchor_summary=body.anchor_summary,
                reason=body.reason,
            )
        except TopicNotFound:
            raise HTTPException(404, f"unknown topic {topic_id!r}") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/topics/{topic_id}/ticks")
    def api_topic_ticks(topic_id: str, limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        try:
            return topic_bridge.get_ticks(topic_id, limit=limit)
        except TopicNotFound:
            raise HTTPException(404, f"unknown topic {topic_id!r}") from None

    @app.get("/api/topics/{topic_id}/bundles/{bundle_id}/{asset:path}")
    def api_bundle_asset(topic_id: str, bundle_id: str, asset: str) -> FileResponse:
        try:
            path = topic_bridge.resolve_asset(topic_id, bundle_id, asset)
        except TopicNotFound:
            raise HTTPException(404, f"unknown topic {topic_id!r}") from None
        except (PermissionError, ValueError) as exc:
            raise HTTPException(403, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, f"asset {asset!r} not found") from None
        return FileResponse(path, media_type=guess_media_type(path))

    @app.post("/api/topics/{topic_id}/chat")
    def api_topic_chat(topic_id: str, body: ChatIn) -> dict[str, Any]:
        try:
            return topic_bridge.chat(topic_id, body.message, attachments=body.attachments)
        except TopicNotFound:
            raise HTTPException(404, f"unknown topic {topic_id!r}") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None

    # --- Alert Inbox (proxying vs_notify in-process or loopback) ----------------

    @app.get("/api/alerts")
    def api_list_alerts(
        topic_id: str | None = None,
        severity: Severity | None = None,
        unacked: bool | None = None,
        limit: int = Query(50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return alerts_bridge.list_alerts(
            topic_id=topic_id, severity=severity, unacked=unacked, limit=limit
        )

    @app.get("/api/alerts/{alert_id}")
    def api_get_alert(alert_id: str) -> dict[str, Any]:
        try:
            return alerts_bridge.get_alert(alert_id)
        except AlertNotFound:
            raise HTTPException(404, f"no such alert {alert_id!r}") from None

    @app.post("/api/alerts/{alert_id}/ack")
    def api_ack_alert(alert_id: str, body: AckIn | None = None) -> dict[str, Any]:
        try:
            return alerts_bridge.ack(alert_id, by=body.by if body else "web-ui")
        except AlertNotFound:
            raise HTTPException(404, f"no such alert {alert_id!r}") from None

    @app.post("/api/alerts/{alert_id}/feedback")
    def api_feedback_alert(alert_id: str, body: FeedbackIn) -> dict[str, Any]:
        try:
            return alerts_bridge.feedback(
                alert_id, body.rating, note=body.note, by=body.by or "web-ui"
            )
        except AlertNotFound:
            raise HTTPException(404, f"no such alert {alert_id!r}") from None

    @app.get("/api/alerts/{alert_id}/thumbnail")
    def api_alert_thumbnail(alert_id: str) -> FileResponse:
        path = alerts_bridge.resolve_media(alert_id, "thumbnail")
        if path is None:
            raise HTTPException(404, "no thumbnail for alert")
        return FileResponse(path, media_type=guess_media_type(path))

    @app.get("/api/alerts/{alert_id}/clip")
    def api_alert_clip(alert_id: str) -> FileResponse:
        path = alerts_bridge.resolve_media(alert_id, "clip")
        if path is None:
            raise HTTPException(404, "no clip for alert")
        return FileResponse(path, media_type="video/mp4")

    # --- Static Frontend + SPA Fallback -----------------------------------------

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> Response:
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        if full_path:
            candidate = (static_root / full_path).resolve()
            if candidate.is_relative_to(static_root) and candidate.is_file():
                return FileResponse(candidate, media_type=guess_media_type(candidate))
        index = static_root / "index.html"
        if index.is_file():
            return FileResponse(index, media_type="text/html")
        return JSONResponse(
            {
                "detail": "Vision Sentinel Web UI static assets are not built yet. "
                "Run `npm run build` in packages/ui/frontend/."
            },
            status_code=503,
        )

    return app


def _cleanup_dir(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def serve_ui(
    *,
    manager: Any = None,
    hub: Any = None,
    notifier: Any = None,
    config_path: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    harness: Any = None,
    topics_root: Path | str | None = None,
    notify_token: str | None = None,
    start_hub: bool = False,
    allowed_hosts: list[str] | None = None,
) -> None:
    """Launch the Unified Web UI server (called by ``vision-sentinel ui`` or ``vs-ui``)."""
    import uvicorn

    started_hub = None
    if config_path is not None and (manager is None or hub is None or notifier is None):
        from vs_runtime.cli import App

        runtime_app = App(Path(config_path))
        if hub is None:
            hub = runtime_app.hub()
            if start_hub:
                hub.start()
                started_hub = hub
        if notifier is None:
            notifier = runtime_app.notifier()
        if harness is None:
            harness = runtime_app.harness()
        if topics_root is None and runtime_app.topics:
            topics_root = runtime_app.topics[0].dir.parent
        topics = runtime_app.topics
    else:
        topics = None

    warn_if_non_loopback(host)
    fastapi_app = create_app(
        manager=manager,
        hub=hub,
        notifier=notifier,
        topics_root=topics_root,
        topics=topics,
        harness=harness,
        notify_token=notify_token,
        bind_host=host,
        enforce_host=is_loopback_host(host),
        allowed_hosts=allowed_hosts,
    )
    try:
        uvicorn.run(fastapi_app, host=host, port=port, log_level="info")
    finally:
        if started_hub is not None:
            started_hub.stop()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vs-ui", description="Vision Sentinel Unified Web UI server")
    p.add_argument("--config", type=Path, default=Path("vision-sentinel.toml"),
                   help="Path to vision-sentinel.toml")
    p.add_argument("--host", default="127.0.0.1",
                   help="Bind host (default 127.0.0.1; warns on non-loopback)")
    p.add_argument("--port", type=int, default=8080, help="HTTP port (default 8080)")
    p.add_argument("--start-hub", action="store_true",
                   help="Start StreamHub ingestion inside this process")
    p.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="Extra Host name to accept on a loopback bind (e.g. your private tunnel's name)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    serve_ui(
        config_path=args.config if args.config.exists() else None,
        host=args.host,
        port=args.port,
        start_hub=args.start_hub,
        allowed_hosts=args.allow_host,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
