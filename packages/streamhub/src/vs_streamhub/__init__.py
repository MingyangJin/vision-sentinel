"""Vision Sentinel Stream Hub: camera adapters, single-pull ingest, rolling fMP4 buffer,
window slicing, live view helpers, and the credential boundary (ARCHITECTURE §5.1)."""

from vs_streamhub.adapters import (
    ADAPTERS,
    BaseAdapter,
    FileReplayAdapter,
    HttpIngestAdapter,
    OnvifAdapter,
    OnvifDevice,
    OnvifError,
    OnvifProfile,
    RtmpIngestAdapter,
    RtspAdapter,
    SpeakerDisabledError,
    SpeakerUnsupportedError,
    WebcamAdapter,
    WebcamDevice,
    build_adapter,
    discover_onvif,
    list_webcam_devices,
)
from vs_streamhub.credentials import CredentialError, redact_url, scrub_credentials
from vs_streamhub.hub import StreamHub
from vs_streamhub.live import MJPEG_BOUNDARY, MJPEG_MEDIA_TYPE, LiveStreamProvider, SegmentInfo, format_mjpeg_frame
from vs_streamhub.slicer import SliceResult, WindowSlicer

__all__ = [
    "ADAPTERS",
    "BaseAdapter",
    "CredentialError",
    "FileReplayAdapter",
    "HttpIngestAdapter",
    "LiveStreamProvider",
    "MJPEG_BOUNDARY",
    "MJPEG_MEDIA_TYPE",
    "OnvifAdapter",
    "OnvifDevice",
    "OnvifError",
    "OnvifProfile",
    "RtmpIngestAdapter",
    "RtspAdapter",
    "SegmentInfo",
    "SliceResult",
    "SpeakerDisabledError",
    "SpeakerUnsupportedError",
    "StreamHub",
    "WebcamAdapter",
    "WebcamDevice",
    "WindowSlicer",
    "build_adapter",
    "discover_onvif",
    "format_mjpeg_frame",
    "list_webcam_devices",
    "redact_url",
    "scrub_credentials",
]
