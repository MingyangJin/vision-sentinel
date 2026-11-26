"""Stream Hub & media helpers for the Web UI.

Strictly enforces "The One Rule" (ARCHITECTURE.md §5.1):
- Never opens a camera directly, never exposes camera source URLs, IP addresses, or credentials.
- All live snapshots, MJPEG streams, segments, and clips flow strictly through ``StreamHub``
  and its rolling buffer directory by logical ``camera_id``.
- All bundle and segment file lookups are strictly path-confined (rejecting traversal & symlink escapes).
"""

from __future__ import annotations

import asyncio
import inspect
import mimetypes
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from vs_runtime.contracts import StreamHealth, utcnow

SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.mp4$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_safe_id(value: str) -> bool:
    return bool(value) and bool(SAFE_ID.match(value)) and ".." not in value


def scrub_text(text: str) -> str:
    """Redact credentials if anything unexpected leaks into a health detail string."""
    try:
        from vs_streamhub.credentials import scrub_credentials

        return scrub_credentials(text)
    except Exception:
        return re.sub(r"(\w+://)[^/@\s]+@", r"\1***@", text)


def list_camera_ids(hub: Any) -> list[str]:
    if hub is None:
        return []
    if hasattr(hub, "camera_ids"):
        return list(hub.camera_ids)
    if hasattr(hub, "adapters") and isinstance(hub.adapters, dict):
        return list(hub.adapters.keys())
    return []


def camera_summary(hub: Any, camera_id: str) -> dict[str, Any]:
    """Return strictly sanitized camera metadata — zero URLs, IPs, or credentials."""
    health: StreamHealth = hub.health(camera_id)
    adapter_name = "unknown"
    speaker_enabled = False
    volume_cap = 0.3
    has_live_stream = False
    if hasattr(hub, "adapters") and camera_id in hub.adapters:
        adapter = hub.adapters[camera_id]
        cfg = getattr(adapter, "config", None)
        if cfg is not None:
            adapter_name = str(getattr(cfg, "adapter", "unknown"))
            speaker_enabled = bool(getattr(cfg, "speaker_enabled", False))
            volume_cap = float(getattr(cfg, "volume_cap", 0.3))
        has_live_stream = bool(getattr(adapter, "live_url", None))
    segs = list_camera_segments(hub, camera_id)
    return {
        "camera_id": camera_id,
        "adapter": adapter_name,
        "speaker_enabled": speaker_enabled,
        "volume_cap": volume_cap,
        "has_live_stream": has_live_stream or len(segs) > 0,
        "segment_count": len(segs),
        "health": {
            "camera_id": health.camera_id,
            "status": health.status,
            "checked_at": health.checked_at.isoformat(),
            "last_segment_at": health.last_segment_at.isoformat() if health.last_segment_at else None,
            "detail": scrub_text(health.detail),
        },
    }


def list_camera_segments(hub: Any, camera_id: str) -> list[dict[str, Any]]:
    """List rolling buffer segments for ``camera_id`` (newest last)."""
    if hub is None:
        return []
    # Prefer StreamHub's native M2 helper when available
    if hasattr(hub, "list_segments"):
        try:
            raw = hub.list_segments(camera_id)
            out: list[dict[str, Any]] = []
            for item in raw:
                if hasattr(item, "to_dict"):
                    d = item.to_dict()
                    out.append({
                        "name": d.get("name"),
                        "start": _iso(d.get("start")),
                        "end": _iso(d.get("end")),
                        "duration_sec": d.get("duration_sec"),
                        "size_bytes": d.get("size_bytes", 0),
                    })
                elif isinstance(item, dict):
                    name = item.get("name") or Path(str(item.get("path", ""))).name
                    out.append({
                        "name": name,
                        "start": _iso(item.get("start")),
                        "end": _iso(item.get("end")),
                        "duration_sec": item.get("duration_sec"),
                        "size_bytes": item.get("size_bytes", 0),
                    })
                else:
                    p = Path(getattr(item, "path"))
                    stat = p.stat() if p.exists() else None
                    out.append({
                        "name": p.name,
                        "start": _iso(getattr(item, "start", None)),
                        "end": _iso(getattr(item, "end", None)),
                        "duration_sec": getattr(item, "duration_sec", None),
                        "size_bytes": stat.st_size if stat else 0,
                    })
            return out
        except KeyError:
            raise
        except Exception:
            pass

    if not hasattr(hub, "segment_dir"):
        return []
    seg_dir = Path(hub.segment_dir(camera_id))
    if not seg_dir.is_dir():
        return []
    try:
        from vs_streamhub import segments as seg_mod

        segs = seg_mod.list_segments(seg_dir)
    except Exception:
        return []

    result: list[dict[str, Any]] = []
    for idx, seg in enumerate(segs):
        try:
            stat = seg.path.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        except OSError:
            continue
        end = segs[idx + 1].start if idx + 1 < len(segs) else mtime
        dur = max(0.0, round((end - seg.start).total_seconds(), 2))
        result.append({
            "name": seg.path.name,
            "start": seg.start.isoformat(),
            "end": end.isoformat(),
            "duration_sec": dur,
            "size_bytes": size,
        })
    return result


