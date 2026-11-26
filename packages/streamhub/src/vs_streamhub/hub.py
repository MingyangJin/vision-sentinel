"""The Stream Hub: every camera by logical ``camera_id`` (ARCHITECTURE §4, §5.1).

Each camera is pulled once by its adapter into ``<recordings_root>/<camera_id>/`` as rolling
fMP4 segments, pruned to ``retention_sec``. Consumers get only camera ids, health, local
restream URLs, live snapshots/MJPEG, and extracted windows: never a camera address or
credential. Satisfies ``vs_runtime.contracts.StreamHubClient``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from vs_runtime.contracts import AssetKind, CameraConfig, ObservationAsset, StreamHealth, utcnow

from vs_streamhub import segments
from vs_streamhub.adapters import BaseAdapter, SpeakerDisabledError, build_adapter
from vs_streamhub.credentials import scrub_credentials
from vs_streamhub.live import LiveStreamProvider, SegmentInfo
from vs_streamhub.mediamtx import MediaMTXServer, MediaMTXSettings
from vs_streamhub.slicer import ALL_KINDS, SliceResult, WindowSlicer

log = logging.getLogger("vs_streamhub")
DEFAULT_ROOT = Path("~/.local/share/vision-sentinel/recordings")


class StreamHub:
    def __init__(self, cameras: Iterable[CameraConfig] = (), recordings_root: Path | str = DEFAULT_ROOT, *,
                 retention_sec: float = 3600.0, segment_sec: float = 2.0, mediamtx: str = "auto",
                 rtsp_port: int = 8554, api_port: int = 9997, rtmp_port: int = 1935,
                 mediamtx_binary: str | None = None, prune_interval_sec: float = 60.0,
                 slicer: WindowSlicer | None = None):  # fmt: skip
        if mediamtx not in ("auto", "always", "never"):
            raise ValueError("mediamtx must be 'auto', 'always' or 'never'")
        self.recordings_root = Path(recordings_root).expanduser()
        self.retention_sec = float(retention_sec)
        self.segment_sec = float(segment_sec)
        self.mediamtx_mode = mediamtx
        self.mediamtx_settings = MediaMTXSettings(
            binary=mediamtx_binary, rtsp_port=rtsp_port, api_port=api_port, rtmp_port=rtmp_port
        )
        self.prune_interval_sec = prune_interval_sec
        self.slicer = slicer or WindowSlicer()
        self.adapters: dict[str, BaseAdapter] = {}
        self.mediamtx: MediaMTXServer | None = None
        self.live = LiveStreamProvider(self)
        self._wired = False
        self._stop = threading.Event()
        self._pruner: threading.Thread | None = None
        for cfg in cameras:
            self.add_camera(cfg)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> StreamHub:
        """``{"recordings_root": ..., "retention_sec": ..., "cameras": [{camera_id, adapter, source, ...}]}``;
        ``cameras`` may also map camera_id -> settings."""
        cfg = dict(config)
        cams = cfg.pop("cameras", [])
        if isinstance(cams, dict):
            cams = [{"camera_id": k, **v} for k, v in cams.items()]
        return cls([c if isinstance(c, CameraConfig) else CameraConfig(**c) for c in cams], **cfg)

    # -- cameras -------------------------------------------------------------------
    def add_camera(self, config: CameraConfig) -> BaseAdapter:
        if self._wired:
            raise RuntimeError("add cameras before start()")
        if config.camera_id in self.adapters:
            raise ValueError(f"duplicate camera_id {config.camera_id!r}")
        if not config.camera_id or "/" in config.camera_id or config.camera_id.startswith("."):
            raise ValueError(f"invalid camera_id {config.camera_id!r}")
        adapter = build_adapter(config, self.segment_dir(config.camera_id), segment_sec=self.segment_sec)
        self.adapters[config.camera_id] = adapter
        return adapter

    @property
    def camera_ids(self) -> list[str]:
        return list(self.adapters)

    def adapter(self, camera_id: str) -> BaseAdapter:
        try:
            return self.adapters[camera_id]
        except KeyError:
            raise KeyError(f"unknown camera_id {camera_id!r}") from None

    def segment_dir(self, camera_id: str) -> Path:
        return self.recordings_root / camera_id

    def live_url(self, camera_id: str) -> str | None:
        self._wire()
        return self.adapter(camera_id).live_url

    # -- MediaMTX wiring -------------------------------------------------------------
    def _wire(self) -> None:
        """Decide which cameras go through MediaMTX. Idempotent; resolves no secret."""
        if self._wired:
            return
        self._wired = True
        wanting = [a for a in self.adapters.values() if a.wants_mediamtx]
        if self.mediamtx_mode == "never" or not wanting:
            return
        binary = self.mediamtx_settings.resolve_binary()
        if not binary:
            explicit = [a.camera_id for a in wanting if a.config.options.get("ingest") == "mediamtx"]
            if self.mediamtx_mode == "always" or explicit:
                raise RuntimeError(f"mediamtx is required (cameras {explicit or 'all'}) but not on PATH")
            log.info("mediamtx not found; cameras use the FFmpeg fallback writer")
            return
        self.mediamtx = MediaMTXServer(self.mediamtx_settings, self.recordings_root / ".mediamtx")
        for a in wanting:
            if a.wants_rtmp:
                self.mediamtx.enable_rtmp(
                    host=a.config.options.get("bind_host"),
                    port=a.config.options.get("port"),
                    publish_ips=a.config.options.get("publish_ips"),
                )
            path = self.mediamtx.add_path(a.camera_id, a.segment_dir, a.mediamtx_source())
            a.attach_mediamtx(self.mediamtx, path)

    def plan(self) -> dict:
        """What ``start()`` would run, credentials redacted (for ``check``/``--check``)."""
        self._wire()
        plan = {"recordings_root": str(self.recordings_root), "retention_sec": self.retention_sec,
                "mediamtx": None, "cameras": [a.describe() for a in self.adapters.values()]}  # fmt: skip
        if self.mediamtx:
            s = self.mediamtx.settings
            mtx_plan: dict[str, Any] = {
                "binary": s.resolve_binary(),
                "rtsp": f"{s.host}:{s.rtsp_port}",
                "api": f"{s.host}:{s.api_port}",
                "paths": sorted(self.mediamtx.paths),
            }
            if s.rtmp_enabled:
                mtx_plan["rtmp"] = f"{s.rtmp_host}:{s.rtmp_port}"
            plan["mediamtx"] = mtx_plan
        return plan

    # -- lifecycle -------------------------------------------------------------------
    def start(self) -> None:
        self._wire()
        self.recordings_root.mkdir(parents=True, exist_ok=True)
        if self.mediamtx:
            self.mediamtx.start()
        for a in self.adapters.values():
            a.start()
        self._stop.clear()
        if self.retention_sec > 0 and self._pruner is None:
            self._pruner = threading.Thread(target=self._prune_loop, name="vs-prune", daemon=True)
            self._pruner.start()

    def stop(self) -> None:
        self._stop.set()
        for a in self.adapters.values():
            a.stop()
        if self.mediamtx:
            self.mediamtx.stop()
        if self._pruner:
            self._pruner.join(5)
            self._pruner = None

    def __enter__(self) -> StreamHub:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def prune(self) -> int:
        """Delete segments older than the retention window (never a camera's newest)."""
        cutoff = utcnow() - timedelta(seconds=self.retention_sec)
        return sum(len(segments.prune(a.segment_dir, cutoff)) for a in self.adapters.values())

    def _prune_loop(self) -> None:
        while not self._stop.wait(self.prune_interval_sec):
            try:
                self.prune()
            except OSError as e:
                log.warning("prune failed: %s", scrub_credentials(str(e)))

    # -- StreamHubClient ---------------------------------------------------------------
    def probe(self, camera_id: str) -> StreamHealth:
        self._wire()
        return self.adapter(camera_id).probe()

    def health(self, camera_id: str) -> StreamHealth:
        if camera_id not in self.adapters:
            return StreamHealth(camera_id=camera_id, status="offline", detail="unknown camera_id")
        return self.adapters[camera_id].health()

    def wait_until_ok(self, camera_id: str, timeout: float) -> StreamHealth:
        deadline = utcnow() + timedelta(seconds=timeout)
        while (h := self.health(camera_id)).status != "ok" and utcnow() < deadline:
            self._stop.wait(0.5)
        return h

    def slice(self, camera_id: str, start: datetime, end: datetime, out_dir: Path,
              kinds: set[AssetKind] | frozenset[AssetKind] = ALL_KINDS) -> SliceResult:  # fmt: skip
        adapter = self.adapter(camera_id)
        return self.slicer.slice(camera_id, adapter.segment_dir, start, end, Path(out_dir), kinds,
                                 stream_health=adapter.health())  # fmt: skip

    def extract_window(self, camera_id: str, start: datetime, end: datetime, out_dir: Path,
                       kinds: set[AssetKind]) -> list[ObservationAsset]:  # fmt: skip
        return self.slice(camera_id, start, end, out_dir, kinds).assets

    def slice_latest(self, camera_id: str, window_sec: float, out_dir: Path,
                     kinds: set[AssetKind] | frozenset[AssetKind] = ALL_KINDS) -> SliceResult:  # fmt: skip
        end = utcnow()
        return self.slice(camera_id, end - timedelta(seconds=window_sec), end, out_dir, kinds)

    # -- Live view & timeline helpers (for Web UI, "The One Rule") -------------------
    def list_cameras(self) -> list[dict[str, Any]]:
        self._wire()
        return self.live.list_cameras()

    def latest_snapshot(self, camera_id: str, *, width: int | None = 960, quality: int = 5) -> bytes | None:
        return self.live.latest_snapshot(camera_id, width=width, quality=quality)

    def iter_mjpeg(self, camera_id: str, *, fps: float = 2.0, width: int | None = 960,
                   quality: int = 5, max_frames: int | None = None) -> AsyncIterator[bytes]:  # fmt: skip
        return self.live.iter_mjpeg(camera_id, fps=fps, width=width, quality=quality, max_frames=max_frames)

    def iter_fmp4(self, camera_id: str, *, max_chunks: int | None = None) -> AsyncIterator[bytes]:
        return self.live.iter_fmp4(camera_id, max_chunks=max_chunks)

    def list_segments(self, camera_id: str, *, limit: int | None = None) -> list[SegmentInfo]:
        return self.live.list_segments(camera_id, limit=limit)

    def segment_file(self, camera_id: str, segment_name: str) -> Path:
        return self.live.segment_file(camera_id, segment_name)

    def extract_clip(self, camera_id: str, start: datetime, end: datetime, out_path: Path | str,
                     *, width: int = 960) -> Path | None:  # fmt: skip
        return self.live.extract_clip(camera_id, start, end, out_path, width=width)

    # -- Optional one-way camera speaker alert (M4, §5.1) ----------------------------
    def play_alert(
        self,
        camera_id: str,
        audio_path: Path | str,
        volume: float = 0.3,
        *,
        topic_speaker_enabled: bool = True,
    ) -> float:
        """Play a one-way warning audio clip on ``camera_id``'s speaker.

        Enforces double opt-in (``topic_speaker_enabled`` AND camera ``speaker_enabled``)
        and clamps ``volume`` to ``camera.volume_cap``. Returns the clamped volume played.
        Raises ``SpeakerDisabledError`` if not opted in, ``SpeakerUnsupportedError`` if
        the adapter has no speaker backend, or ``KeyError`` if ``camera_id`` is unknown.
        """
        if not topic_speaker_enabled:
            raise SpeakerDisabledError(f"speaker output is disabled for this topic (camera {camera_id!r})")
        adapter = self.adapter(camera_id)
        return adapter.play_alert(Path(audio_path), volume)
