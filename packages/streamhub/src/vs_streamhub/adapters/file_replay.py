"""Local file / folder loop replay: a camera with no hardware (ARCHITECTURE §5.1, item 5).

Loops one ``.mp4`` (or every video in a folder, in name order) in real time into the
camera's rolling segment directory. By default FFmpeg writes the segments itself, so no
daemon is needed; with ``options.ingest = "mediamtx"`` it publishes to the hub's MediaMTX,
which records the segments and serves a live restream.

Options: ``segment_sec``, ``ingest`` ("direct" | "mediamtx"), ``transcode`` (default
true: H.264 + AAC with a keyframe every second, so segments cut cleanly for any input).
"""

from __future__ import annotations

from pathlib import Path

from vs_runtime.contracts import StreamHealth

from vs_streamhub import ffmpeg
from vs_streamhub.adapters.base import BaseAdapter, ffmpeg_base, segment_args

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".m4v"}


class FileReplayAdapter(BaseAdapter):
    kind = "file"

    @property
    def wants_mediamtx(self) -> bool:
        return self.config.options.get("ingest", "direct") == "mediamtx"

    def files(self) -> list[Path]:
        src = Path(self.config.source).expanduser()
        if src.is_dir():
            return sorted(p for p in src.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
        return [src] if src.is_file() else []

    def _input_args(self) -> list[str]:
        files = self.files()
        if not files:
            raise FileNotFoundError(f"no replay media at {self.config.source!r}")
        if len(files) == 1:
            return ["-re", "-stream_loop", "-1", "-i", str(files[0].resolve())]
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        listing = self.segment_dir / ".replay.ffconcat"  # not *.mp4, so never taken for a segment
        listing.write_text("ffconcat version 1.0\n" + "".join(
            "file '{}'\n".format(str(p.resolve()).replace("'", "'\\''")) for p in files))  # fmt: skip
        return ["-re", "-stream_loop", "-1", "-f", "concat", "-safe", "0", "-i", str(listing)]

    def _codec_args(self) -> list[str]:
        maps = ["-map", "0:v:0", "-map", "0:a:0?"]
        if not self.config.options.get("transcode", True):
            return [*maps, "-c", "copy"]
        return [*maps, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-force_key_frames", "expr:gte(t,n_forced*1)", "-c:a", "aac", "-ac", "1"]  # fmt: skip

    def writer_command(self) -> tuple[list[str], dict[str, str] | None]:
        argv = [*ffmpeg_base(), *self._input_args(), *self._codec_args()]
        if self.ingest == "mediamtx" and self.mediamtx and self.mediamtx_path:
            argv += ["-f", "rtsp", "-rtsp_transport", "tcp", self.mediamtx.publish_url(self.mediamtx_path)]
        else:
            argv += segment_args(self.segment_dir, self.segment_sec)
        return argv, None

    def probe(self) -> StreamHealth:
        files = self.files()
        if not files:
            return StreamHealth(camera_id=self.camera_id, status="offline", detail=f"no replay media at {self.config.source}")
        info = ffmpeg.probe(files[0])
        if info is None or info.video_codec is None:
            return StreamHealth(camera_id=self.camera_id, status="offline", detail=f"{files[0].name}: no readable video stream")
        audio = info.audio_codec or "no audio"
        return StreamHealth(camera_id=self.camera_id, status="ok", detail=(
            f"{len(files)} file(s); {info.video_codec} {info.width}x{info.height} @ {info.fps:g} fps, {audio}"))  # fmt: skip

    def describe(self) -> dict:
        return {**super().describe(), "source": self.config.source, "files": len(self.files()),
                "segment_sec": self.segment_sec}  # fmt: skip
