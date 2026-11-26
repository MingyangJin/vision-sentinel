"""Generic webhook sink: POSTs the alert as JSON (Slack/Discord bridges, ntfy, Home Assistant...)."""

from __future__ import annotations

import os

import httpx

from vs_runtime.contracts import AlertRecord


def payload(alert: AlertRecord) -> dict:
    r = alert.request
    return {
        "id": alert.id,
        "created_at": alert.created_at.isoformat(),
        "topic_id": r.topic_id,
        "severity": r.severity,
        "title": r.title,
        "body": r.body,
        "clip_ref": r.clip_ref,
        "bundle_id": r.bundle_id,
        "camera_ids": r.camera_ids,
        "requires_ack": r.requires_ack,
        "source": r.source,
    }


class WebhookSink:
    """``headers`` values of the form ``env:NAME`` are read from the environment at send time,
    so a webhook secret lives in secret storage, never in the config file."""

    def __init__(
        self,
        url: str,
        name: str = "webhook",
        headers: dict[str, str] | None = None,
        timeout_sec: float = 5.0,
        min_severity: str = "info",
        transport: httpx.BaseTransport | None = None,
    ):
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.timeout_sec = timeout_sec
        self.min_severity = min_severity
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        out = {}
        for k, v in self.headers.items():
            if v.startswith("env:"):
                var = v[4:]
                if var not in os.environ:
                    raise RuntimeError(f"header {k}: environment variable {var} is not set")
                v = os.environ[var]
            out[k] = v
        return out

    def send(self, alert: AlertRecord) -> None:
        with httpx.Client(timeout=self.timeout_sec, transport=self._transport) as client:
            resp = client.post(self.url, json=payload(alert), headers=self._headers())
        if resp.status_code >= 300:
            raise RuntimeError(f"HTTP {resp.status_code} from webhook")