def resolve_segment_path(hub: Any, camera_id: str, segment_name: str) -> Path:
    """Safely resolve a rolling segment file within ``hub.segment_dir(camera_id)``."""
    if not is_safe_id(camera_id) or not SAFE_SEGMENT.match(segment_name) or ".." in segment_name:
        raise ValueError("invalid camera_id or segment name")
    if hub is None:
        raise FileNotFoundError("no segment storage")
    if hasattr(hub, "segment_file"):
        p = Path(hub.segment_file(camera_id, segment_name))
        if p.is_symlink():
            raise PermissionError("symlinks are not permitted")
        return p.resolve()
    if not hasattr(hub, "segment_dir"):
        raise FileNotFoundError("no segment storage")
    root = Path(hub.segment_dir(camera_id)).resolve()
    candidate = root / segment_name
    if candidate.is_symlink():
        raise PermissionError("symlinks are not permitted")
    resolved = candidate.resolve()
    if resolved.parent != root or not resolved.is_file():
        raise FileNotFoundError(f"segment {segment_name!r} not found")
    return resolved


def latest_snapshot(hub: Any, camera_id: str) -> bytes | None:
    """Fetch a fresh JPEG snapshot strictly through StreamHub (never camera URLs)."""
    if hub is None:
        return None
    if hasattr(hub, "latest_snapshot"):
        data = hub.latest_snapshot(camera_id)
        if data:
            return bytes(data)
    if not hasattr(hub, "segment_dir"):
        return None
    seg_dir = Path(hub.segment_dir(camera_id))
    if not seg_dir.is_dir():
        return None
    try:
        from vs_streamhub import ffmpeg, segments as seg_mod

        segs = seg_mod.list_segments(seg_dir)
        if not segs:
            return None
        # Try newest segment first, then previous (newest may still be opening)
        for seg in reversed(segs[-2:]):
            with tempfile.TemporaryDirectory(prefix="vs-snap-") as td:
                tmp_seg = Path(td) / "seg.mp4"
                out_jpg = Path(td) / "frame.jpg"
                try:
                    shutil.copyfile(seg.path, tmp_seg)
                except OSError:
                    continue
                if tmp_seg.stat().st_size < 512:
                    continue
                proc = ffmpeg.run(
                    [
                        "ffmpeg", "-y", "-v", "error", "-sseof", "-0.5",
                        "-i", str(tmp_seg), "-frames:v", "1", "-q:v", "4",
                        "-update", "1", str(out_jpg),
                    ],
                    timeout=10.0,
                    check=False,
                )
                if proc.returncode == 0 and out_jpg.exists() and out_jpg.stat().st_size > 0:
                    return out_jpg.read_bytes()
    except Exception:
        return None
    return None


async def iter_fmp4_stream(
    hub: Any,
    camera_id: str,
    *,
    max_chunks: int | None = None,
) -> AsyncIterator[bytes]:
    """Yield continuous video-only fragmented MP4 bytes at native camera FPS via StreamHub."""
    if hub is not None and hasattr(hub, "iter_fmp4"):
        raw_iter = hub.iter_fmp4(camera_id, max_chunks=max_chunks)
        count = 0
        if inspect.isasyncgen(raw_iter) or hasattr(raw_iter, "__aiter__"):
            async for chunk in raw_iter:
                yield chunk
                count += 1
                if max_chunks is not None and count >= max_chunks:
                    return
            return


