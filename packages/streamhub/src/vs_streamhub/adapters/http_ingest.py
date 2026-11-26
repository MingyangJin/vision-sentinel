"""HTTP fMP4 segment push-ingest adapter for companion camera apps (e.g. macOS Camera Client).

Instead of pulling from an IP camera or fighting OS camera permissions in a background CLI,
a native client app (such as ``clients/macos-camera``) captures video + microphone natively,
encodes 2-second fragmented MP4 segments with hardware VideoToolbox/AVFoundation, and POSTs
each self-contained ``.mp4`` segment to::

    POST http://<bind>:<port>/v1/ingest/<camera_id>/segment
    Content-Type: video/mp4
    Authorization: Bearer <token>   # when credential_ref is set (required for a non-loopback bind)

Multiple ``http_ingest`` / ``macos_camera`` cameras configured on the same ``(bind, port)``
share one lightweight HTTP server cleanly.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from vs_runtime.contracts import CameraConfig, StreamHealth, utcnow

from vs_streamhub import segments
from vs_streamhub.adapters.base import BaseAdapter
from vs_streamhub.credentials import resolve_secret, scrub_credentials

log = logging.getLogger("vs_streamhub.http_ingest")

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8560
DEFAULT_MAX_SEGMENT_BYTES = 32 * 1024 * 1024  # 32 MiB


def _is_loopback(bind: str) -> bool:
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def _looks_like_mp4(data: bytes) -> bool:
    """Fast structural check: ISO BMFF MP4 begins with an ``ftyp`` box at bytes 4..8."""
    return len(data) >= 16 and data[4:8] == b"ftyp"


def _parse_segment_time(header: str | None) -> datetime:
    if header:
        try:
            dt = datetime.fromisoformat(header.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt
            return dt.astimezone().replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.now()


class _SharedIngestServer:
    """Ref-counted ``ThreadingHTTPServer`` shared by adapters bound to one ``(bind, port)``."""

    def __init__(self, bind: str, port: int):
        self.bind = bind
        self.port = port
        self._lock = threading.Lock()
        self._adapters: dict[str, HttpIngestAdapter] = {}
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.actual_port: int = port

    def register(self, adapter: HttpIngestAdapter) -> None:
        with self._lock:
            self._adapters[adapter.camera_id] = adapter
            if self._httpd is None:
                owner = self

                class _Handler(BaseHTTPRequestHandler):
                    protocol_version = "HTTP/1.1"

                    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                        log.debug("http_ingest %s - %s", self.address_string(), format % args)

                    def _send_json(self, code: int, payload: dict[str, Any], *, close: bool = False) -> None:
                        body = json.dumps(payload).encode("utf-8")
                        self.send_response(code)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        if close:
                            self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(body)

                    def _drain_request_body(self, limit: int = 4 * 1024 * 1024) -> None:
                        try:
                            length = int(self.headers.get("Content-Length", "0"))
                        except ValueError:
                            return
                        remaining = min(max(length, 0), limit)
                        while remaining > 0:
                            chunk = self.rfile.read(min(remaining, 65536))
                            if not chunk:
                                break
                            remaining -= len(chunk)

                    def do_GET(self) -> None:  # noqa: N802
                        path = urlparse(self.path).path.rstrip("/")
                        if path in ("/v1/ingest/health", "/healthz"):
                            with owner._lock:
                                cams = {
                                    cid: ad.health().model_dump(mode="json")
                                    for cid, ad in owner._adapters.items()
                                }
                            self._send_json(200, {"ok": True, "cameras": cams})
                            return
                        self._send_json(404, {"error": "not found"})

                    def do_POST(self) -> None:  # noqa: N802
                        path = urlparse(self.path).path.rstrip("/")
                        parts = [unquote(p) for p in path.split("/") if p]
                        if len(parts) != 4 or parts[0] != "v1" or parts[1] != "ingest" or parts[3] != "segment":
                            self._drain_request_body()
                            self._send_json(404, {"error": "not found"}, close=True)
                            return
                        cam_id = parts[2]
                        with owner._lock:
                            ad = owner._adapters.get(cam_id)
                        if ad is None:
                            self._drain_request_body()
                            self._send_json(404, {"error": f"unknown camera_id {cam_id!r}"}, close=True)
                            return
                        if not ad.check_auth(self.headers.get("Authorization")):
                            self._drain_request_body()
                            self._send_json(401, {"error": "unauthorized"}, close=True)
                            return
                        try:
                            length = int(self.headers.get("Content-Length", "0"))
                        except ValueError:
                            self._send_json(400, {"error": "invalid Content-Length"})
                            return
                        if length <= 0:
                            self._send_json(400, {"error": "empty segment payload"})
                            return
                        if length > ad.max_segment_bytes:
                            self._send_json(413, {"error": "segment exceeds max_segment_bytes"})
                            return
                        body = self.rfile.read(length)
                        if len(body) != length:
                            self._send_json(400, {"error": "truncated request body"})
                            return
                        if not _looks_like_mp4(body):
                            self._send_json(400, {"error": "payload is not an MP4 segment (missing ftyp box)"})
                            return
                        try:
                            seg_name = ad.accept_segment(body, self.headers.get("X-Segment-Start"))
                        except Exception as exc:
                            self._send_json(500, {"error": scrub_credentials(str(exc))})
                            return
                        self._send_json(
                            200,
                            {
                                "ok": True,
                                "camera_id": cam_id,
                                "segment": seg_name,
                                "bytes": len(body),
                            },
                        )

                class _Server(ThreadingHTTPServer):
                    allow_reuse_address = True
                    daemon_threads = True

                self._httpd = _Server((self.bind, self.port), _Handler)
                self.actual_port = int(self._httpd.server_address[1])
                self._thread = threading.Thread(
                    target=self._httpd.serve_forever,
                    name=f"vs-http-ingest-{self.bind}:{self.actual_port}",
                    daemon=True,
                )
                self._thread.start()
                log.info("http_ingest listening on http://%s:%d", self.bind, self.actual_port)

    def unregister(self, camera_id: str) -> bool:
        """Remove a camera; shuts down the server and returns True if no cameras remain."""
        with self._lock:
            self._adapters.pop(camera_id, None)
            if self._adapters:
                return False
            httpd = self._httpd
            thread = self._thread
            self._httpd = None
            self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=3.0)
        return True


_REGISTRY_LOCK = threading.Lock()
_SERVERS: dict[tuple[str, int], _SharedIngestServer] = {}


def _acquire_server(bind: str, port: int, adapter: HttpIngestAdapter) -> _SharedIngestServer:
    with _REGISTRY_LOCK:
        # Ephemeral port=0 gets its own dedicated server instance keyed by adapter id.
        key = (bind, port) if port != 0 else (f"{bind}:{id(adapter)}", 0)
        srv = _SERVERS.get(key)
        if srv is None:
            srv = _SharedIngestServer(bind, port)
            _SERVERS[key] = srv
        srv.register(adapter)
        return srv


def _release_server(bind: str, port: int, adapter: HttpIngestAdapter) -> None:
    with _REGISTRY_LOCK:
        key = (bind, port) if port != 0 else (f"{bind}:{id(adapter)}", 0)
        srv = _SERVERS.get(key)
        if srv is None:
            return
        if srv.unregister(adapter.camera_id):
            _SERVERS.pop(key, None)


class HttpIngestAdapter(BaseAdapter):
    """Receives self-contained fMP4 segments over HTTP POST (e.g. from ``clients/macos-camera``)."""

    kind = "http_ingest"
    segments_atomic = True  # accept_segment() writes a temp file and renames it into place

    def __init__(self, config: CameraConfig, segment_dir: Path, **kw):
        super().__init__(config, segment_dir, **kw)
        self.bind = str(config.options.get("bind", DEFAULT_BIND))
        self.port = int(config.options.get("port", DEFAULT_PORT))
        self.max_segment_bytes = int(config.options.get("max_segment_bytes", DEFAULT_MAX_SEGMENT_BYTES))
        if not _is_loopback(self.bind) and not config.credential_ref:
            # Without a token, anyone who can reach the port could feed this camera fake video.
            raise ValueError(
                f"camera {config.camera_id!r}: http_ingest bind {self.bind!r} is not loopback, so "
                "credential_ref (the Bearer token the camera client sends) is required"
            )
        self._server: _SharedIngestServer | None = None
        self._running = False
        self._token: str | None = None
        self._last_received_monotonic: float | None = None
        self._segments_received = 0
        self._seq_lock = threading.Lock()

    @property
    def wants_mediamtx(self) -> bool:
        return False

    @property
    def actual_port(self) -> int:
        return self._server.actual_port if self._server is not None else self.port

    def ingest_url(self) -> str:
        host = "127.0.0.1" if self.bind in ("0.0.0.0", "::", "") else self.bind
        return f"http://{host}:{self.actual_port}/v1/ingest/{self.camera_id}/segment"

    def writer_command(self) -> tuple[list[str], dict[str, str] | None]:
        raise NotImplementedError("HttpIngestAdapter runs an in-process HTTP receiver, not FFmpeg")

    def check_auth(self, auth_header: str | None) -> bool:
        if not self._token:
            return True
        if not auth_header or not auth_header.startswith("Bearer "):
            return False
        supplied = auth_header[7:].strip()
        return hmac.compare_digest(supplied, self._token)

    def accept_segment(self, data: bytes, started_header: str | None = None) -> str:
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        local_dt = _parse_segment_time(started_header)
        with self._seq_lock:
            # Ensure monotonic unique filename even if two segments arrive in the same microsecond.
            name = local_dt.strftime("%Y-%m-%d_%H-%M-%S-%f.mp4")
            target = self.segment_dir / name
            if target.exists():
                name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f.mp4")
                target = self.segment_dir / name
            tmp = self.segment_dir / f".{name}.tmp"
            tmp.write_bytes(data)
            tmp.replace(target)
            self._last_received_monotonic = time.monotonic()
            self._segments_received += 1
        return name

    def start(self) -> None:
        if self._running:
            return
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        if self.config.credential_ref:
            self._token = resolve_secret(self.config.credential_ref)
        else:
            self._token = None
        self._server = _acquire_server(self.bind, self.port, self)
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        _release_server(self.bind, self.port, self)
        self._server = None
        self._running = False

    def probe(self) -> StreamHealth:
        auth_note = "Bearer token required" if self.config.credential_ref else "no token, loopback only"
        return StreamHealth(
            camera_id=self.camera_id,
            status="ok",
            detail=f"http_ingest ready at {self.ingest_url()} ({auth_note})",
        )

    def health(self, now: datetime | None = None) -> StreamHealth:
        now = now or utcnow()
        last = segments.newest_write(self.segment_dir)
        if not self._running:
            return StreamHealth(
                camera_id=self.camera_id,
                status="offline",
                checked_at=now,
                last_segment_at=last,
                detail="http_ingest stopped",
            )
        if last is None:
            return StreamHealth(
                camera_id=self.camera_id,
                status="degraded",
                checked_at=now,
                last_segment_at=None,
                detail=f"waiting for camera client push at {self.ingest_url()}",
            )
        age = (now - last).total_seconds()
        if age <= self.stale_sec:
            return StreamHealth(
                camera_id=self.camera_id,
                status="ok",
                checked_at=now,
                last_segment_at=last,
                detail=f"{self._segments_received} segments received",
            )
        return StreamHealth(
            camera_id=self.camera_id,
            status="degraded",
            checked_at=now,
            last_segment_at=last,
            detail=f"no segment from camera client for {age:.0f}s (expected at {self.ingest_url()})",
        )

    def describe(self) -> dict[str, Any]:
        return {
            **super().describe(),
            "ingest": "http_push",
            "bind": self.bind,
            "port": self.actual_port,
            "ingest_url": self.ingest_url(),
            "bearer_required": bool(self.config.credential_ref),
            "credential_ref": self.config.credential_ref,
        }
