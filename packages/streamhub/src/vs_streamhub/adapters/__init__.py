"""Camera adapters by name (ARCHITECTURE §5.1).

Built-in v1 adapters:
- ``file_replay`` (alias ``file``): local file / folder loop replay
- ``rtsp`` (alias ``rtsps``): generic RTSP / RTSPS single-pull
- ``onvif``: ONVIF WS-Discovery + GetProfiles / GetStreamUri
- ``webcam`` (alias ``usb``): local USB / built-in webcam (avfoundation / v4l2)
- ``rtmp_ingest`` (alias ``rtmp``): phone / mobile RTMP or RTSP push ingest
- ``http_ingest`` (aliases ``macos_camera``, ``camera_client``): HTTP fMP4 segment push (native macOS Camera app)
"""

from __future__ import annotations

from pathlib import Path

from vs_runtime.contracts import CameraConfig

from vs_streamhub.adapters.base import BaseAdapter, SpeakerDisabledError, SpeakerUnsupportedError, Supervisor
from vs_streamhub.adapters.file_replay import FileReplayAdapter
from vs_streamhub.adapters.http_ingest import HttpIngestAdapter
from vs_streamhub.adapters.onvif import OnvifAdapter, OnvifDevice, OnvifError, OnvifProfile, discover_onvif
from vs_streamhub.adapters.rtmp_ingest import RtmpIngestAdapter
from vs_streamhub.adapters.rtsp import RtspAdapter
from vs_streamhub.adapters.webcam import WebcamAdapter, WebcamDevice, list_webcam_devices

ADAPTERS: dict[str, type[BaseAdapter]] = {
    "file_replay": FileReplayAdapter,
    "file": FileReplayAdapter,
    "rtsp": RtspAdapter,
    "rtsps": RtspAdapter,
    "onvif": OnvifAdapter,
    "webcam": WebcamAdapter,
    "usb": WebcamAdapter,
    "rtmp_ingest": RtmpIngestAdapter,
    "rtmp": RtmpIngestAdapter,
    "http_ingest": HttpIngestAdapter,
    "macos_camera": HttpIngestAdapter,
    "camera_client": HttpIngestAdapter,
}


def build_adapter(config: CameraConfig, segment_dir: Path, **kw) -> BaseAdapter:
    try:
        cls = ADAPTERS[config.adapter]
    except KeyError:
        raise ValueError(f"camera {config.camera_id!r}: unknown adapter {config.adapter!r} "
                         f"(available: {', '.join(sorted(ADAPTERS))})") from None  # fmt: skip
    return cls(config, segment_dir, **kw)


__all__ = [
    "ADAPTERS",
    "BaseAdapter",
    "FileReplayAdapter",
    "HttpIngestAdapter",
    "OnvifAdapter",
    "OnvifDevice",
    "OnvifError",
    "OnvifProfile",
    "RtmpIngestAdapter",
    "RtspAdapter",
    "SpeakerDisabledError",
    "SpeakerUnsupportedError",
    "Supervisor",
    "WebcamAdapter",
    "WebcamDevice",
    "build_adapter",
    "discover_onvif",
    "list_webcam_devices",
]
