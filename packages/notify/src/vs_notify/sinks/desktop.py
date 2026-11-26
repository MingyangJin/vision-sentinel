"""Local desktop sink: a macOS or Linux banner, or a log line where neither exists.

``dry_run`` records the command it would run and logs the alert instead, so tests and
headless checks never pop a banner or play a sound.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

from vs_runtime.contracts import AlertRecord

log = logging.getLogger("vs_notify.desktop")

# The alert text is passed as argv, never interpolated into the script.
OSASCRIPT = [
    "-e", "on run argv",
    "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
    "-e", "end run",
]
OSASCRIPT_SOUND = [
    "-e", "on run argv",
    "-e", 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
    "-e", "end run",
]


class DesktopSink:
    def __init__(
        self,
        name: str = "desktop",
        dry_run: bool = False,
        sound: bool = False,
        min_severity: str = "info",
        timeout_sec: float = 5.0,
    ):
        self.name = name
        self.dry_run = dry_run
        self.sound = sound
        self.min_severity = min_severity
        self.timeout_sec = timeout_sec
        self.sent: list[list[str] | None] = []  # dry-run record: command, or None for log-only

    def command(self, alert: AlertRecord) -> list[str] | None:
        r = alert.request
        title = f"[{r.severity}] {r.title}"
        body = r.body or r.topic_id
        if sys.platform == "darwin" and shutil.which("osascript"):
            return ["osascript", *(OSASCRIPT_SOUND if self.sound else OSASCRIPT), title, body]
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            urgency = {"info": "low", "warning": "normal", "urgent": "critical"}[r.severity]
            return ["notify-send", "-u", urgency, "-a", "vision-sentinel", title, body]
        return None

    def send(self, alert: AlertRecord) -> None:
        cmd = self.command(alert)
        r = alert.request
        log.info("alert %s [%s] %s: %s", alert.id, r.severity, r.title, r.body)
        if self.dry_run:
            self.sent.append(cmd)
            return
        if cmd is None:
            print(f"[vision-sentinel] [{r.severity}] {r.topic_id}: {r.title} {r.body}".rstrip(),
                  flush=True)
            return
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_sec)
        if done.returncode != 0:
            raise RuntimeError(f"{cmd[0]} exited {done.returncode}: {done.stderr.strip()[:200]}")
