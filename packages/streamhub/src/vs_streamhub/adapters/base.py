"""Adapter base: lifecycle, supervised FFmpeg writer with reconnect, health, speaker guard."""

from __future__ import annotations

import collections
import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from vs_runtime.contracts import CameraConfig, StreamHealth, utcnow

from vs_streamhub import segments
from vs_streamhub.credentials import remember_address, scrub_addresses, scrub_credentials
from vs_streamhub.ffmpeg import tool

if TYPE_CHECKING:
    from vs_streamhub.mediamtx import MediaMTXServer

log = logging.getLogger("vs_streamhub")


class SpeakerDisabledError(PermissionError):
    """play_alert was called on a camera or topic whose speaker output is not opted in."""


class SpeakerUnsupportedError(NotImplementedError):
    """Speaker output was opted in, but this adapter/camera has no speaker backend."""


def segment_args(directory: Path, segment_sec: float) -> list[str]:
    """FFmpeg output args for the rolling fMP4 buffer: one file per ~segment_sec,
    named by wall-clock start, fragmented so a reader can copy the open one."""
    return [
        "-f", "segment", "-segment_time", f"{segment_sec:g}", "-reset_timestamps", "1",
        "-strftime", "1", "-segment_format", "mp4",
        "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof:flush_packets=1",
        str(directory / segments.FFMPEG_PATTERN),
    ]  # fmt: skip


