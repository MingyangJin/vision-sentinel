"""Shared fixtures: tiny synthetic clips made with FFmpeg (no personal media, no camera)."""

from __future__ import annotations

import shutil
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_clip(path: Path, seconds: float = 6, audio: str | None = "sine=frequency=440:sample_rate=16000") -> Path:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=15:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "15"]
    cmd += ["-c:a", "aac", "-shortest"] if audio else []
    subprocess.run([*cmd, str(path)], check=True, capture_output=True)
    return path


def make_segments(clip: Path, directory: Path, *, seg_sec: int = 2, end: datetime | None = None,
                  gap_after: int | None = None, gap_sec: float = 0) -> list[Path]:  # fmt: skip
    """Split ``clip`` into fMP4 segments named by wall-clock start, the newest ending at ``end``."""
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / "_split"
    tmp.mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-c", "copy", "-f", "segment",
                    "-segment_time", str(seg_sec), "-reset_timestamps", "1", "-segment_format", "mp4",
                    "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
                    str(tmp / "%03d.mp4")], check=True, capture_output=True)  # fmt: skip
    parts = sorted(tmp.iterdir())
    durations = [_duration(p) for p in parts]
    end = end or datetime.now(timezone.utc)
    t = end - timedelta(seconds=sum(durations) + (gap_sec if gap_after is not None else 0))
    out = []
    for i, (p, d) in enumerate(zip(parts, durations)):
        name = t.astimezone().strftime("%Y-%m-%d_%H-%M-%S-%f") + ".mp4"  # MediaMTX-style names
        out.append(p.rename(directory / name))
        t += timedelta(seconds=d + (gap_sec if gap_after == i else 0))
    tmp.rmdir()
    return out


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)  # fmt: skip
    return float(out.stdout)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict[str, Path]:
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("media")
    return {
        "tone": make_clip(d / "tone.mp4", 12),
        "mute": make_clip(d / "mute.mp4", 12, audio=None),
        "silent": make_clip(d / "silent.mp4", 12, audio="anullsrc=r=16000:cl=mono"),
        "short": make_clip(d / "short.mp4", 4),
    }


@pytest.fixture
def segmenter():
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not installed")
    return make_segments


@pytest.fixture
def port():
    return free_port
