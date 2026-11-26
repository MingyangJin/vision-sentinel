"""The rolling segment directory of one camera: ``<recordings_root>/<camera_id>/*.mp4``.

Segment names carry their local wall-clock start time, as both writers produce them:
FFmpeg's segment muxer (``%Y-%m-%d_%H-%M-%S.mp4``) and MediaMTX
(``%Y-%m-%d_%H-%M-%S-%f.mp4``). The newest segment is the one still being written:
readers only copy it, and pruning never deletes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_NAME = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:-(\d{1,6}))?\.mp4$")
FFMPEG_PATTERN = "%Y-%m-%d_%H-%M-%S.mp4"
MEDIAMTX_PATTERN = "%Y-%m-%d_%H-%M-%S-%f"  # MediaMTX appends the extension


@dataclass(frozen=True)
class Segment:
    path: Path
    start: datetime  # tz-aware

    @property
    def mtime(self) -> datetime:
        return datetime.fromtimestamp(self.path.stat().st_mtime, timezone.utc)


def parse_start(name: str) -> datetime | None:
    m = _NAME.search(name)
    if not m:
        return None
    t = datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S")
    if m.group(2):
        t = t.replace(microsecond=int(m.group(2).ljust(6, "0")))
    return t.astimezone(timezone.utc)  # naive = local time


def list_segments(directory: Path) -> list[Segment]:
    if not directory.is_dir():
        return []
    segs = []
    for p in directory.iterdir():
        start = parse_start(p.name) if p.is_file() else None
        if start is not None:
            segs.append(Segment(p, start))
    return sorted(segs, key=lambda s: s.start)


def newest_write(directory: Path) -> datetime | None:
    """When the newest segment was last written to (its mtime), the liveness signal."""
    segs = list_segments(directory)
    if not segs:
        return None
    try:
        return max(s.mtime for s in segs[-2:])
    except FileNotFoundError:
        return None


def prune(directory: Path, older_than: datetime) -> list[Path]:
    """Delete segments that ended before ``older_than``; never the newest one."""
    segs = list_segments(directory)
    removed = []
    for seg, nxt in zip(segs, segs[1:]):  # a segment ends where the next begins
        if nxt.start < older_than:
            seg.path.unlink(missing_ok=True)
            removed.append(seg.path)
    return removed
