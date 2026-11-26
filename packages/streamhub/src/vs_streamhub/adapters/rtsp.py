"""Generic RTSP / RTSPS camera: one pull from the camera, ever (ARCHITECTURE §5.1).

``source`` is the camera URL WITHOUT credentials (``rtsp://192.0.2.10:554/stream1``);
``credential_ref`` names the secret (see ``vs_streamhub.credentials``). The URL with
credentials is built only when a process is launched and goes to:

- MediaMTX (default when installed): through its environment
  (``MTX_PATHS_<PATH>_SOURCE``), never argv or a file. MediaMTX holds the single camera
  session, records the rolling segments, reconnects, and serves the local restream.
- FFmpeg (fallback, or ``options.ingest = "ffmpeg"``): a supervised single-pull writer that
  copies video, transcodes audio to AAC, and restarts with backoff on exit or stall. FFmpeg
  only accepts its input URL in argv, which other processes of the same user can read.

Options: ``ingest`` ("auto" | "mediamtx" | "ffmpeg"), ``segment_sec``, ``transport``
("tcp" | "udp"), ``timeout_sec``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from vs_runtime.contracts import StreamHealth

from vs_streamhub import ffmpeg
from vs_streamhub.adapters.base import BaseAdapter, SpeakerUnsupportedError, ffmpeg_base, segment_args
from vs_streamhub.credentials import CredentialError, redact_url, resolve_secret, scrub_credentials, with_credentials


class RtspAdapter(BaseAdapter):
    kind = "rtsp"

    def __init__(self, config, segment_dir, **kw):
        super().__init__(config, segment_dir, **kw)
        if not config.source.lower().startswith(("rtsp://", "rtsps://")):
            raise ValueError(f"camera {config.camera_id!r}: rtsp source must start with rtsp:// or rtsps://")
        if "@" in config.source.partition("://")[2].partition("/")[0]:
            raise ValueError(f"camera {config.camera_id!r}: put credentials in credential_ref, not in source")

    @property
    def wants_mediamtx(self) -> bool:
        return self.config.options.get("ingest", "auto") in ("auto", "mediamtx")

    def needs_writer(self) -> bool:
        return self.ingest == "ffmpeg"

    def mediamtx_source(self) -> Callable[[], str] | None:
        return self.camera_url

    def camera_url(self) -> str:
        """The URL with credentials. Only process launchers call this; never log it."""
        ref = self.config.credential_ref
        return with_credentials(self.config.source, resolve_secret(ref)) if ref else self.config.source

    def _play_alert(self, audio: Path, volume: float) -> None:
        """Optional RTSP/FFmpeg audio backchannel push when ``options.speaker_backchannel_url`` is set."""
        target = self.config.options.get("speaker_backchannel_url")
        if not target:
            raise SpeakerUnsupportedError(f"camera {self.camera_id!r} has no speaker backchannel configured")
        if not audio.is_file():
            raise FileNotFoundError(f"alert audio not found: {audio}")
        ref = self.config.credential_ref
        url = with_credentials(str(target), resolve_secret(ref)) if ref else str(target)
        codec = str(self.config.options.get("speaker_codec", "pcm_mulaw"))
        rate = str(int(self.config.options.get("speaker_sample_rate", 8000)))
        ffmpeg.run(
            [
                "ffmpeg", "-y", "-v", "error", "-re", "-i", str(audio),
                "-vn", "-af", f"volume={volume:.3f}", "-ac", "1", "-ar", rate,
                "-c:a", codec, "-f", "rtsp", "-rtsp_transport", self.config.options.get("transport", "tcp"), url,
            ],
            timeout=float(self.config.options.get("speaker_timeout_sec", 30.0)),
        )

    def _input_args(self) -> list[str]:
        timeout_us = int(float(self.config.options.get("timeout_sec", 10)) * 1_000_000)
        return ["-rtsp_transport", self.config.options.get("transport", "tcp"), "-timeout", str(timeout_us)]

    def writer_command(self) -> tuple[list[str], dict[str, str] | None]:
        argv = [*ffmpeg_base(), *self._input_args(), "-i", self.camera_url(),
                "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-ac", "1",
                *segment_args(self.segment_dir, self.segment_sec)]  # fmt: skip
        return argv, None

    def probe(self) -> StreamHealth:
        try:
            url = self.camera_url()
        except CredentialError as e:
            return StreamHealth(camera_id=self.camera_id, status="offline", detail=str(e))
        info = ffmpeg.probe(url, timeout=float(self.config.options.get("timeout_sec", 10)) + 5,
                            extra=self._input_args())  # fmt: skip
        if info is None or info.video_codec is None:
            return StreamHealth(camera_id=self.camera_id, status="offline",
                                detail=f"no video from {redact_url(self.config.source)}")  # fmt: skip
        return StreamHealth(camera_id=self.camera_id, status="ok", detail=scrub_credentials(
            f"{info.video_codec} {info.width}x{info.height} @ {info.fps:g} fps, {info.audio_codec or 'no audio'}"))  # fmt: skip

    def describe(self) -> dict:
        source = redact_url(self.config.source)
        if self.config.credential_ref:
            scheme, _, rest = source.partition("://")
            source = f"{scheme}://***@{rest}"
        return {**super().describe(), "source": source, "credential_ref": self.config.credential_ref,
                "transport": self.config.options.get("transport", "tcp")}  # fmt: skip
