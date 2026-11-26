"""Window slicing: the latest (or any) wall-clock window of a camera's rolling buffer
turned into Observation Bundle assets (ARCHITECTURE §4 step 2, §5.2).

Segments are copied before they are read, so the segment still being written is only
ever read as a snapshot and never modified. Outputs, all in ``out_dir``:

- ``clip.mp4``           the window, H.264 (+ AAC mono 16 kHz when there is audio)
- ``contact_sheet.jpg``  a cols x rows grid of evenly spaced frames
- ``frame.jpg``          the latest frame of the window
- ``audio.mp3``          16 kHz mono MP3; skipped without an audio track or on digital silence
- ``meta.json``          window, coverage, segments, audio RMS/peak dBFS, camera status
"""

from __future__ import annotations

import array
import json
import math
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vs_runtime.contracts import AssetKind, CameraStatus, ObservationAsset, StreamHealth, utcnow

from vs_streamhub import ffmpeg, segments
from vs_streamhub.credentials import scrub_credentials

ALL_KINDS: frozenset[AssetKind] = frozenset({"video", "contact_sheet", "frame", "audio", "telemetry"})
FILES = {"video": ("clip.mp4", "video/mp4"), "contact_sheet": ("contact_sheet.jpg", "image/jpeg"),
         "frame": ("frame.jpg", "image/jpeg"), "audio": ("audio.mp3", "audio/mpeg"),
         "telemetry": ("meta.json", "application/json")}  # fmt: skip


@dataclass
class SliceResult:
    assets: list[ObservationAsset]
    meta: dict
    status: CameraStatus = "offline"


