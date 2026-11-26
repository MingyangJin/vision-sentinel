"""Delivery sinks. Each implements ``vs_runtime.contracts.NotificationSink``: a ``name`` and a
``send(alert)`` that raises on failure. The service fans out to every sink and records each
result, so one failing sink never blocks another.

Built-in sink types: ``webhook``, ``desktop``, ``apns``, ``fcm``, ``camera_speaker``.
``build_sinks`` constructs sinks from ``[[notify.sinks]]`` config tables.
"""

from __future__ import annotations

from typing import Any

from vs_runtime.contracts import AlertRecord, NotificationSink

from .apns import ApnsSink
from .camera_speaker import CameraSpeakerSink
from .desktop import DesktopSink
from .fcm import FcmSink
from .webhook import WebhookSink

SEVERITY_RANK = {"info": 0, "warning": 1, "urgent": 2}

SINK_TYPES: dict[str, type] = {
    "webhook": WebhookSink,
    "desktop": DesktopSink,
    "apns": ApnsSink,
    "fcm": FcmSink,
    "camera_speaker": CameraSpeakerSink,
}


def wants(sink: NotificationSink, alert: AlertRecord) -> bool:
    """A sink may set ``min_severity`` or define ``accepts(alert)`` to filter alerts."""
    floor = getattr(sink, "min_severity", "info")
    if SEVERITY_RANK[alert.request.severity] < SEVERITY_RANK[floor]:
        return False
    accepts = getattr(sink, "accepts", None)
    if callable(accepts) and not accepts(alert):
        return False
    return True


def build_sinks(configs: list[dict[str, Any]]) -> list[NotificationSink]:
    sinks = []
    for cfg in configs:
        cfg = dict(cfg)
        kind = cfg.pop("type")
        if kind not in SINK_TYPES:
            raise ValueError(f"unknown sink type {kind!r}; known: {', '.join(SINK_TYPES)}")
        sinks.append(SINK_TYPES[kind](**cfg))
    names = [s.name for s in sinks]
    if len(names) != len(set(names)):
        raise ValueError(f"sink names must be unique: {names}")
    return sinks


__all__ = [
    "SINK_TYPES",
    "ApnsSink",
    "CameraSpeakerSink",
    "DesktopSink",
    "FcmSink",
    "WebhookSink",
    "build_sinks",
    "wants",
]