class Supervisor:
    """Keeps one subprocess running: restarts it with backoff when it exits, and kills it
    when ``is_stalled()`` says its output stopped. Log lines are scrubbed."""

    def __init__(self, name: str, command: Callable[[], tuple[list[str], dict[str, str] | None]],
                 *, is_stalled: Callable[[float], bool] | None = None, max_backoff: float = 30.0,
                 cwd: Path | None = None):  # fmt: skip
        self.name = name
        self.command = command
        self.is_stalled = is_stalled
        self.max_backoff = max_backoff
        self.cwd = cwd
        self.restarts = 0
        self.last_error = ""
        self.running = False
        self._proc: subprocess.Popen[bytes] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tail: collections.deque[str] = collections.deque(maxlen=20)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"vs-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._kill()
        if self._thread:
            self._thread.join(timeout)
        self.running = False

    def _kill(self) -> None:
        p = self._proc
        if p and p.poll() is None:
            p.terminate()  # ffmpeg finalizes the open segment on SIGTERM
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()

    def _loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                argv, env = self.command()
                self._proc = subprocess.Popen(argv, env=env, cwd=self.cwd, stdin=subprocess.DEVNULL,
                                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)  # fmt: skip
            except Exception as e:  # missing binary, unresolved credential, ...
                self.last_error = scrub_credentials(str(e))
                log.warning("%s: cannot start: %s", self.name, self.last_error)
            else:
                self.running = True
                reader = threading.Thread(target=self._drain, args=(self._proc,), daemon=True)
                reader.start()
                while self._proc.poll() is None and not self._stop.wait(1.0):
                    if self.is_stalled and self.is_stalled(time.monotonic() - started):
                        self.last_error = "output stalled; restarting"
                        log.warning("%s: %s", self.name, self.last_error)
                        self._kill()
                reader.join(2)
                self.running = False
                code = self._proc.returncode
                if self._stop.is_set():
                    break
                if self._tail:
                    self.last_error = f"exited {code}: {self._tail[-1]}"
                elif self.last_error != "output stalled; restarting":
                    self.last_error = f"exited {code}"
                log.warning("%s: %s", self.name, self.last_error)
            if time.monotonic() - started > 60:
                backoff = 1.0
            self.restarts += 1
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, self.max_backoff)

    def _drain(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None  # stderr is merged into stdout
        for raw in proc.stdout:
            line = scrub_credentials(raw.decode(errors="replace").strip())
            if line:
                self._tail.append(line)
                log.debug("%s: %s", self.name, line)


class BaseAdapter:
    """One camera publishing into its rolling segment directory.

    ``ingest`` is ``"ffmpeg"`` (this adapter runs its own supervised FFmpeg writer) or
    ``"mediamtx"`` (the hub's MediaMTX pulls/records; set via ``attach_mediamtx``).
    """

    kind = "base"
    # True when every file in the segment directory is complete the moment it appears (pushed
    # whole and renamed into place). False when a writer is still appending to the newest one.
    segments_atomic = False

    def __init__(self, config: CameraConfig, segment_dir: Path, *, segment_sec: float = 2.0,
                 stale_sec: float | None = None, offline_sec: float | None = None):  # fmt: skip
        self.config = config
        self.camera_id = config.camera_id
        self.segment_dir = Path(segment_dir)
        self.segment_sec = max(1.0, float(config.options.get("segment_sec", segment_sec)))
        self.stale_sec = stale_sec or max(3 * self.segment_sec, 10.0)
        self.offline_sec = offline_sec or max(6 * self.segment_sec, 30.0)
        self.ingest = "ffmpeg"
        self.mediamtx: MediaMTXServer | None = None
        self.mediamtx_path: str | None = None
        self.started_at: datetime | None = None
        self._supervisor: Supervisor | None = None
        self.speaker_hook: Callable[[Path, float], None] | None = None
        for location in (config.source, config.options.get("speaker_backchannel_url")):
            remember_address(str(location or ""))

    # -- wiring ----------------------------------------------------------------
    @property
    def wants_mediamtx(self) -> bool:
        return False

    @property
    def wants_rtmp(self) -> bool:
        return False

    def mediamtx_source(self) -> Callable[[], str] | None:
        """Callable returning the credentialed pull URL for MediaMTX env, or None for publisher mode."""
        return None

    def attach_mediamtx(self, server: MediaMTXServer, path: str) -> None:
        self.mediamtx, self.mediamtx_path, self.ingest = server, path, "mediamtx"

    @property
    def live_url(self) -> str | None:
        """Local restream URL (credential-free), when MediaMTX serves this camera."""
        return self.mediamtx.read_url(self.mediamtx_path) if self.mediamtx and self.mediamtx_path else None

    # -- subclass hooks ----------------------------------------------------------
    def writer_command(self) -> tuple[list[str], dict[str, str] | None]:
        """argv/env of this adapter's own writer process; None-able for MediaMTX pull."""
        raise NotImplementedError

    def needs_writer(self) -> bool:
        return True

    def describe(self) -> dict:
        """Redacted plan for ``--check``: no credential ever appears here."""
        return {"camera_id": self.camera_id, "adapter": self.kind, "ingest": self.ingest,
                "segment_dir": str(self.segment_dir), "live_url": self.live_url}  # fmt: skip

    # -- lifecycle ---------------------------------------------------------------
    def probe(self) -> StreamHealth:
        raise NotImplementedError

    def start(self) -> None:
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = utcnow()
        if self.needs_writer() and self._supervisor is None:
            self._supervisor = Supervisor(f"{self.kind}:{self.camera_id}", self.writer_command,
                                          is_stalled=self._stalled)  # fmt: skip
            self._supervisor.start()

    def stop(self) -> None:
        if self._supervisor:
            self._supervisor.stop()
            self._supervisor = None
        self.started_at = None

    def _stalled(self, uptime: float) -> bool:
        if uptime < self.offline_sec:
            return False
        last = segments.newest_write(self.segment_dir)
        return last is None or (utcnow() - last).total_seconds() > self.offline_sec

    def health(self) -> StreamHealth:
        last = segments.newest_write(self.segment_dir)
        age = (utcnow() - last).total_seconds() if last else None
        sup = self._supervisor
        notes = []
        if self.started_at is None:
            notes.append("not started")
        if sup and sup.restarts:
            notes.append(f"restarts={sup.restarts}")
        if sup and sup.last_error:
            notes.append(f"last_error={sup.last_error}")
        if self.mediamtx and self.mediamtx_path and self.mediamtx.running and not self.mediamtx.ready(self.mediamtx_path):
            notes.append("restream not ready")
        if age is None:
            status = "offline"
            notes.insert(0, "no segments yet")
        elif age > self.offline_sec or self.started_at is None and age > self.stale_sec:
            status = "offline"
            notes.insert(0, f"newest segment {age:.0f}s old")
        elif age > self.stale_sec or (sup and not sup.running):
            status = "degraded"
            notes.insert(0, f"newest segment {age:.0f}s old")
        else:
            status = "ok"
        # Health leaves the hub (Web UI, bundle meta.json): no credential and no camera address.
        return StreamHealth(camera_id=self.camera_id, status=status, last_segment_at=last,
                            detail=scrub_addresses(scrub_credentials("; ".join(notes))))  # fmt: skip

    # -- optional one-way speaker (§5.1) --------------------------------------------
    def play_alert(self, audio: Path | str, volume: float) -> float:
        """Play a one-way alert through the camera speaker. Off unless the camera opts
        in (``speaker_enabled``); volume is clamped to ``volume_cap``. Returns clamped volume."""
        if not self.config.speaker_enabled:
            raise SpeakerDisabledError(f"speaker output is disabled for camera {self.camera_id!r}")
        clamped = min(max(float(volume), 0.0), self.config.volume_cap)
        audio_path = Path(audio)
        if self.speaker_hook is not None:
            self.speaker_hook(audio_path, clamped)
            return clamped
        self._play_alert(audio_path, clamped)
        return clamped

    def _play_alert(self, audio: Path, volume: float) -> None:
        raise SpeakerUnsupportedError(f"the {self.kind} adapter has no speaker output")


def ffmpeg_base() -> list[str]:
    return [tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "warning"]