@dataclass
class WindowSlicer:
    width: int = 640  # clip and sheet tiles are scaled down to this width at most
    fps: float | None = None  # None keeps the source frame rate
    sheet_cols: int = 3
    sheet_rows: int = 2
    silence_dbfs: float = -90.0  # peak at or below this is treated as no audio
    min_coverage: float = 0.8  # covered/requested below this marks the camera degraded ...
    lag_allowance_sec: float = 5.0  # ... unless the shortfall is within the writer's normal lag
    gap_tolerance_sec: float = 2.0
    timeout_sec: float = 120.0
    _stamp: bool = field(default=False, init=False)

    def slice(self, camera_id: str, segment_dir: Path, start: datetime, end: datetime, out_dir: Path,
              kinds: set[AssetKind] | frozenset[AssetKind] = ALL_KINDS,
              stream_health: StreamHealth | None = None) -> SliceResult:  # fmt: skip
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        start, end = _aware(start), _aware(end)
        requested = max((end - start).total_seconds(), 0.0)
        meta: dict = {"camera_id": camera_id, "status": "offline", "created_at": utcnow().isoformat(),
                      "requested": {"start": start.isoformat(), "end": end.isoformat(), "sec": round(requested, 3)},
                      "covered": None, "segment_count": 0, "segments": [], "gaps_sec": [],
                      "newest_segment_age_sec": None, "video": None,
                      "audio": {"present": False, "rms_dbfs": None, "peak_dbfs": None, "written": False},
                      "assets": [], "detail": "",
                      "stream_health": stream_health.model_dump(mode="json") if stream_health else None}  # fmt: skip
        all_segs = segments.list_segments(Path(segment_dir))
        if all_segs:
            newest = segments.newest_write(Path(segment_dir))
            meta["newest_segment_age_sec"] = round((utcnow() - newest).total_seconds(), 1) if newest else None
        # Segments overlapping [start, end): each runs until the next one starts (the newest
        # until now). One extra earlier segment covers filename-timestamp imprecision.
        picked = [s for s, nxt in zip(all_segs, [*all_segs[1:], None])
                  if s.start < end and (nxt is None or nxt.start > start)]  # fmt: skip
        if picked and all_segs.index(picked[0]) > 0 and picked[0].start > start:
            picked.insert(0, all_segs[all_segs.index(picked[0]) - 1])
        try:
            with tempfile.TemporaryDirectory(prefix="vs-slice-") as td:
                result = self._build(camera_id, picked, start, end, requested, Path(td), out_dir, kinds, meta)
        except ffmpeg.FFmpegError as e:
            meta["detail"] = scrub_credentials(str(e))
            meta["status"] = "degraded" if meta.get("covered") else "offline"
            result = SliceResult([], meta, meta["status"])
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        if "telemetry" in kinds:
            result.assets.append(ObservationAsset(camera_id=camera_id, kind="telemetry", path="meta.json",
                                                  start=start, end=end, mime="application/json"))  # fmt: skip
        return result

    # -----------------------------------------------------------------------------
    def _build(self, camera_id, picked, start, end, requested, tmp: Path, out_dir: Path, kinds, meta) -> SliceResult:
        copies: list[tuple[segments.Segment, Path, float]] = []
        for i, seg in enumerate(picked):
            dst = tmp / f"seg_{i:04d}.mp4"
            try:
                shutil.copyfile(seg.path, dst)  # snapshot; the writer keeps its file
            except FileNotFoundError:  # pruned meanwhile
                continue
            info = ffmpeg.probe(dst) if dst.stat().st_size > 1024 else None
            if info and info.duration > 0 and info.video_codec:
                copies.append((seg, dst, info.duration))
        if not copies:
            meta["detail"] = "no readable segments in window" if picked else "no segments in window"
            return SliceResult([], meta, "offline")

        # Wall-clock placement: each copy starts at its filename time; the stitched clip is
        # aligned on the end of the newest data, which is what a latest-window read wants.
        gaps = []
        for (a, _, da), (b, _, _) in zip(copies, copies[1:]):
            gap = (b.start - a.start).total_seconds() - da
            if gap > self.gap_tolerance_sec:
                gaps.append(round(gap, 1))
        total = sum(d for _, _, d in copies)
        data_end = copies[-1][0].start + timedelta(seconds=copies[-1][2])
        data_start = data_end - timedelta(seconds=total)
        cover_start, cover_end = max(start, data_start), min(end, data_end)
        covered = (cover_end - cover_start).total_seconds()
        meta.update(segment_count=len(copies), segments=[s.path.name for s, _, _ in copies], gaps_sec=gaps)
        if covered <= 0.5:
            meta["detail"] = "no media inside the requested window"
            return SliceResult([], meta, "offline")
        meta["covered"] = {"start": cover_start.isoformat(), "end": cover_end.isoformat(), "sec": round(covered, 3)}

        if len(copies) == 1:
            stitched = copies[0][1]
        else:
            listing = tmp / "concat.txt"
            listing.write_text("ffconcat version 1.0\n" + "".join(f"file '{p.name}'\n" for _, p, _ in copies))
            stitched = tmp / "stitched.mp4"
            ffmpeg.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-c", "copy", str(stitched)], timeout=self.timeout_sec)  # fmt: skip
        offset = (cover_start - data_start).total_seconds()
        src = ffmpeg.probe(stitched)
        has_audio = bool(src and src.has_audio)

        clip = out_dir / "clip.mp4" if "video" in kinds else tmp / "clip.mp4"
        vf = f"scale='min({self.width},iw)':-2" + (f",fps={self.fps:g}" if self.fps else "")
        ffmpeg.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{offset:.3f}", "-i", str(stitched), "-t", f"{covered:.3f}",
                    "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1", "-ar", "16000", "-b:a", "48k",
                    "-movflags", "+faststart", str(clip)], timeout=self.timeout_sec)  # fmt: skip
        info = ffmpeg.probe(clip)
        clip_dur = info.duration if info else covered
        if info:
            meta["video"] = {"codec": info.video_codec, "width": info.width, "height": info.height,
                             "fps": info.fps, "duration_sec": round(info.duration, 3)}  # fmt: skip

        assets: list[ObservationAsset] = []

        def add(kind: AssetKind) -> None:
            name, mime = FILES[kind]
            assets.append(ObservationAsset(camera_id=camera_id, kind=kind, path=name,
                                           start=cover_start, end=cover_end, mime=mime))  # fmt: skip
            meta["assets"].append(name)

        if "video" in kinds:
            add("video")
        if "contact_sheet" in kinds and self._contact_sheet(clip, clip_dur, out_dir / "contact_sheet.jpg", cover_start):
            add("contact_sheet")
        if "frame" in kinds and self._last_frame(clip, out_dir / "frame.jpg"):
            add("frame")
        if has_audio:
            meta["audio"].update(present=True, **_levels(clip, self.timeout_sec))
            peak = meta["audio"]["peak_dbfs"]
            if peak is None or peak <= self.silence_dbfs:
                meta["audio"]["skipped_reason"] = "digital silence"
            elif "audio" in kinds:
                ffmpeg.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vn", "-c:a", "libmp3lame",
                            "-ac", "1", "-ar", "16000", "-b:a", "48k", str(out_dir / "audio.mp3")],
                           timeout=self.timeout_sec)  # fmt: skip
                meta["audio"]["written"] = True
                add("audio")
        else:
            meta["audio"]["skipped_reason"] = "no audio track"

        degraded = []
        if covered < self.min_coverage * requested and requested - covered > self.lag_allowance_sec:
            degraded.append(f"covered {covered:.1f}s of {requested:.1f}s")
        if gaps:
            degraded.append(f"gaps {gaps}")
        meta["status"] = "degraded" if degraded else "ok"
        meta["detail"] = "; ".join(degraded)
        return SliceResult(assets, meta, meta["status"])

    def _contact_sheet(self, clip: Path, duration: float, out: Path, t0: datetime) -> bool:
        n = self.sheet_cols * self.sheet_rows
        tile_w = min(self.width // 2, 480)
        # Sample n frames evenly; tile pads with black if the clip yields fewer.
        rate = n / max(duration, 0.1)
        base = f"fps={rate:.6f}:start_time=0,scale={tile_w}:-2"
        stamp = ""
        if ffmpeg.has_filter("drawtext"):
            epoch = int(t0.timestamp())
            stamp = (f",drawtext=text='%{{pts\\:localtime\\:{epoch}\\:%H\\\\\\:%M\\\\\\:%S}}':x=8:y=8:"
                     "fontsize=18:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4")  # fmt: skip
        for chain in ([base + stamp] if stamp else []) + [base]:
            vf = f"{chain},tile={self.sheet_cols}x{self.sheet_rows}"
            try:
                ffmpeg.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-frames:v", "1", "-vf", vf,
                            "-q:v", "3", "-update", "1", str(out)], timeout=self.timeout_sec)  # fmt: skip
            except ffmpeg.FFmpegError:
                continue
            if out.exists() and out.stat().st_size > 0:
                return True
        return False

    def _last_frame(self, clip: Path, out: Path) -> bool:
        try:
            ffmpeg.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.5", "-i", str(clip), "-frames:v", "1",
                        "-q:v", "3", "-update", "1", str(out)], timeout=self.timeout_sec)  # fmt: skip
        except ffmpeg.FFmpegError:
            return False
        return out.exists() and out.stat().st_size > 0


def _levels(media: Path, timeout: float) -> dict:
    """Overall RMS and peak level in dBFS of the first audio track (16 kHz mono)."""
    proc = ffmpeg.run(["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000",
                       "-f", "s16le", "-"], timeout=timeout, check=False)  # fmt: skip
    pcm = array.array("h")
    pcm.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    if sys.byteorder == "big":
        pcm.byteswap()
    if not pcm:
        return {"rms_dbfs": None, "peak_dbfs": None}
    peak = max(max(pcm), -min(pcm)) / 32768.0
    rms = math.sqrt(math.fsum(s * s for s in pcm) / len(pcm)) / 32768.0
    db = lambda x: round(20 * math.log10(x), 1) if x > 0 else -120.0  # noqa: E731
    return {"rms_dbfs": db(rms), "peak_dbfs": db(peak)}


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.astimezone(timezone.utc)
