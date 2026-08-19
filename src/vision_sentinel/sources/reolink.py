"""Reolink camera integration.

Reolink serves two RTSP streams per channel. Vision Sentinel uses them for
different jobs: the substream is decoded continuously for cheap perception,
the main stream is only touched when something needs a high-detail clip.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

# H.264 firmware serves h264Preview_*; H.265 models use Preview_*.
PATHS = {
    "sub": ["h264Preview_01_sub", "Preview_01_sub"],
    "main": ["h264Preview_01_main", "Preview_01_main"],
}


@dataclass(frozen=True)
class StreamInfo:
    url: str
    codec: str
    width: int
    height: int
    fps: float

    def __str__(self) -> str:
        return f"{self.width}x{self.height} @ {self.fps:.1f}fps {self.codec}"


def redact(url: str) -> str:
    """Strip credentials so a URL is safe to log."""
    parts = urlsplit(url)
    if not parts.username:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, f"***:***@{host}", parts.path, parts.query, ""))


_CRED_RE = re.compile(r"(rtsps?://)[^/\s]+@")  # greedy: password may contain @


def scrub(text: str) -> str:
    """Strip credentials from arbitrary text, e.g. ffmpeg's stderr."""
    return _CRED_RE.sub(r"\1***:***@", text)


def url(host: str, user: str, password: str, path: str, port: int = 554) -> str:
    return f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{path}"


def probe(rtsp_url: str, timeout: float = 15.0) -> StreamInfo | None:
    """Read stream properties with ffprobe. None if the stream did not open."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-rtsp_transport", "tcp",
         "-select_streams", "v:0", "-of", "json",
         "-show_entries", "stream=codec_name,width,height,avg_frame_rate", rtsp_url],
        capture_output=True, text=True, timeout=timeout,
    )
    if out.returncode != 0:
        return None
    streams = json.loads(out.stdout or "{}").get("streams") or []
    if not streams:
        return None
    s = streams[0]
    num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
    return StreamInfo(
        url=rtsp_url,
        codec=s.get("codec_name", "?"),
        width=s.get("width", 0),
        height=s.get("height", 0),
        fps=float(num) / float(den) if den and float(den) else 0.0,
    )


def open_stream(host: str, user: str, password: str, kind: str) -> StreamInfo | None:
    """Resolve a stream by kind ('sub' or 'main'), trying known path conventions."""
    for path in PATHS[kind]:
        info = probe(url(host, user, password, path))
        if info:
            return info
    return None
