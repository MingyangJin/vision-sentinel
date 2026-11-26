"""Live view and rolling-segment helpers for the Web UI (ARCHITECTURE §5.1, §5.5).

Everything here reads strictly from the Stream Hub's rolling segments or loopback MediaMTX
restream — never from a camera directly, and never returning camera URLs or credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vs_runtime.contracts import utcnow

from vs_streamhub import ffmpeg, segments
from vs_streamhub.slicer import WindowSlicer

if TYPE_CHECKING:
    from vs_streamhub.hub import StreamHub

MJPEG_BOUNDARY = "frame"
MJPEG_MEDIA_TYPE = f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}"
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"


@dataclass(frozen=True)
class SegmentInfo:
    name: str
    start: datetime
    end: datetime | None
    duration_sec: float | None
    size_bytes: int
    is_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start": self.start.isoformat(),
            "end": self.end.isoformat() if self.end else None,
            "duration_sec": round(self.duration_sec, 3) if self.duration_sec is not None else None,
            "size_bytes": self.size_bytes,
            "is_active": self.is_active,
        }


def format_mjpeg_frame(jpeg: bytes, boundary: str = MJPEG_BOUNDARY) -> bytes:
    header = (
        f"--{boundary}\r\n"
        f"Content-Type: image/jpeg\r\n"
        f"Content-Length: {len(jpeg)}\r\n\r\n"
    ).encode("ascii")
    return header + jpeg + b"\r\n"


def _pop_jpegs(buf: bytearray) -> list[bytes]:
    """Extract complete JPEG frames from a streaming MJPEG byte buffer."""
    frames: list[bytes] = []
    while True:
        start = buf.find(_JPEG_SOI)
        if start < 0:
            buf.clear()
            break
        if start > 0:
            del buf[:start]
        end = buf.find(_JPEG_EOI, 2)
        if end < 0:
            break
        frames.append(bytes(buf[: end + 2]))
        del buf[: end + 2]
    return frames


def _scale_filter(width: int | None) -> list[str]:
    return ["-vf", f"scale='min({int(width)},iw)':-2"] if width else []


def _remux_segment_fmp4(segment_path: Path, timeout: float = 10.0) -> bytes | None:
    """Zero-transcode copy of one segment into video-only fragmented MP4 for browser MSE."""
    try:
        if not segment_path.is_file() or segment_path.stat().st_size < 16:
            return None
        proc = ffmpeg.run(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
                "-i", str(segment_path),
                "-map", "0:v:0", "-c:v", "copy", "-an",
                "-f", "mp4",
                "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
                "pipe:1",
            ],
            timeout=timeout,
            check=False,
        )
        # A segment whose first fragment is not flushed yet remuxes to headers only (ftyp +
        # moov, no moof): that is "not ready", not media.
        if proc.returncode == 0 and proc.stdout[4:8] == b"ftyp" and b"moof" in proc.stdout:
            return proc.stdout
    except (OSError, ffmpeg.FFmpegError):
        return None
    return None


def _snapshot_from_segment(seg_path: Path, *, width: int | None, quality: int, timeout: float) -> bytes | None:
    """Copy a segment first (safe if actively written) and decode its latest frame as JPEG."""
    with tempfile.TemporaryDirectory(prefix="vs-snap-") as td:
        copy_path = Path(td) / "seg.mp4"
        try:
            shutil.copyfile(seg_path, copy_path)
        except FileNotFoundError:
            return None
        if copy_path.stat().st_size < 1024:
            return None
        for seek in (["-sseof", "-0.5"], []):
            try:
                proc = ffmpeg.run(
                    [
                        "ffmpeg", "-v", "error", *seek, "-i", str(copy_path),
                        "-frames:v", "1", *_scale_filter(width),
                        "-q:v", str(quality), "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                    ],
                    timeout=timeout,
                    check=True,
                )
            except ffmpeg.FFmpegError:
                continue
            if proc.stdout.startswith(_JPEG_SOI) and len(proc.stdout) > 100:
                return proc.stdout
    return None


class LiveStreamProvider:
    """Read-only live view, snapshot, segment listing, and clip extraction over a StreamHub."""

    def __init__(self, hub: StreamHub, *, snapshot_timeout_sec: float = 10.0):
        self.hub = hub
        self.snapshot_timeout_sec = snapshot_timeout_sec

    def list_cameras(self) -> list[dict[str, Any]]:
        """Credential-free camera summaries safe to return from a Web UI API."""
        out: list[dict[str, Any]] = []
        for cid, adapter in self.hub.adapters.items():
            h = adapter.health()
            segs = segments.list_segments(adapter.segment_dir)
            out.append({
                "camera_id": cid,
                "adapter": adapter.kind,
                "status": h.status,
                "checked_at": h.checked_at.isoformat(),
                "last_segment_at": h.last_segment_at.isoformat() if h.last_segment_at else None,
                "detail": h.detail,
                "speaker_enabled": adapter.config.speaker_enabled,
                "volume_cap": adapter.config.volume_cap,
                "segment_count": len(segs),
                "has_live_restream": bool(adapter.live_url),
            })
        return out

    def latest_snapshot(self, camera_id: str, *, width: int | None = 960, quality: int = 5) -> bytes | None:
        """Return a fresh JPEG frame for ``camera_id``, or None if no readable media exists yet.
        Raises KeyError for an unknown camera_id."""
        adapter = self.hub.adapter(camera_id)
        segs = segments.list_segments(adapter.segment_dir)
        # Try newest first, then previous (in case newest just rotated and has <1 fragment).
        for seg in reversed(segs[-3:]):
            frame = _snapshot_from_segment(seg.path, width=width, quality=quality, timeout=self.snapshot_timeout_sec)
            if frame is not None:
                return frame
        return None

    async def iter_mjpeg(
        self,
        camera_id: str,
        *,
        fps: float = 2.0,
        width: int | None = 960,
        quality: int = 5,
        max_frames: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Async generator yielding multipart MJPEG chunks (``MJPEG_MEDIA_TYPE``).

        Uses the local MediaMTX restream when running and ready; otherwise polls rolling
        segments safely via ``latest_snapshot``. Raises KeyError for an unknown camera_id.
        """
        adapter = self.hub.adapter(camera_id)
        clamped_fps = min(max(float(fps), 0.1), 15.0)
        sent = 0

        # Fast path: local MediaMTX restream (still 100% loopback, never direct camera).
        live_url = adapter.live_url
        if live_url and adapter.mediamtx and adapter.mediamtx_path and adapter.mediamtx.running:
            if await asyncio.to_thread(adapter.mediamtx.ready, adapter.mediamtx_path):
                async for chunk in self._iter_restream_mjpeg(
                    live_url, fps=clamped_fps, width=width, quality=quality, max_frames=max_frames
                ):
                    yield chunk
                    sent += 1
                    if max_frames is not None and sent >= max_frames:
                        return

        # Universal path: rolling segments via latest_snapshot (works for file replay & FFmpeg-direct).
        interval = 1.0 / clamped_fps
        while max_frames is None or sent < max_frames:
            t0 = time.monotonic()
            jpeg = await asyncio.to_thread(self.latest_snapshot, camera_id, width=width, quality=quality)
            if jpeg is not None:
                yield format_mjpeg_frame(jpeg)
                sent += 1
                if max_frames is not None and sent >= max_frames:
                    return
                wait = max(0.0, interval - (time.monotonic() - t0))
            else:
                wait = min(max(interval, 0.5), 2.0)
            await asyncio.sleep(wait)

    async def iter_fmp4(
        self,
        camera_id: str,
        *,
        max_chunks: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Async generator yielding a continuous video-only fragmented MP4 (H.264) byte stream
        at the camera's native frame rate with zero video transcoding (``-c:v copy``).

        - RTSP-backed cameras (MediaMTX ready): remuxes the loopback restream live (~0.5-1 s latency).
        - Segment-backed cameras (``macos_camera`` / ``http_ingest``, ``file_replay``, direct ``webcam``):
          remuxes each newly completed segment into a video-only fMP4 fragment as it arrives.
        Raises KeyError for an unknown camera_id.
        """
        adapter = self.hub.adapter(camera_id)
        sent = 0

        live_url = adapter.live_url
        if live_url and adapter.mediamtx and adapter.mediamtx_path and adapter.mediamtx.running:
            if await asyncio.to_thread(adapter.mediamtx.ready, adapter.mediamtx_path):
                async for chunk in self._iter_restream_fmp4(live_url, max_chunks=max_chunks):
                    yield chunk
                    sent += 1
                    if max_chunks is not None and sent >= max_chunks:
                        return
                return

        async for chunk in self._iter_segments_fmp4(
            adapter.segment_dir,
            max_chunks=max_chunks,
            newest_complete=bool(getattr(adapter, "segments_atomic", False)),
        ):
            yield chunk
            sent += 1
            if max_chunks is not None and sent >= max_chunks:
                return

    async def _iter_restream_fmp4(
        self,
        restream_url: str,
        *,
        max_chunks: int | None = None,
    ) -> AsyncIterator[bytes]:
        argv = [
            ffmpeg.tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error",
            "-rtsp_transport", "tcp", "-fflags", "+genpts+nobuffer",
            "-i", restream_url,
            "-map", "0:v:0", "-c:v", "copy", "-an",
            "-f", "mp4",
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof+faststart",
            "pipe:1",
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        sent = 0
        try:
            assert proc.stdout is not None
            while max_chunks is None or sent < max_chunks:
                chunk = await proc.stdout.read(32768)
                if not chunk:
                    break
                yield chunk
                sent += 1
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()

    async def _iter_segments_fmp4(
        self,
        segment_dir: Path,
        *,
        max_chunks: int | None = None,
        newest_complete: bool = False,
    ) -> AsyncIterator[bytes]:
        """Each *completed* segment, once, as its own fMP4 chunk. Unless ``newest_complete``
        (segments pushed whole), the newest file is still being written: sending it early would
        deliver a partial fragment and then skip the rest of that segment, freezing the picture.
        So a segment is sent only after a newer one exists (one segment of latency)."""
        sent = 0
        last_name: str | None = None
        while max_chunks is None or sent < max_chunks:
            segs = await asyncio.to_thread(segments.list_segments, segment_dir)
            ready = segs if newest_complete else segs[:-1]
            if last_name is None:
                newer = ready[-1:]  # start at the live edge
            else:
                newer = [s for s in ready if s.path.name > last_name]
                if len(newer) > 3:  # fell behind: jump to the live edge
                    newer = newer[-2:]
            if not newer:
                await asyncio.sleep(0.25)
                continue
            progressed = False
            for seg in newer:
                data = await asyncio.to_thread(_remux_segment_fmp4, seg.path, self.snapshot_timeout_sec)
                if data:
                    last_name = seg.path.name
                    progressed = True
                    yield data
                    sent += 1
                    if max_chunks is not None and sent >= max_chunks:
                        return
                elif seg is not newer[-1]:
                    last_name = seg.path.name  # unreadable and already superseded: skip it
                else:
                    break  # may still be finalizing; retry on the next poll
            if not progressed:
                await asyncio.sleep(0.25)

    async def _iter_restream_mjpeg(
        self,
        restream_url: str,
        *,
        fps: float,
        width: int | None,
        quality: int,
        max_frames: int | None,
    ) -> AsyncIterator[bytes]:
        vf = f"fps={fps:g}" + (f",scale='min({int(width)},iw)':-2" if width else "")
        argv = [
            ffmpeg.tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error",
            "-rtsp_transport", "tcp", "-i", restream_url,
            "-vf", vf, "-q:v", str(quality), "-f", "image2pipe", "-vcodec", "mjpeg", "-",
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        buf = bytearray()
        sent = 0
        try:
            assert proc.stdout is not None
            while max_frames is None or sent < max_frames:
                chunk = await proc.stdout.read(16384)
                if not chunk:
                    break
                buf.extend(chunk)
                for jpeg in _pop_jpegs(buf):
                    yield format_mjpeg_frame(jpeg)
                    sent += 1
                    if max_frames is not None and sent >= max_frames:
                        return
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    with contextlib.suppress(Exception):
                        await proc.wait()

    def list_segments(self, camera_id: str, *, limit: int | None = None) -> list[SegmentInfo]:
        """List rolling segments for ``camera_id`` (oldest first, optionally last ``limit``)."""
        adapter = self.hub.adapter(camera_id)
        segs = segments.list_segments(adapter.segment_dir)
        now = utcnow()
        infos: list[SegmentInfo] = []
        for idx, seg in enumerate(segs):
            try:
                st = seg.path.stat()
            except FileNotFoundError:
                continue
            mtime = datetime.fromtimestamp(st.st_mtime, tz=now.tzinfo)
            is_last = idx == len(segs) - 1
            end = segs[idx + 1].start if not is_last else max(mtime, seg.start)
            dur = max(0.0, (end - seg.start).total_seconds())
            is_active = is_last and (now - mtime).total_seconds() <= adapter.stale_sec
            infos.append(SegmentInfo(
                name=seg.path.name,
                start=seg.start,
                end=end,
                duration_sec=dur,
                size_bytes=st.st_size,
                is_active=is_active,
            ))
        return infos[-limit:] if limit is not None and limit > 0 else infos

    def segment_file(self, camera_id: str, segment_name: str) -> Path:
        """Path-confined lookup for one rolling segment file by exact filename."""
        adapter = self.hub.adapter(camera_id)
        if not segment_name or "/" in segment_name or "\\" in segment_name or ".." in segment_name:
            raise ValueError(f"invalid segment name {segment_name!r}")
        if segments.parse_start(segment_name) is None:
            raise ValueError(f"not a rolling segment filename: {segment_name!r}")
        root = adapter.segment_dir.resolve()
        candidate = (root / segment_name).resolve()
        if candidate.parent != root:
            raise ValueError(f"segment path escapes camera directory: {segment_name!r}")
        if not candidate.is_file():
            raise FileNotFoundError(f"segment {segment_name!r} not found for camera {camera_id!r}")
        return candidate

    def extract_clip(
        self,
        camera_id: str,
        start: datetime,
        end: datetime,
        out_path: Path | str,
        *,
        width: int = 960,
    ) -> Path | None:
        """Safely slice [start, end) from rolling segments into a browser-playable MP4 at ``out_path``.
        Returns the written Path, or None if no readable media covers the window."""
        adapter = self.hub.adapter(camera_id)
        dest = Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vs-clip-") as td:
            res = WindowSlicer(width=width).slice(
                camera_id, adapter.segment_dir, start, end, Path(td), kinds={"video"},
                stream_health=adapter.health(),
            )
            clip = Path(td) / "clip.mp4"
            if res.status == "offline" or not clip.is_file() or clip.stat().st_size == 0:
                return None
            tmp_dest = dest.with_name(f".{dest.name}.tmp")
            shutil.copyfile(clip, tmp_dest)
            tmp_dest.replace(dest)
        return dest
