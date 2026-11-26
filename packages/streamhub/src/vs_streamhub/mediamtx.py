"""MediaMTX: the hub's single-pull restream and rolling recorder (ARCHITECTURE §4, §5.1).

One MediaMTX process serves every camera that uses it. Each camera is one MediaMTX path
that either pulls from the camera (the credentialed URL reaches MediaMTX only through its
environment, ``MTX_PATHS_<PATH>_SOURCE``) or accepts a local publisher (file replay).
MediaMTX records each path as fMP4 segments into ``<recordings_root>/<camera_id>/`` and
serves it at ``rtsp://127.0.0.1:<rtsp_port>/<path>`` to any number of local readers.
The generated config file holds no secret; listeners bind to loopback only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from vs_streamhub import segments
from vs_streamhub.adapters.base import Supervisor


@dataclass
class MediaMTXSettings:
    binary: str | None = None  # default: `mediamtx` on PATH
    host: str = "127.0.0.1"
    rtsp_port: int = 8554
    api_port: int = 9997
    rtmp_enabled: bool = False
    rtmp_host: str = "127.0.0.1"
    rtmp_port: int = 1935
    publish_ips: list[str] = field(default_factory=lambda: ["127.0.0.1", "::1"])
    segment_sec: float = 10.0
    part_sec: float = 1.0
    log_level: str = "warn"

    def resolve_binary(self) -> str | None:
        return self.binary or shutil.which("mediamtx")


@dataclass
class _Path:
    name: str
    record_dir: Path
    source: Callable[[], str] | None = None  # None: local/push publisher
    extra_env: Callable[[str], dict[str, str]] | None = None


def path_name(camera_id: str, taken: set[str]) -> str:
    """A MediaMTX path name for a camera: lowercase letters and digits only, because
    MediaMTX splits environment keys (MTX_PATHS_<NAME>_SOURCE) on underscores."""
    base = re.sub(r"[^a-z0-9]", "", camera_id.lower()) or "cam"
    name, i = base, 2
    while name in taken:
        name, i = f"{base}{i}", i + 1
    return name


@dataclass
class MediaMTXServer:
    settings: MediaMTXSettings
    run_dir: Path
    paths: dict[str, _Path] = field(default_factory=dict)
    _supervisor: Supervisor | None = None

    def add_path(
        self,
        camera_id: str,
        record_dir: Path,
        source: Callable[[], str] | None = None,
        *,
        extra_env: Callable[[str], dict[str, str]] | None = None,
    ) -> str:
        name = path_name(camera_id, set(self.paths))
        self.paths[name] = _Path(name, Path(record_dir), source, extra_env)
        return name

    def enable_rtmp(self, *, host: str | None = None, port: int | None = None, publish_ips: list[str] | None = None) -> None:
        self.settings.rtmp_enabled = True
        if host:
            self.settings.rtmp_host = host
        if port is not None:
            self.settings.rtmp_port = int(port)
        if publish_ips:
            for ip in publish_ips:
                if ip not in self.settings.publish_ips:
                    self.settings.publish_ips.append(ip)

    def config(self) -> dict:
        s = self.settings
        paths = {}
        for p in self.paths.values():
            # MediaMTX requires %path in recordPath; the camera's own directory still holds it.
            entry: dict = {"recordPath": str(p.record_dir.resolve() / f"%path_{segments.MEDIAMTX_PATTERN}")}
            if p.source is None:
                entry["source"] = "publisher"
            paths[p.name] = entry  # pull sources come from the environment, see env()
        local = ["127.0.0.1", "::1"]
        users: list[dict] = [{"user": "any", "ips": local, "permissions": [
            {"action": "publish"}, {"action": "read"}, {"action": "api"}]}]
        extra_pub = [ip for ip in s.publish_ips if ip not in local]
        if extra_pub:
            users.append({"user": "any", "ips": extra_pub, "permissions": [{"action": "publish"}]})
        cfg: dict = {
            "logLevel": s.log_level, "logDestinations": ["stdout"],
            "rtsp": True, "rtspTransports": ["tcp"], "rtspAddress": f"{s.host}:{s.rtsp_port}",
            "rtmp": bool(s.rtmp_enabled), "hls": False, "webrtc": False, "srt": False, "moq": False,
            "api": True, "apiAddress": f"{s.host}:{s.api_port}",
            "metrics": False, "pprof": False, "playback": False,
            "authInternalUsers": users,
            "pathDefaults": {
                "sourceOnDemand": False, "rtspTransport": "tcp",
                "record": True, "recordFormat": "fmp4",
                "recordPartDuration": f"{s.part_sec:g}s", "recordSegmentDuration": f"{s.segment_sec:g}s",
                "recordDeleteAfter": "0s",  # the hub prunes by its own retention
            },
            "paths": paths,
        }
        if s.rtmp_enabled:
            cfg["rtmpAddress"] = f"{s.rtmp_host}:{s.rtmp_port}"
        return cfg

    def write_config(self) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        cfg = self.run_dir / "mediamtx.yml"
        cfg.write_text(json.dumps(self.config(), indent=2) + "\n")  # JSON is valid YAML
        return cfg

    def env(self) -> dict[str, str]:
        """MediaMTX's extra environment: each pulled path's camera URL. Secret: never log."""
        out: dict[str, str] = {}
        for p in self.paths.values():
            if p.source:
                out[f"MTX_PATHS_{p.name.upper()}_SOURCE"] = p.source()
            if p.extra_env:
                out.update(p.extra_env(p.name))
        return out

    def command(self) -> tuple[list[str], dict[str, str]]:
        binary = self.settings.resolve_binary()
        if not binary:
            raise FileNotFoundError("mediamtx not found on PATH")
        for p in self.paths.values():
            p.record_dir.mkdir(parents=True, exist_ok=True)
        return [binary, str(self.write_config())], {**os.environ, **self.env()}

    def preflight(self) -> None:
        """Refuse to start beside another listener on our ports: MediaMTX would fail to bind,
        and our API/restream URLs would reach the other instance instead."""
        s = self.settings
        checks: list[tuple[str, str, int]] = [
            ("rtsp_port", s.host, s.rtsp_port),
            ("api_port", s.host, s.api_port),
        ]
        if s.rtmp_enabled:
            checks.append(("rtmp_port", s.rtmp_host, s.rtmp_port))
        for option, bind_host, port in checks:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind((bind_host, port))
                except OSError:
                    raise RuntimeError(f"{bind_host}:{port} is already in use (another MediaMTX?); "
                                       f"choose a free port with the StreamHub option {option}=...") from None  # fmt: skip

    def start(self) -> None:
        if self._supervisor is None:
            self.preflight()
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self._supervisor = Supervisor("mediamtx", self.command, cwd=self.run_dir)
            self._supervisor.start()

    def stop(self) -> None:
        if self._supervisor:
            self._supervisor.stop()
            self._supervisor = None

    @property
    def running(self) -> bool:
        return bool(self._supervisor and self._supervisor.running)

    @property
    def last_error(self) -> str:
        return self._supervisor.last_error if self._supervisor else ""

    def read_url(self, name: str) -> str:
        return f"rtsp://{self.settings.host}:{self.settings.rtsp_port}/{name}"

    publish_url = read_url

    def rtmp_publish_url(self, name: str) -> str:
        return f"rtmp://{self.settings.rtmp_host}:{self.settings.rtmp_port}/{name}"

    def api(self, endpoint: str) -> dict | None:
        url = f"http://{self.settings.host}:{self.settings.api_port}/v3/{endpoint}"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return json.loads(resp.read())
        except (OSError, urllib.error.URLError, ValueError):
            return None

    def ready(self, name: str) -> bool:
        info = self.api(f"paths/get/{name}")
        return bool(info and info.get("ready"))
