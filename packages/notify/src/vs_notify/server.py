"""HTTP API of the Notification Service (ARCHITECTURE.md §5.4, §5.7).

- ``POST /v1/alerts``: server-internal, loopback only (runtime and watchdog): loopback peer,
  no forwarding headers, loopback ``Host``.
- ``GET /v1/alerts``, ``GET /v1/alerts/{id}``, ``POST /v1/alerts/{id}/ack``,
  ``POST /v1/alerts/{id}/feedback``, ``POST /v1/devices/register``: device calls, each needing
  ``Authorization: Bearer <household token>``.
- ``GET /healthz``: liveness, open.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from vs_runtime.contracts import AlertRecord, AlertRequest, Severity

from .auth import (
    bearer_ok,
    ensure_household_token,
    is_loopback_host_header,
    is_loopback_request,
    load_household_token,
)
from .config import NotifyConfig, build_notifier
from .service import NotFound, NotifyService

log = logging.getLogger("vs_notify.server")

LOOPBACK_ONLY = {("POST", "/v1/alerts"), ("POST", "/v1/alerts/")}


class AckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: str | None = Field(default=None, max_length=100)


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["up", "down"]
    note: str = Field(default="", max_length=1000)
    by: str = Field(default="", max_length=100)


class DeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["ios", "android"]
    token: str = Field(min_length=1, max_length=4096)
    name: str = Field(default="", max_length=100)


def create_app(service: NotifyService, household_token: str | None) -> FastAPI:
    app = FastAPI(title="Vision Sentinel Notification Service", version="0.1.0")
    app.state.service = service
    if not household_token:
        log.warning("no household token configured: every device call will be rejected")

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        """Server-internal routes refuse non-loopback callers before the body is even parsed."""
        if (request.method, request.url.path) in LOOPBACK_ONLY:
            host = request.client.host if request.client else None
            # Peer must be this machine, with no proxy in between, addressed by a loopback
            # name (a DNS-rebinding page reaches the port under its own host name).
            if not is_loopback_request(host, request.headers) or not is_loopback_host_header(
                request.headers.get("host")
            ):
                return JSONResponse({"detail": "this endpoint accepts loopback calls only"}, 403)
        return await call_next(request)

    def device_auth(request: Request) -> None:
        if not bearer_ok(request.headers.get("authorization"), household_token):
            raise HTTPException(401, "missing or invalid household token",
                                headers={"WWW-Authenticate": "Bearer"})

    def get_or_404(alert_id: str) -> AlertRecord:
        try:
            return service.get(alert_id)
        except NotFound:
            raise HTTPException(404, "no such alert") from None

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "sinks": [s.name for s in service.sinks]}

    @app.post("/v1/alerts")
    def post_alert(req: AlertRequest) -> AlertRecord:
        return service.send_alert(req)

    @app.get("/v1/alerts", dependencies=[Depends(device_auth)])
    def list_alerts(
        topic_id: str | None = None,
        severity: Severity | None = None,
        unacked: bool | None = None,
        since: datetime | None = None,
        limit: int = Query(50, ge=1, le=500),
    ) -> list[dict]:
        rows = service.list(topic_id=topic_id, severity=severity, unacked=unacked,
                            since_ts=since.timestamp() if since else None, limit=limit)
        return [service.alert_view(r) for r in rows]

    @app.get("/v1/alerts/{alert_id}", dependencies=[Depends(device_auth)])
    def get_alert(alert_id: str) -> dict:
        return service.alert_view(get_or_404(alert_id))

    @app.get("/v1/alerts/{alert_id}/thumbnail", dependencies=[Depends(device_auth)])
    def get_thumbnail(alert_id: str) -> FileResponse:
        rec = get_or_404(alert_id)
        path = service.resolve_thumbnail(rec)
        if path is None:
            raise HTTPException(404, "no thumbnail for this alert")
        media_type = _media_type(path, default="image/jpeg")
        return FileResponse(path, media_type=media_type)

    @app.get("/v1/alerts/{alert_id}/clip", dependencies=[Depends(device_auth)])
    def get_clip(alert_id: str) -> FileResponse:
        rec = get_or_404(alert_id)
        path = service.resolve_clip(rec)
        if path is None:
            raise HTTPException(404, "no clip for this alert")
        media_type = _media_type(path, default="video/mp4")
        return FileResponse(path, media_type=media_type)

    @app.post("/v1/alerts/{alert_id}/ack", dependencies=[Depends(device_auth)])
    def ack(alert_id: str, body: AckIn | None = None) -> dict:
        get_or_404(alert_id)
        return service.alert_view(service.ack(alert_id, by=body.by if body else None))

    @app.post("/v1/alerts/{alert_id}/feedback", dependencies=[Depends(device_auth)])
    def feedback(alert_id: str, body: FeedbackIn) -> dict:
        get_or_404(alert_id)
        out = service.feedback(alert_id, body.rating, note=body.note, by=body.by)
        return {
            "feedback": out["feedback"].model_dump(mode="json"),
            "pinned": out["pinned"],
            "alert": service.alert_view(out["alert"]),
        }

    @app.post("/v1/devices/register", dependencies=[Depends(device_auth)])
    def register_device(body: DeviceIn) -> dict:
        return service.register_device(body.platform, body.token, body.name)

    return app


def _media_type(path: Path, default: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
    }.get(path.suffix.lower(), default)


def load_config(path: Path | None) -> NotifyConfig:
    if path is None:
        return NotifyConfig()
    data = tomllib.loads(path.read_text())
    table = dict(data.get("notify", data))
    # The same file can drive `vision-sentinel` too. `url` there points the runtime at this
    # service, and a relative `db` means "next to the config file" for both programs.
    table.pop("url", None)
    db = table.get("db")
    if db and db != ":memory:" and not Path(db).expanduser().is_absolute():
        table["db"] = str((path.parent / db).resolve())
    return NotifyConfig.model_validate(table)


def serve(config: NotifyConfig) -> None:
    import uvicorn

    token_path = Path(config.token_file).expanduser() if config.token_file else None
    app = create_app(build_notifier(config), load_household_token(token_path))
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vs-notify", description=__doc__.split("\n")[0])
    p.add_argument("--config", type=Path, help="TOML file with a [notify] table")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the HTTP service")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    sub.add_parser("check", help="print the resolved config (no secrets) and exit")
    t = sub.add_parser("token", help="create the household token file if missing; print its path")
    t.add_argument("--show", action="store_true", help="also print the token, to enter in an app")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    cfg = load_config(args.config)
    token_path = Path(cfg.token_file).expanduser() if cfg.token_file else None
    if args.cmd == "token":
        path = ensure_household_token(token_path)
        print(path)
        if args.show:
            print(load_household_token(path))
        return 0
    if args.cmd == "check":
        shown = cfg.model_dump()
        shown["sinks"] = [{"type": s.get("type"), "name": s.get("name", s.get("type"))}
                          for s in cfg.sinks]  # sink options may carry URLs with secrets
        shown["db"] = cfg.db_path()
        shown["household_token"] = "set" if load_household_token(token_path) else "MISSING"
        for k, v in shown.items():
            print(f"{k} = {v}")
        return 0
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    serve(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
