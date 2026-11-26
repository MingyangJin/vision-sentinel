"""Direct outbound FCM HTTP v1 push sink (GCP service-account OAuth2 JWT, zero relay)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import jwt

from vs_runtime.contracts import AlertRecord

log = logging.getLogger("vs_notify.fcm")

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
FCM_URL_TEMPLATE = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"


def fcm_payload(
    alert: AlertRecord,
    token: str,
    *,
    has_thumbnail: bool = False,
    has_clip: bool = False,
) -> dict[str, Any]:
    """Build a data-only high-priority FCM HTTP v1 message.

    Data-only messages always invoke ``FirebaseMessagingService.onMessageReceived`` on Android
    (foreground or background), allowing the sample app to route to ``sentinel_urgent`` vs.
    ``sentinel_default`` channels and attach inline Acknowledge / Thumbs Up / Thumbs Down actions
    for both phone and paired Wear OS watches.
    """
    r = alert.request
    channel_id = "sentinel_urgent" if r.severity == "urgent" else "sentinel_default"
    data = {
        "alert_id": alert.id,
        "topic_id": r.topic_id,
        "severity": r.severity,
        "title": r.title,
        "body": r.body,
        "channel_id": channel_id,
        "requires_ack": "true" if r.requires_ack else "false",
        "bundle_id": r.bundle_id or "",
        "has_thumbnail": "true" if has_thumbnail else "false",
        "has_clip": "true" if has_clip else "false",
    }
    android: dict[str, Any] = {"priority": "HIGH"}
    if r.dedupe_key:
        android["collapse_key"] = r.dedupe_key[:64]
    return {"message": {"token": token, "android": android, "data": data}}


class FcmSink:
    """Delivers alerts directly to Firebase Cloud Messaging HTTP v1 using a service account."""

    def __init__(
        self,
        *,
        service_account_path: str | Path | None = None,
        service_account_json: str | dict[str, Any] | None = None,
        project_id: str | None = None,
        name: str = "fcm",
        min_severity: str = "info",
        timeout_sec: float = 10.0,
        device_tokens: list[str] | None = None,
        store: Any = None,
        service: Any = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if not service_account_path and not service_account_json:
            raise ValueError("FcmSink requires service_account_path or service_account_json")
        self.name = name
        self.service_account_path = (
            Path(service_account_path).expanduser() if service_account_path else None
        )
        self._sa_input = service_account_json
        self._project_id_override = project_id
        self.min_severity = min_severity
        self.timeout_sec = timeout_sec
        self.device_tokens = list(device_tokens or [])
        self.store = store
        self._service = service
        self._transport = transport
        self._clock = clock
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._access_expires_at: float = 0.0

    def bind_store(self, store: Any) -> None:
        if self.store is None:
            self.store = store

    def bind_service(self, service: Any) -> None:
        if self._service is None:
            self._service = service

    def _load_service_account(self) -> dict[str, Any]:
        if isinstance(self._sa_input, dict):
            return self._sa_input
        if isinstance(self._sa_input, str):
            raw = self._sa_input
            if raw.startswith("env:"):
                var = raw[4:]
                if var not in os.environ:
                    raise RuntimeError(f"FCM service account env var {var} is not set")
                raw = os.environ[var]
            raw = raw.strip()
            if raw.startswith("{"):
                return json.loads(raw)
            return json.loads(Path(raw).expanduser().read_text(encoding="utf-8"))
        assert self.service_account_path is not None
        return json.loads(self.service_account_path.read_text(encoding="utf-8"))

    def _get_access_token(self, client: httpx.Client, sa: dict[str, Any]) -> str:
        with self._lock:
            now = self._clock()
            if self._access_token and now < self._access_expires_at:
                return self._access_token
            token_uri = sa.get("token_uri") or DEFAULT_TOKEN_URI
            headers = {"kid": sa["private_key_id"]} if sa.get("private_key_id") else None
            assertion = jwt.encode(
                {
                    "iss": sa["client_email"],
                    "sub": sa["client_email"],
                    "aud": token_uri,
                    "scope": FCM_SCOPE,
                    "iat": int(now),
                    "exp": int(now) + 3600,
                },
                sa["private_key"],
                algorithm="RS256",
                headers=headers,
            )
            resp = client.post(
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
            if resp.status_code >= 300:
                raise RuntimeError(f"OAuth2 token exchange failed: HTTP {resp.status_code}")
            body = resp.json()
            token = body["access_token"]
            expires_in = float(body.get("expires_in", 3600))
            self._access_token = token
            self._access_expires_at = now + max(0.0, expires_in - 60.0)
            return token

    def _tokens(self) -> list[str]:
        tokens = list(self.device_tokens)
        if self.store is not None:
            for d in self.store.devices("android"):
                if d["token"] not in tokens:
                    tokens.append(d["token"])
        return tokens

    def _media_flags(self, alert: AlertRecord) -> tuple[bool, bool]:
        if self._service is not None:
            return (
                self._service.resolve_thumbnail(alert) is not None,
                self._service.resolve_clip(alert) is not None,
            )
        return (False, bool(alert.request.clip_ref))

    def send(self, alert: AlertRecord) -> None:
        tokens = self._tokens()
        if not tokens:
            log.debug("fcm: no registered android devices; skipping alert %s", alert.id)
            return

        sa = self._load_service_account()
        project_id = self._project_id_override or sa["project_id"]
        url = FCM_URL_TEMPLATE.format(project_id=project_id)
        has_thumb, has_clip = self._media_flags(alert)
        succeeded = 0
        errors: list[str] = []

        with httpx.Client(timeout=self.timeout_sec, transport=self._transport) as cli:
            access_token = self._get_access_token(cli, sa)
            headers = {"Authorization": f"Bearer {access_token}"}
            for tok in tokens:
                body = fcm_payload(alert, tok, has_thumbnail=has_thumb, has_clip=has_clip)
                resp = cli.post(url, json=body, headers=headers)
                if resp.status_code < 300:
                    succeeded += 1
                    continue
                code, msg = _fcm_error(resp)
                if _is_dead_fcm_token(resp.status_code, code, msg):
                    log.warning("fcm: pruning invalid android token (%s: %s)", resp.status_code,
                                code or msg)
                    if self.store is not None:
                        self.store.remove_device("android", tok)
                errors.append(f"HTTP {resp.status_code} ({code or msg or 'unknown'})")

        if succeeded == 0 and errors:
            raise RuntimeError(f"FCM delivery failed for all {len(tokens)} device(s): "
                               + "; ".join(errors[:3]))


def _fcm_error(resp: httpx.Response) -> tuple[str, str]:
    try:
        err = resp.json().get("error", {})
    except Exception:
        return "", ""
    msg = str(err.get("message", ""))
    for detail in err.get("details", []):
        if isinstance(detail, dict) and detail.get("errorCode"):
            return str(detail["errorCode"]), msg
    return str(err.get("status", "")), msg


def _is_dead_fcm_token(status_code: int, code: str, msg: str) -> bool:
    if code == "UNREGISTERED" or status_code == 404:
        return True
    if status_code == 400 and "registration token" in msg.lower():
        return True
    return False
