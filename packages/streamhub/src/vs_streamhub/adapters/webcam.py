"""Local USB / built-in webcam adapter via FFmpeg (ARCHITECTURE §5.1, item 3).

Supports macOS (``avfoundation``) and Linux (``v4l2``). Writes rolling fMP4 segments
directly by default (no daemon needed); set ``options.ingest = "mediamtx"`` to publish
to the hub's MediaMTX for live RTSP restreaming as well.

``source``:
- macOS: video device index (``"0"``), ``"<video>:<audio>"`` (``"0:0"`` or ``"0:none"``),
  or device name (``"FaceTime HD Camera"``); empty / ``"default"`` defaults to ``"0"``.
- Linux: device path (``"/dev/video0"``) or index (``"0"`` -> ``"/dev/video0"``).

Options: ``framerate`` (default 15), ``resolution`` (e.g. ``"1280x720"``),
``pixel_format`` (optional), ``audio_device`` (optional), ``audio_backend`` (Linux,
default ``"alsa"``), ``ingest`` (``"direct"`` | ``"mediamtx"``), ``segment_sec``.
"""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from vs_runtime.contracts import StreamHealth

from vs_streamhub import ffmpeg
from vs_streamhub.adapters.base import BaseAdapter, ffmpeg_base, segment_args

_AVF_DEVICE = re.compile(r"\[(\d+)\]\s+(.+)$")
_VIDEO_DEV_NUM = re.compile(r"^video(\d+)$")


@dataclass(frozen=True)
class WebcamDevice:
    id: str  # "0" on macOS, "/dev/video0" on Linux
    name: str
    kind: Literal["video", "audio"] = "video"
    backend: Literal["avfoundation", "v4l2"] = "avfoundation"


def parse_avfoundation_devices(stderr: str) -> list[WebcamDevice]:
    """Parse ``ffmpeg -f avfoundation -list_devices true -i ""`` stderr output."""
    devices: list[WebcamDevice] = []
    section: Literal["video", "audio"] | None = None
    for raw in stderr.splitlines():
        line = raw.strip()
        lower = line.lower()
        if "avfoundation video devices:" in lower:
            section = "video"
            continue
        if "avfoundation audio devices:" in lower:
            section = "audio"
            continue
        if section is None:
            continue
        m = _AVF_DEVICE.search(line)
        if m:
            devices.append(WebcamDevice(id=m.group(1), name=m.group(2).strip(), kind=section, backend="avfoundation"))
    return devices


def list_v4l2_devices(dev_root: Path = Path("/dev"), sys_root: Path = Path("/sys/class/video4linux")) -> list[WebcamDevice]:
    """Enumerate Linux ``/dev/video*`` capture nodes with friendly names from sysfs when available."""
    if not dev_root.is_dir():
        return []
    found: list[tuple[int, WebcamDevice]] = []
    for entry in dev_root.iterdir():
        m = _VIDEO_DEV_NUM.match(entry.name)
        if not m:
            continue
        idx = int(m.group(1))
        name_file = sys_root / entry.name / "name"
        name = name_file.read_text(errors="replace").strip() if name_file.is_file() else entry.name
        found.append((idx, WebcamDevice(id=str(entry), name=name or entry.name, kind="video", backend="v4l2")))
    return [dev for _, dev in sorted(found, key=lambda pair: pair[0])]


def list_webcam_devices(system: str | None = None) -> list[WebcamDevice]:
    """List local webcam devices on macOS (avfoundation) or Linux (v4l2)."""
    os_name = (system or platform.system()).lower()
    if os_name == "darwin":
        try:
            proc = ffmpeg.run(
                ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                timeout=10.0,
                check=False,
            )
        except ffmpeg.FFmpegError:
            return []
        return parse_avfoundation_devices(proc.stderr.decode(errors="replace"))
    if os_name == "linux":
        return list_v4l2_devices()
    return []


