"""Phone / mobile RTMP or RTSP push ingest adapter (ARCHITECTURE §5.1, item 4).

Accepts an inbound stream pushed from a phone app (e.g. Larix, OBS, Prism) and writes
rolling fMP4 segments into ``<recordings_root>/<camera_id>/``.

- **MediaMTX-backed** (default when ``mediamtx`` is installed): MediaMTX listens for the
  push on ``rtmp://<bind_host>:<rtmp_port>/<path>`` (or RTSP), records fMP4 segments, and
  serves the local restream — zero FFmpeg process needed.
- **FFmpeg listen fallback** (when ``mediamtx`` is absent or ``options.ingest = "ffmpeg"``):
  runs a supervised ``ffmpeg -listen 1`` (RTMP) or ``-rtsp_flags listen`` (RTSP) writer
  that restarts automatically between push sessions.

``source`` is optional: a stream path (``"live/phone1"``) or credential-free listener URL
(``"rtmp://127.0.0.1:1935/live/phone1"``). Never put credentials in ``source``; use
``credential_ref`` if publish authentication is needed.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit

from vs_runtime.contracts import StreamHealth

from vs_streamhub import ffmpeg
from vs_streamhub.adapters.base import BaseAdapter, ffmpeg_base, segment_args
from vs_streamhub.credentials import CredentialError, redact_url, resolve_secret, scrub_credentials, with_credentials


def _parse_push_source(camera_id: str, source: str, options: dict) -> tuple[str, str, int, str]:
    """Return (protocol, bind_host, port, stream_path) from config.source + options."""
    s = (source or "").strip()
    protocol = str(options.get("protocol", "")).lower()
    bind_host = str(options.get("bind_host", "127.0.0.1"))
    port_opt = options.get("port")

    if "://" in s:
        if "@" in s.partition("://")[2].partition("/")[0]:
            raise ValueError(f"camera {camera_id!r}: put publish credentials in credential_ref, not in source")
        parts = urlsplit(s)
        scheme = parts.scheme.lower()
        if scheme not in ("rtmp", "rtsp"):
            raise ValueError(f"camera {camera_id!r}: rtmp_ingest source must use rtmp:// or rtsp:// (got {scheme!r})")
        protocol = protocol or scheme
        bind_host = parts.hostname or bind_host
        port = int(port_opt) if port_opt is not None else (parts.port or (1935 if protocol == "rtmp" else 8554))
        stream_path = parts.path.lstrip("/") or f"live/{camera_id}"
        return protocol, bind_host, port, stream_path

    protocol = protocol or "rtmp"
    if protocol not in ("rtmp", "rtsp"):
        raise ValueError(f"camera {camera_id!r}: protocol must be 'rtmp' or 'rtsp' (got {protocol!r})")
    port = int(port_opt) if port_opt is not None else (1935 if protocol == "rtmp" else 8554)
    stream_path = s.lstrip("/") or (f"live/{camera_id}" if protocol == "rtmp" else camera_id)
    return protocol, bind_host, port, stream_path


class RtmpIngestAdapter(BaseAdapter):
    kind = "rtmp_ingest"

    def __init__(self, config, segment_dir, **kw):
        super().__init__(config, segment_dir, **kw)
        self.protocol, self.bind_host, self.listen_port, self.stream_path = _parse_push_source(
            config.camera_id, config.source, config.options
        )

    @property
    def wants_mediamtx(self) -> bool:
        return self.config.options.get("ingest", "auto") in ("auto", "mediamtx")

    @property
    def wants_rtmp(self) -> bool:
        return self.protocol == "rtmp"

    def needs_writer(self) -> bool:
        return self.ingest == "ffmpeg"

    @property
    def ingest_url(self) -> str:
        """Credential-free push URL that a phone or encoder publishes to."""
        if self.ingest == "mediamtx" and self.mediamtx and self.mediamtx_path:
            if self.protocol == "rtmp":
                return self.mediamtx.rtmp_publish_url(self.mediamtx_path)
            return self.mediamtx.publish_url(self.mediamtx_path)
        return f"{self.protocol}://{self.bind_host}:{self.listen_port}/{self.stream_path}"

    def _listen_url(self) -> str:
        """Listener URL for FFmpeg fallback, with optional secret stream key / auth resolved at runtime."""
        base = f"{self.protocol}://{self.bind_host}:{self.listen_port}/{self.stream_path}"
        ref = self.config.credential_ref
        if not ref:
            return base
        secret = resolve_secret(ref)
        if "://" in secret:
            return secret
        if ":" in secret:
            return with_credentials(base, secret)
        # Single token treated as a stream key query parameter for RTMP push.
        return f"{base}?key={quote(secret, safe='')}"

    def writer_command(self) -> tuple[list[str], dict[str, str] | None]:
        url = self._listen_url()
        if self.protocol == "rtmp":
            in_args = ["-listen", "1", "-f", "flv", "-i", url]
        else:
            in_args = ["-rtsp_flags", "listen", "-rtsp_transport", "tcp", "-i", url]
        if self.config.options.get("transcode", False):
            codec = [
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-force_key_frames", "expr:gte(t,n_forced*1)", "-c:a", "aac", "-ac", "1",
            ]
        else:
            codec = ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-ac", "1"]
        return [*ffmpeg_base(), *in_args, *codec, *segment_args(self.segment_dir, self.segment_sec)], None

    def probe(self) -> StreamHealth:
        if self.config.credential_ref:
            try:
                resolve_secret(self.config.credential_ref)
            except CredentialError as e:
                return StreamHealth(camera_id=self.camera_id, status="offline", detail=scrub_credentials(str(e)))
        if self.ingest == "mediamtx" and self.mediamtx:
            if self.mediamtx.running and self.mediamtx_path:
                ready = self.mediamtx.ready(self.mediamtx_path)
                state = "publisher connected" if ready else "waiting for publisher"
                return StreamHealth(
                    camera_id=self.camera_id,
                    status="ok" if ready else "degraded",
                    detail=f"MediaMTX {self.protocol} push ({state}) at {redact_url(self.ingest_url)}",
                )
            return StreamHealth(
                camera_id=self.camera_id,
                status="ok",
                detail=f"ready for {self.protocol} push via MediaMTX at {redact_url(self.ingest_url)}",
            )
        try:
            ffmpeg.tool("ffmpeg")
        except ffmpeg.FFmpegError as e:
            return StreamHealth(camera_id=self.camera_id, status="offline", detail=str(e))
        return StreamHealth(
            camera_id=self.camera_id,
            status="ok",
            detail=f"ready for {self.protocol} push via FFmpeg listen at {redact_url(self.ingest_url)}",
        )

    def describe(self) -> dict:
        return {
            **super().describe(),
            "protocol": self.protocol,
            "ingest_url": redact_url(self.ingest_url),
            "credential_ref": self.config.credential_ref,
            "segment_sec": self.segment_sec,
        }
