"""Loopback HTTP client: a ``Notifier`` for a runtime or watchdog running in another process.

For a single-process run, use ``NotifyService`` (``build_notifier``) directly; it is the same
``Notifier``.
"""

from __future__ import annotations

import httpx

from vs_runtime.contracts import AlertRecord, AlertRequest

from .config import DEFAULT_PORT


class NotifyClient:
    def __init__(self, base_url: str = f"http://127.0.0.1:{DEFAULT_PORT}", timeout_sec: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url, timeout=timeout_sec, transport=transport,
                                  trust_env=False)  # never route loopback calls via a proxy

    def send_alert(self, request: AlertRequest) -> AlertRecord:
        resp = self._http.post("/v1/alerts", content=request.model_dump_json(),
                               headers={"content-type": "application/json"})
        resp.raise_for_status()
        return AlertRecord.model_validate(resp.json())

    def healthy(self) -> bool:
        try:
            return self._http.get("/healthz").status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        self._http.close()
