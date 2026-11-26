"""Service configuration and construction from a plain dict (a ``[notify]`` TOML table).

    [notify]
    host = "127.0.0.1"         # bind; devices reach it over LAN/VPN/tunnel only if you widen it
    port = 8790
    db = "~/.local/share/vision-sentinel/notify.sqlite"
    topics_root = "topics"     # feedback is logged under <topics_root>/<topic_id>/state/
    cooldown_sec = 300         # default dedupe window when an alert sets none
    token_file = "..."         # household token file (default: ~/.config/vision-sentinel/)

    [[notify.sinks]]
    type = "desktop"
    dry_run = false

    [[notify.sinks]]
    type = "webhook"
    url = "https://ntfy.sh/your-topic"
    headers = { Authorization = "env:NTFY_AUTH" }
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .service import NotifyService, topics_root_resolver
from .sinks import build_sinks
from .store import Store

DEFAULT_PORT = 8790


def default_db_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "vision-sentinel" / "notify.sqlite"


class NotifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    db: str | None = None
    topics_root: str | None = None
    cooldown_sec: float = 300.0
    token_file: str | None = None
    sinks: list[dict[str, Any]] = Field(default_factory=list)

    def db_path(self) -> str:
        if self.db == ":memory:":
            return self.db
        return str(Path(self.db).expanduser() if self.db else default_db_path())


def build_notifier(config: dict[str, Any] | NotifyConfig, topics_root: Path | str | None = None,
                   **service_kwargs) -> NotifyService:
    """In-process ``Notifier`` for the runtime CLI. ``topics_root`` overrides the config's."""
    cfg = config if isinstance(config, NotifyConfig) else NotifyConfig.model_validate(config)
    root = topics_root if topics_root is not None else cfg.topics_root
    return NotifyService(
        Store(cfg.db_path()),
        build_sinks(cfg.sinks),
        topic_dir_for=topics_root_resolver(Path(root).expanduser() if root else None),
        default_cooldown_sec=cfg.cooldown_sec,
        **service_kwargs,
    )
