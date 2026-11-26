"""FFmpeg/ffprobe helpers. Every error text that leaves here is scrubbed."""

from __future__ import annotations

import functools
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from vs_streamhub.credentials import scrub_credentials


class FFmpegError(RuntimeError):
    pass


def tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise FFmpegError(f"{name} not found on PATH; install FFmpeg (https://ffmpeg.org/download.html)")
    return path


def run(args: list[str], *, timeout: float = 120.0, check: bool = True, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run ffmpeg/ffprobe (args[0] is the tool name). Raises FFmpegError with scrubbed stderr."""
    cmd = [tool(args[0]), *args[1:]]
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        # TimeoutExpired carries the full argv, which may hold a camera URL: never re-raise it.
        raise FFmpegError(f"{args[0]} timed out after {timeout:.0f}s") from None
    if check and proc.returncode != 0:
        tail = scrub_credentials(proc.stderr.decode(errors="replace").strip())[-600:]
        raise FFmpegError(f"{args[0]} exited {proc.returncode}: {tail}")
    return proc


@functools.cache
def has_filter(name: str) -> bool:
    try:
        out = run(["ffmpeg", "-hide_banner", "-filters"], timeout=15).stdout.decode()
    except FFmpegError:
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


@dataclass(frozen=True)
class MediaInfo:
    duration: float  # seconds, 0.0 if unknown
    video_codec: str | None = None
    width: int = 0
    height: int = 0
    fps: float = 0.0
    audio_codec: str | None = None

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None


def probe(target: str | Path, *, timeout: float = 15.0, extra: list[str] | None = None) -> MediaInfo | None:
    """Duration and first video/audio stream of a file or URL; None if unreadable."""
    try:
        out = run(
            ["ffprobe", "-v", "error", *(extra or []), "-of", "json", "-show_entries",
             "format=duration,start_time:stream=codec_type,codec_name,width,height,avg_frame_rate,nb_frames", str(target)],
            timeout=timeout,
        )
        data = json.loads(out.stdout or b"{}")
    except (FFmpegError, ValueError):
        return None
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None and a is None:
        return None
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    try:
        start_time = float(fmt.get("start_time") or 0.0)
    except ValueError:
        start_time = 0.0
    # Fragmented MP4 segments with a non-zero tfdt (e.g. AVAssetWriter Mach-uptime PTS)
    # omit mvhd/mehd duration, so ffprobe reports end_pts (start_time + span) as duration.
    if (
        v is not None
        and not v.get("nb_frames")
        and math.isfinite(start_time)
        and start_time > 1.0
        and duration > start_time
    ):
        duration = duration - start_time
    fps = 0.0
    if v:
        num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
        try:
            fps = float(num) / float(den) if float(den or 0) else 0.0
        except ValueError:
            fps = 0.0
    return MediaInfo(
        duration=duration if math.isfinite(duration) and duration > 0 else 0.0,
        video_codec=v.get("codec_name") if v else None,
        width=int(v.get("width") or 0) if v else 0,
        height=int(v.get("height") or 0) if v else 0,
        fps=round(fps, 3),
        audio_codec=a.get("codec_name") if a else None,
    )