async def iter_mjpeg_frames(
    hub: Any,
    camera_id: str,
    *,
    fps: float = 2.0,
    max_frames: int | None = None,
) -> AsyncIterator[bytes]:
    """Yield multipart/x-mixed-replace MJPEG chunks via StreamHub."""
    interval = 1.0 / max(0.2, min(fps, 15.0))
    if hub is not None and hasattr(hub, "iter_mjpeg"):
        raw_iter = hub.iter_mjpeg(camera_id, fps=fps)
        count = 0
        if inspect.isasyncgen(raw_iter) or hasattr(raw_iter, "__aiter__"):
            async for chunk in raw_iter:
                yield _ensure_mjpeg_part(chunk)
                count += 1
                if max_frames is not None and count >= max_frames:
                    return
            return
        for chunk in raw_iter:
            yield _ensure_mjpeg_part(chunk)
            count += 1
            if max_frames is not None and count >= max_frames:
                return
            await asyncio.sleep(0)
        return

    count = 0
    while True:
        frame = await asyncio.to_thread(latest_snapshot, hub, camera_id)
        if frame:
            yield _format_mjpeg_part(frame)
            count += 1
            if max_frames is not None and count >= max_frames:
                return
        else:
            if max_frames is not None:
                return
        await asyncio.sleep(interval)


def _format_mjpeg_part(jpeg: bytes) -> bytes:
    header = (
        f"--frame\r\n"
        f"Content-Type: image/jpeg\r\n"
        f"Content-Length: {len(jpeg)}\r\n\r\n"
    ).encode("ascii")
    return header + jpeg + b"\r\n"


def _ensure_mjpeg_part(chunk: bytes) -> bytes:
    if chunk.startswith(b"--"):
        return chunk
    return _format_mjpeg_part(chunk)


def extract_camera_clip(
    hub: Any,
    camera_id: str,
    start: datetime,
    end: datetime,
    out_dir: Path,
) -> Path | None:
    """Slice an MP4 clip for [start, end) through StreamHub into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "clip.mp4"
    if hasattr(hub, "extract_clip"):
        res = hub.extract_clip(camera_id, start, end, out_path)
        return Path(res) if res and Path(res).is_file() else None
    if hasattr(hub, "extract_window"):
        assets = hub.extract_window(camera_id, start, end, out_dir, {"video"})
        for a in assets:
            p = (out_dir / a.path).resolve()
            if p.is_relative_to(out_dir.resolve()) and p.is_file():
                return p
    return out_path if out_path.is_file() else None


def resolve_bundle_asset(topic_dir: Path, bundle_id: str, asset_rel: str) -> Path:
    """Strictly confine bundle media lookups to ``state/bundles/<id>`` or ``state/saved/<id>``.

    Rejects:
    - invalid/traversal bundle_ids (``..``, ``/``, leading ``.``)
    - absolute asset paths, null bytes, ``..`` segments
    - symlinks that resolve outside the bundle directory
    """
    if not is_safe_id(bundle_id):
        raise PermissionError(f"invalid bundle_id: {bundle_id!r}")
    if not asset_rel or "\x00" in asset_rel or asset_rel.startswith(("/", "\\")):
        raise PermissionError(f"invalid asset path: {asset_rel!r}")
    parts = Path(asset_rel).parts
    if any(p in ("..", "") for p in parts):
        raise PermissionError(f"path traversal denied: {asset_rel!r}")

    state = Path(topic_dir).resolve() / "state"
    for parent_name in ("saved", "bundles"):
        root = (state / parent_name).resolve()
        bdir = (root / bundle_id).resolve()
        if bdir.parent != root or not bdir.is_dir():
            continue
        candidate = bdir / asset_rel
        resolved = candidate.resolve()
        if not resolved.is_relative_to(bdir):
            raise PermissionError(f"path escape denied: {asset_rel!r}")
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(f"asset {asset_rel!r} not found in bundle {bundle_id!r}")


def guess_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".mp4":
        return "video/mp4"
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".mp3":
        return "audio/mpeg"
    if ext == ".json":
        return "application/json"
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _iso(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def default_clip_window(seconds: float = 30.0) -> tuple[datetime, datetime]:
    end = utcnow()
    return end - timedelta(seconds=max(1.0, min(seconds, 600.0))), end
