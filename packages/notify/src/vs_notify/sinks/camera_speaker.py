"""Optional, opt-in one-way Camera Speaker alert sink (ARCHITECTURE.md §5.4).

Routes a warning chime/clip back to a camera's speaker strictly via an injected
``play_alert(camera_id, audio_path, volume)`` callback from Stream Hub — never touches
camera URLs, protocols, or credentials ("The One Rule").

Disabled by default: fires ONLY when ``alert.request.speaker is True`` AND the target
``camera_id`` is explicitly listed in ``enabled_cameras``.
"""

from __future__ import annotations

import logging
import math
import struct
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path

from vs_runtime.contracts import AlertRecord

log = logging.getLogger("vs_notify.camera_speaker")


def write_default_chime(path: Path) -> Path:
    """Write a short, clean two-tone alert chime (16 kHz mono 16-bit PCM WAV)."""
    rate = 16000
    tones = [(880.0, 0.16), (0.0, 0.06), (1100.0, 0.22)]
    frames = bytearray()
    for freq, dur in tones:
        n = int(rate * dur)
        for i in range(n):
            if freq <= 0:
                sample = 0
            else:
                env = min(1.0, i / 200.0, (n - 1 - i) / 200.0)
                sample = int(0.4 * env * 32767 * math.sin(2.0 * math.pi * freq * i / rate))
            frames += struct.pack("<h", sample)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return path


class CameraSpeakerSink:
    """One-way camera speaker alert sink with explicit per-camera opt-in and volume clamping."""

    def __init__(
        self,
        *,
        play_alert: Callable[[str, Path, float], None] | None = None,
        enabled_cameras: list[str] | set[str] | None = None,
        audio_path: str | Path | None = None,
        volume: float = 0.3,
        max_volume: float = 0.3,
        volume_caps: dict[str, float] | None = None,
        name: str = "camera_speaker",
        min_severity: str = "info",
    ):
        self.name = name
        self.play_alert = play_alert
        self.enabled_cameras: set[str] = set(enabled_cameras or ())
        self.audio_path = Path(audio_path).expanduser() if audio_path else None
        self.volume = max(0.0, min(1.0, float(volume)))
        self.max_volume = max(0.0, min(1.0, float(max_volume)))
        self.volume_caps = {k: max(0.0, min(1.0, float(v))) for k, v in (volume_caps or {}).items()}
        self.min_severity = min_severity
        self._generated_chime: Path | None = None

    def bind_play_alert(self, play_alert: Callable[[str, Path, float], None]) -> None:
        if self.play_alert is None:
            self.play_alert = play_alert

    def target_cameras(self, alert: AlertRecord) -> list[str]:
        if not alert.request.speaker or not self.enabled_cameras:
            return []
        return [cid for cid in alert.request.camera_ids if cid in self.enabled_cameras]

    def accepts(self, alert: AlertRecord) -> bool:
        """True only when the alert explicitly requests speaker output on an opted-in camera."""
        return bool(self.target_cameras(alert))

    def _resolve_audio(self) -> Path:
        if self.audio_path is not None:
            if not self.audio_path.is_file():
                raise FileNotFoundError(f"speaker audio file not found: {self.audio_path}")
            return self.audio_path
        if self._generated_chime is None or not self._generated_chime.is_file():
            chime = Path(tempfile.gettempdir()) / "vision-sentinel-alert-chime.wav"
            self._generated_chime = write_default_chime(chime)
        return self._generated_chime

    def effective_volume(self, camera_id: str) -> float:
        cap = self.volume_caps.get(camera_id, self.max_volume)
        return max(0.0, min(self.volume, cap))

    def send(self, alert: AlertRecord) -> None:
        targets = self.target_cameras(alert)
        if not targets:
            return
        if self.play_alert is None:
            raise RuntimeError("camera_speaker sink has no play_alert callback configured")
        audio = self._resolve_audio()
        errors: list[str] = []
        for cam_id in targets:
            vol = self.effective_volume(cam_id)
            try:
                self.play_alert(cam_id, audio, vol)
            except Exception as exc:
                log.warning("camera_speaker failed on %s: %s", cam_id, exc)
                errors.append(f"{cam_id}: {exc}")
        if errors:
            raise RuntimeError("camera speaker failed: " + "; ".join(errors))
