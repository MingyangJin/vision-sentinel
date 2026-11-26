"""Direct outbound APNs push sink (HTTP/2 + ES256 .p8 auth key, zero third-party relay)."""

from __future__ import annotations

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

log = logging.getLogger("vs_notify.apns")

APNS_PROD = "https://api.push.apple.com"
APNS_SANDBOX = "https://api.sandbox.push.apple.com"
JWT_TTL_SEC = 50 * 60  # Apple accepts up to 60 min and rejects refresh faster than 20 min
DEAD_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}


def apns_payload(alert: AlertRecord, *, critical_alerts: bool = False) -> dict[str, Any]:
    r = alert.request
    if r.severity == "info":
        level = "active"
        sound: Any = "default"
    elif r.severity == "urgent" and critical_alerts:
        level = "critical"
        sound = {"critical": 1, "name": "default", "volume": 1.0}
    else:
        level = "time-sensitive"
        sound = "default"

    aps: dict[str, Any] = {
        "alert": {"title": r.title, "subtitle": r.topic_id, "body": r.body},
        "sound": sound,
        "category": "VISION_ALERT",
        "interruption-level": level,
        "mutable-content": 1,
    }
    body: dict[str, Any] = {
        "aps": aps,
        "alert_id": alert.id,
        "topic_id": r.topic_id,
        "severity": r.severity,
        "requires_ack": r.requires_ack,
    }
    if r.bundle_id:
        body["bundle_id"] = r.bundle_id
    return body


class ApnsSink:
    """Delivers alerts directly to Apple's APNs HTTP/2 API using a ``.p8`` ES256 Auth Key.

    ``private_key`` accepts raw PEM text or ``env:NAME``; ``key_path`` points to a ``.p8`` file.
    Device tokens come from the bound ``Store`` (``platform='ios'``) plus optional static
    ``device_tokens``. Invalid/unregistered tokens (HTTP 410 or ``BadDeviceToken``) are pruned.
    """

    def __init__(
        self,
        team_id: str,
        key_id: str,
        bundle_id: str,
        *,
        key_path: str | Path | None = None,
        private_key: str | None = None,
        sandbox: bool = False,
        critical_alerts: bool = False,
        name: str = "apns",
        min_severity: str = "info",
        timeout_sec: float = 10.0,
        device_tokens: list[str] | None = None,
        store: Any = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if not key_path and not private_key:
            raise ValueError("ApnsSink requires either key_path or private_key")
        self.name = name
        self.team_id = team_id
        self.key_id = key_id
        self.bundle_id = bundle_id
        self.key_path = Path(key_path).expanduser() if key_path else None
        self._private_key = private_key
        self.sandbox = sandbox
        self.critical_alerts = critical_alerts
        self.min_severity = min_severity
        self.timeout_sec = timeout_sec
        self.device_tokens = list(device_tokens or [])
        self.store = store
        self._transport = transport
        self._clock = clock
        self._lock = threading.Lock()
        self._cached_jwt: str | None = None
        self._jwt_issued_at: float = 0.0

    def bind_store(self, store: Any) -> None:
        if self.store is None:
            self.store = store

    @property
    def base_url(self) -> str:
        return APNS_SANDBOX if self.sandbox else APNS_PROD

    def _load_key(self) -> str:
        if self._private_key:
            if self._private_key.startswith("env:"):
                var = self._private_key[4:]
                if var not in os.environ:
                    raise RuntimeError(f"APNs private key env var {var} is not set")
                return os.environ[var]
            return self._private_key
        assert self.key_path is not None
        return self.key_path.read_text(encoding="utf-8")

    def auth_jwt(self) -> str:
        with self._lock:
            now = self._clock()
            if self._cached_jwt and (now - self._jwt_issued_at) < JWT_TTL_SEC:
                return self._cached_jwt
            token = jwt.encode(
                {"iss": self.team_id, "iat": int(now)},
                self._load_key(),
                algorithm="ES256",
                headers={"kid": self.key_id},
            )
            self._cached_jwt = token
            self._jwt_issued_at = now
            return token

    def _tokens(self) -> list[str]:
        tokens = list(self.device_tokens)
        if self.store is not None:
            for d in self.store.devices("ios"):
                if d["token"] not in tokens:
                    tokens.append(d["token"])
        return tokens

    def send(self, alert: AlertRecord) -> None:
        tokens = self._tokens()
        if not tokens:
            log.debug("apns: no registered ios devices; skipping alert %s", alert.id)
            return

        r = alert.request
        headers = {
            "authorization": f"bearer {self.auth_jwt()}",
            "apns-topic": self.bundle_id,
            "apns-push-type": "alert",
            "apns-priority": "5" if r.severity == "info" else "10",
        }
        if r.dedupe_key:
            headers["apns-collapse-id"] = r.dedupe_key[:64]

        body = apns_payload(alert, critical_alerts=self.critical_alerts)
        succeeded = 0
        errors: list[str] = []

        with httpx.Client(http2=True, timeout=self.timeout_sec, transport=self._transport) as cli:
            for tok in tokens:
                resp = cli.post(f"{self.base_url}/3/device/{tok}", json=body, headers=headers)
                if resp.status_code == 200:
                    succeeded += 1
                    continue
                reason = _apns_reason(resp)
                if resp.status_code == 410 or (resp.status_code == 400 and reason in DEAD_REASONS):
                    log.warning("apns: pruning invalid ios token (%s: %s)", resp.status_code, reason)
                    if self.store is not None:
                        self.store.remove_device("ios", tok)
                errors.append(f"HTTP {resp.status_code} ({reason or 'unknown'})")

        if succeeded == 0 and errors:
            raise RuntimeError(f"APNs delivery failed for all {len(tokens)} device(s): "
                               + "; ".join(errors[:3]))


def _apns_reason(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("reason", ""))
    except Exception:
        return ""