class WebcamAdapter(BaseAdapter):
    kind = "webcam"

    def __init__(
        self,
        config,
        segment_dir,
        *,
        system: str | None = None,
        list_devices_fn: Callable[[], list[WebcamDevice]] | None = None,
        **kw,
    ):
        super().__init__(config, segment_dir, **kw)
        self._system = (system or config.options.get("platform") or platform.system()).lower()
        self._list_devices_fn = list_devices_fn

    @property
    def backend(self) -> str:
        if self._system == "darwin":
            return "avfoundation"
        if self._system == "linux":
            return "v4l2"
        raise RuntimeError(f"unsupported webcam platform {self._system!r} (expected macOS/Darwin or Linux)")

    @property
    def wants_mediamtx(self) -> bool:
        return self.config.options.get("ingest", "direct") == "mediamtx"

    def _device_spec(self) -> tuple[str, str | None]:
        src = (self.config.source or "").strip()
        audio_opt = self.config.options.get("audio_device")
        if self.backend == "avfoundation":
            if not src or src.lower() == "default":
                src = "0"
            if ":" in src:
                v, _, a = src.partition(":")
                v_norm = "0" if (not v or v.lower() == "default") else v
                if not a or a.lower() == "none":
                    a_norm = None
                elif a.lower() in ("default", "auto"):
                    a_norm = "0"
                else:
                    a_norm = a
                return v_norm, a_norm
            if audio_opt in (None, "", "none"):
                a_norm = None
            elif str(audio_opt).lower() in ("default", "auto"):
                a_norm = "0"
            else:
                a_norm = str(audio_opt)
            return src, a_norm
        # Linux v4l2
        if not src or src.lower() == "default":
            dev = "/dev/video0"
        elif src.isdigit():
            dev = f"/dev/video{src}"
        else:
            dev = src
        return dev, (str(audio_opt) if audio_opt not in (None, "", "none") else None)

    def _default_framerate(self) -> int:
        # Apple Silicon / FaceTime HD avfoundation devices reject 15 fps unless a narrow mode matches;
        # 30 fps is universally supported on macOS built-in cameras.
        return 30 if self.backend == "avfoundation" else 15

    def _input_args(self) -> list[str]:
        opts = self.config.options
        fps = str(opts.get("framerate", self._default_framerate()))
        res = opts.get("resolution")
        pix = opts.get("pixel_format")
        video_dev, audio_dev = self._device_spec()

        if self.backend == "avfoundation":
            pix_val = pix if pix is not None else "nv12"
            args = ["-f", "avfoundation", "-framerate", fps]
            if res:
                args += ["-video_size", str(res)]
            if pix_val:
                args += ["-pixel_format", str(pix_val)]
            args += ["-thread_queue_size", "512"]
            av_in = f"{video_dev}:{audio_dev}" if audio_dev is not None else f"{video_dev}:none"
            return [*args, "-i", av_in]

        # Linux v4l2
        args = ["-f", "v4l2", "-framerate", fps]
        if res:
            args += ["-video_size", str(res)]
        if pix:
            args += ["-input_format", str(pix)]
        args += ["-i", video_dev]
        if audio_dev:
            audio_backend = str(opts.get("audio_backend", "alsa"))
            args += ["-f", audio_backend, "-i", audio_dev]
        return args

    def writer_command(self) -> tuple[list[str], dict[str, str] | None]:
        video_dev, audio_dev = self._device_spec()
        _ = video_dev
        maps = ["-map", "0:v:0"]
        if self.backend == "avfoundation" and audio_dev is not None:
            maps += ["-map", "0:a:0?"]
        elif self.backend == "v4l2" and audio_dev is not None:
            maps += ["-map", "1:a:0?"]
        codec = [
            *maps, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-force_key_frames", "expr:gte(t,n_forced*1)",
        ]
        if audio_dev is not None:
            codec += ["-c:a", "aac", "-ac", "1"]
        argv = [*ffmpeg_base(), *self._input_args(), *codec]
        if self.ingest == "mediamtx" and self.mediamtx and self.mediamtx_path:
            argv += ["-f", "rtsp", "-rtsp_transport", "tcp", self.mediamtx.publish_url(self.mediamtx_path)]
        else:
            argv += segment_args(self.segment_dir, self.segment_sec)
        return argv, None

    def list_devices(self) -> list[WebcamDevice]:
        if self._list_devices_fn is not None:
            return self._list_devices_fn()
        return list_webcam_devices(self._system)

    def probe(self) -> StreamHealth:
        try:
            backend = self.backend
        except RuntimeError as e:
            return StreamHealth(camera_id=self.camera_id, status="offline", detail=str(e))
        video_dev, audio_dev = self._device_spec()
        all_devices = self.list_devices()
        devices = [d for d in all_devices if d.kind == "video"]
        match = next(
            (d for d in devices if d.id == video_dev or d.name.lower() == video_dev.lower()
             or video_dev.lower() in d.name.lower()),
            None,
        )
        if match is None:
            avail = ", ".join(f"{d.id} ({d.name})" for d in devices) or "none found"
            return StreamHealth(
                camera_id=self.camera_id,
                status="offline",
                detail=f"{backend} device {video_dev!r} not found (available: {avail})",
            )
        audio_note = ", video-only"
        if audio_dev is not None:
            if backend == "avfoundation":
                audio_devs = [d for d in all_devices if d.kind == "audio"]
                amatch = next(
                    (d for d in audio_devs if d.id == audio_dev or d.name.lower() == audio_dev.lower()
                     or audio_dev.lower() in d.name.lower()),
                    None,
                )
                if amatch is None:
                    a_avail = ", ".join(f"{d.id} ({d.name})" for d in audio_devs) or "none found"
                    return StreamHealth(
                        camera_id=self.camera_id,
                        status="offline",
                        detail=f"{backend} audio device {audio_dev!r} not found (available: {a_avail})",
                    )
                audio_note = f", audio={amatch.id} ({amatch.name})"
            else:
                audio_note = f", audio={audio_dev}"
        return StreamHealth(
            camera_id=self.camera_id,
            status="ok",
            detail=f"{backend} device {match.id} ({match.name}){audio_note}",
        )

    def describe(self) -> dict:
        try:
            backend = self.backend
            video_dev, audio_dev = self._device_spec()
            default_fps = self._default_framerate()
        except RuntimeError:
            backend, video_dev, audio_dev, default_fps = self._system, self.config.source, None, 15
        return {
            **super().describe(),
            "backend": backend,
            "device": video_dev,
            "audio_device": audio_dev,
            "framerate": self.config.options.get("framerate", default_fps),
            "resolution": self.config.options.get("resolution"),
            "segment_sec": self.segment_sec,
        }
