"""``vision-sentinel`` command line.

    vision-sentinel init-topic topics/porch --camera porch --goal "Tell me when a parcel arrives"
    vision-sentinel check --config vision-sentinel.toml [--check]   # one cycle per Topic
    vision-sentinel run   --config vision-sentinel.toml [--check]   # loop + watchdog
    vision-sentinel ui    --config vision-sentinel.toml             # Unified Web UI (+ live loops)
    vision-sentinel watchdog --config vision-sentinel.toml          # separate process
    vision-sentinel chat  --config vision-sentinel.toml --topic porch "ignore the tree shadow"

Config (TOML; relative paths resolve against the config file's directory):

    topics = ["topics/porch"]
    recordings_root = "data/recordings"
    harness = "antigravity"            # or "fake" (no model calls), or "package.module:Class"
    [harness_options]                  # keyword arguments for the harness
    [streamhub]                        # keyword arguments for vs_streamhub.StreamHub
    [[cameras]]                        # vs_runtime.contracts.CameraConfig; never put a password here
    camera_id = "porch"
    adapter = "file"
    source = "media/porch.mp4"
    [notify]                           # vs_notify config; or url = "http://127.0.0.1:8790"
"""

from __future__ import annotations

import argparse
import errno
import importlib
import json
import logging
import re
import signal
import sys
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vs_runtime.contracts import (
    AgentHarness,
    CameraConfig,
    Notifier,
    OrchestratorEvent,
    StreamHubClient,
)
from vs_runtime.loop import TickOutcome, TopicLoop
from vs_runtime.manager import TopicManager
from vs_runtime.topic import CONFIG, Topic, init_topic
from vs_runtime.watchdog import Watchdog

log = logging.getLogger("vision-sentinel")

_URL_USERINFO = re.compile(r"(\w+://)[^/@\s]+@")


def redact(value: Any) -> Any:
    """Mask URL userinfo and header/secret-looking values in anything printed by --check."""
    if isinstance(value, str):
        return _URL_USERINFO.sub(r"\1***@", value)
    if isinstance(value, dict):
        return {k: "***" if re.search(r"auth|token|secret|password|key|headers", k, re.I)
                and k != "credential_ref" else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[str] = Field(default_factory=list)
    topics_dir: str | None = None
    recordings_root: str = "data/recordings"
    harness: str = "antigravity"
    harness_options: dict[str, Any] = Field(default_factory=dict)
    cameras: list[CameraConfig] = Field(default_factory=list)
    streamhub: dict[str, Any] = Field(default_factory=dict)
    notify: dict[str, Any] = Field(default_factory=dict)
    watchdog_interval_sec: float = 15.0

    @field_validator("cameras", mode="before")
    @classmethod
    def _cameras_by_id(cls, value: Any) -> Any:
        """Accept ``[cameras.<camera_id>]`` tables as well as ``[[cameras]]`` (same as the Stream Hub)."""
        if isinstance(value, dict):
            return [{"camera_id": cid, **cam} for cid, cam in value.items()]
        return value


class App:
    """Everything one config describes, constructed lazily (peer packages import late)."""

    def __init__(self, config: Path, harness: str | None = None, only: list[str] | None = None):
        self.config_path = config.resolve()
        self.base = self.config_path.parent
        self.settings = Settings.model_validate(tomllib.loads(self.config_path.read_text()))
        if harness:
            self.settings.harness = harness
        loaded: dict[str, Topic] = {}
        for t in self.settings.topics:
            topic = Topic(self._path(t))
            loaded[topic.id] = topic
        if self.settings.topics_dir:
            root = self._path(self.settings.topics_dir)
            if root.is_dir():
                for d in sorted(root.iterdir()):
                    if d.is_dir() and (d / CONFIG).is_file():
                        topic = Topic(d)
                        loaded.setdefault(topic.id, topic)
        self.topics = list(loaded.values())
        if only:
            self.topics = [t for t in self.topics if t.id in only]
            if not self.topics:
                raise SystemExit(f"no topic named {only} in {config}")
        self.loops: dict[str, TopicLoop] = {}
        self._harness: AgentHarness | None = None
        self._manager: TopicManager | None = None

    @property
    def topics_root(self) -> Path:
        if self.settings.topics_dir:
            return self._path(self.settings.topics_dir)
        if self.topics:
            return self.topics[0].dir.parent
        return (self.base / "topics").resolve()

    def _path(self, p: str) -> Path:
        q = Path(p).expanduser()
        return (q if q.is_absolute() else self.base / q).resolve()

    def cameras(self) -> list[CameraConfig]:
        cams = []
        for c in self.settings.cameras:
            if c.adapter == "file" and c.source and not Path(c.source).is_absolute():
                c = c.model_copy(update={"source": str(self._path(c.source))})
            cams.append(c)
        return cams

    def speaker_cameras(self) -> set[str]:
        return {c.camera_id for c in self.cameras() if c.speaker_enabled}

    def harness(self) -> AgentHarness:
        if self._harness is not None:
            return self._harness
        name, opts = self.settings.harness, self.settings.harness_options
        if name == "fake":
            from vs_runtime.fakes import FakeHarness

            self._harness = FakeHarness(**opts)
        elif name == "antigravity":
            from vs_harness_antigravity import AntigravityHarness

            self._harness = AntigravityHarness(**opts)
        else:
            module, _, cls = name.partition(":")
            self._harness = getattr(importlib.import_module(module), cls)(**opts)
        return self._harness

    def hub(self):
        from vs_streamhub import StreamHub

        return StreamHub(self.cameras(), self._path(self.settings.recordings_root),
                         **self.settings.streamhub)

    def _resolve_topic_dir(self, topic_id: str) -> Path | None:
        if self._manager is not None:
            try:
                return self._manager.get_topic(topic_id).dir
            except (FileNotFoundError, ValueError):
                pass
        for t in self.topics:
            if t.id == topic_id:
                return t.dir
        return None

    def notifier(self, hub: Any = None) -> Notifier:
        cfg = dict(self.settings.notify)
        if url := cfg.pop("url", None):
            from vs_notify import NotifyClient

            return NotifyClient(base_url=url)
        from vs_notify import NotifyConfig, NotifyService, Store, build_sinks

        if cfg.get("db") and cfg["db"] != ":memory:":
            cfg["db"] = str(self._path(cfg["db"]))
        ncfg = NotifyConfig.model_validate(cfg)
        sinks = build_sinks(ncfg.sinks)
        play_alert = getattr(hub, "play_alert", None) if hub is not None else None
        if callable(play_alert):
            opted_in = {c.camera_id: c.volume_cap for c in self.cameras() if c.speaker_enabled}
            for sink in sinks:
                bind = getattr(sink, "bind_play_alert", None)
                if callable(bind):
                    bind(play_alert)
                    if not getattr(sink, "enabled_cameras", None):
                        sink.enabled_cameras = set(opted_in)
                    for cid, cap in opted_in.items():
                        sink.volume_caps.setdefault(cid, cap)
        return NotifyService(
            Store(ncfg.db_path()), sinks, topic_dir_for=self._resolve_topic_dir,
            on_feedback=self._on_feedback, default_cooldown_sec=ncfg.cooldown_sec,
        )

    def _on_feedback(self, feedback, record) -> None:
        loop = self.loops.get(record.request.topic_id)
        if loop:
            loop.post_event(OrchestratorEvent(
                topic_id=record.request.topic_id, kind="feedback",
                message=json.dumps({"alert_id": record.id, "title": record.request.title,
                                    "bundle_id": record.request.bundle_id,
                                    **feedback.model_dump(mode="json")}),
            ))

    def plan(self) -> dict[str, Any]:
        try:
            cams = self.hub().plan()
        except Exception:
            cams = [c.model_dump(mode="json") for c in self.cameras()]
        notify = dict(self.settings.notify)
        return redact({
            "config": str(self.config_path),
            "harness": {"name": self.settings.harness,
                        "options": sorted(self.settings.harness_options)},
            "recordings_root": str(self._path(self.settings.recordings_root)),
            "cameras": cams,
            "notify": {**notify, "sinks": [s.get("type") for s in notify.get("sinks", [])]},
            "topics": [{
                "id": t.id, "dir": str(t.dir), "camera_ids": t.config.camera_ids,
                "cadence_sec": t.config.cadence_sec, "window_sec": t.config.window_sec,
                "paused": t.config.paused, "speaker_enabled": t.config.speaker_enabled,
                "main_model": t.config.main_model.model_dump(),
                "vision_model": t.config.vision_model.model_dump(),
                "review_policy": t.config.review_policy.model_dump(),
                "tools": [p.name for p in t.tools()],
                "goal": t.goal().strip().splitlines()[:1],
            } for t in self.topics],
        })

    def build_loops(self, hub: StreamHubClient, notifier: Notifier) -> dict[str, TopicLoop]:
        self._harness = harness = self.harness()
        self.loops = {
            t.id: TopicLoop(t, harness, hub, notifier, speaker_cameras=self.speaker_cameras)
            for t in self.topics
        }
        return self.loops

    def manager(
        self,
        hub: StreamHubClient | None = None,
        notifier: Notifier | None = None,
        *,
        on_topic_created=None,
    ) -> TopicManager:
        self._harness = harness = self.harness()
        self._manager = TopicManager(
            self.topics_root,
            topics=self.topics,
            harness=harness,
            hub=hub,
            notifier=notifier,
            loops=self.loops,
            speaker_cameras=self.speaker_cameras,
            on_topic_created=on_topic_created,
        )
        return self._manager

    def close(self) -> None:
        """Stop in-flight harness calls (kills their process groups) if the harness can."""
        close = getattr(getattr(self, "_harness", None), "close", None)
        if close:
            try:
                close()
            except Exception:
                log.exception("harness close failed")


def wait_ready(hub: StreamHubClient, cameras: list[str], timeout: float) -> dict[str, str]:
    if not cameras:
        return {}
    deadline = time.monotonic() + timeout
    while True:
        status = {c: hub.health(c).status for c in cameras}
        if all(s == "ok" for s in status.values()) or time.monotonic() > deadline:
            return status
        time.sleep(1.0)


def summarize(topic_id: str, o: TickOutcome) -> dict[str, Any]:
    r = o.report
    return {
        "topic": topic_id, "bundle_id": o.bundle.bundle_id, "bundle_dir": o.bundle.path,
        "camera_status": o.bundle.camera_status, "assets": [a.path for a in o.bundle.assets],
        "degraded": r.degraded, "status": r.status, "summary": r.summary,
        "conditions": r.conditions, "delivered": o.delivered, "held": o.held,
        "late_replayed": o.late_replayed, "actions": o.actions,
        "vision_usage": r.usage.model_dump(),
    }


def cmd_init_topic(a: argparse.Namespace) -> int:
    goal = Path(a.goal_file).read_text() if a.goal_file else (a.goal or "")
    if not a.template and (not a.camera or not goal):
        raise SystemExit("init-topic needs --camera and --goal (or --from a template)")
    t = init_topic(Path(a.path), a.camera or [], goal, topic_id=a.id,
                   template=Path(a.template) if a.template else None,
                   cadence_sec=a.cadence, window_sec=a.window)
    print(json.dumps({"topic": t.id, "dir": str(t.dir), "camera_ids": t.config.camera_ids}))
    return 0


def _start(app: App):
    hub = app.hub()
    hub.start()
    cams = sorted(
        {c for t in app.topics for c in t.config.camera_ids}
        | {c.camera_id for c in app.cameras()}
    )
    window = max((t.config.window_sec for t in app.topics), default=0)
    return hub, cams, window


def cmd_check(a: argparse.Namespace) -> int:
    app = App(Path(a.config), a.harness, a.topic)
    if a.check:
        print(json.dumps(app.plan(), indent=2))
        return 0
    if not app.topics:
        raise SystemExit(f"no topics configured in {a.config}")
    hub, cams, window = _start(app)
    try:
        status = wait_ready(hub, cams, window + 15)
        log.info("camera status before slicing: %s", status)
        time.sleep(window if a.settle is None else a.settle)  # let the buffer cover the window
        loops = app.build_loops(hub, app.notifier(hub))
        if len(loops) > 1:
            items = list(loops.items())
            with ThreadPoolExecutor(max_workers=len(items)) as pool:
                outcomes = list(pool.map(lambda kv: (kv[0], kv[1].tick()), items))
            results = [summarize(tid, o) for tid, o in outcomes]
        else:
            results = [summarize(tid, lp.tick()) for tid, lp in loops.items()]
    finally:
        app.close()
        hub.stop()
    print(json.dumps(results, indent=2, default=str))
    return 0 if all(not r["degraded"] for r in results) else 2


def run_app(app: App, stop: threading.Event) -> None:
    """Hub, concurrent Topic loops and in-process watchdog until ``stop`` is set."""
    if not app.topics:
        raise SystemExit(f"no topics configured in {app.config_path}")
    hub, cams, window = _start(app)
    threads: list[threading.Thread] = []
    try:
        wait_ready(hub, cams, window + 15)
        stop.wait(window)  # let a fresh rolling buffer cover the first window
        notifier = app.notifier(hub)
        loops = app.build_loops(hub, notifier)
        watchdog = Watchdog(
            [Topic(t.dir) for t in app.topics], hub, notifier,
            on_event=lambda tid, ev: loops[tid].post_event(ev) if tid in loops else None,
        )
        threads = [threading.Thread(target=lp.run, args=(stop,), name=f"topic:{tid}", daemon=True)
                   for tid, lp in loops.items()]
        threads.append(threading.Thread(
            target=watchdog.run, args=(stop, app.settings.watchdog_interval_sec),
            name="watchdog", daemon=True))
        for th in threads:
            th.start()
        log.info("running %d topic(s): %s", len(loops), ", ".join(loops))
        while not stop.wait(1.0):
            pass
        log.info("stopping")
    finally:
        stop.set()
        app.close()  # interrupt in-flight agent calls so no harness child outlives us
        for th in threads:
            th.join(timeout=10)
        hub.stop()


def cmd_run(a: argparse.Namespace) -> int:
    app = App(Path(a.config), a.harness, a.topic)
    if a.check:
        print(json.dumps(app.plan(), indent=2))
        return 0
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run_app(app, stop)
    return 0


def cmd_ui(a: argparse.Namespace) -> int:
    """Launch the Unified Web UI (vs_ui) alongside the Stream Hub and optional live loops."""
    from vs_ui.server import serve_ui

    app = App(Path(a.config), a.harness, a.topic)
    stop = threading.Event()
    threads: list[threading.Thread] = []
    hub, cams, window = _start(app)
    try:
        notifier = app.notifier(hub)
        loops = app.build_loops(hub, notifier)
        watchdog: Watchdog | None = None

        def _spawn_topic(topic: Topic) -> None:
            if a.no_loop or stop.is_set() or topic.id in loops:
                return
            lp = TopicLoop(
                topic, app.harness(), hub, notifier, speaker_cameras=app.speaker_cameras
            )
            loops[topic.id] = lp
            if watchdog is not None:
                watchdog.topics.append(Topic(topic.dir))
            th = threading.Thread(target=lp.run, args=(stop,), name=f"topic:{topic.id}", daemon=True)
            threads.append(th)
            th.start()

        manager = app.manager(hub, notifier, on_topic_created=_spawn_topic)
        if not a.no_loop:
            watchdog = Watchdog(
                [Topic(t.dir) for t in app.topics], hub, notifier,
                on_event=lambda tid, ev: loops[tid].post_event(ev) if tid in loops else None,
            )

            def _start_loops() -> None:
                wait_ready(hub, cams, window + 15)
                if window:
                    stop.wait(window)
                if stop.is_set():
                    return
                for tid, lp in list(loops.items()):
                    th = threading.Thread(
                        target=lp.run, args=(stop,), name=f"topic:{tid}", daemon=True
                    )
                    threads.append(th)
                    th.start()
                wd_th = threading.Thread(
                    target=watchdog.run, args=(stop, app.settings.watchdog_interval_sec),
                    name="watchdog", daemon=True,
                )
                threads.append(wd_th)
                wd_th.start()

            boot = threading.Thread(target=_start_loops, name="ui-loop-boot", daemon=True)
            threads.append(boot)
            boot.start()

        serve_ui(manager=manager, hub=hub, notifier=notifier, host=a.host, port=a.port,
                 allowed_hosts=a.allow_host)
    finally:
        stop.set()
        app.close()
        for th in threads:
            th.join(timeout=10)
        hub.stop()
    return 0


def cmd_watchdog(a: argparse.Namespace) -> int:
    """Standalone watchdog: a separate process, so a crashed or hung `run` still alerts."""
    app = App(Path(a.config), None, a.topic)
    hub = app.hub()  # not started: health comes from the age of the newest segment on disk
    wd = Watchdog(app.topics, hub, app.notifier(), startup_grace=False)
    if a.once:
        print(json.dumps(wd.check_once(), indent=2))
        return 0
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    wd.run(stop, a.interval or app.settings.watchdog_interval_sec)
    return 0


def cmd_chat(a: argparse.Namespace) -> int:
    app = App(Path(a.config), a.harness, [a.topic])
    from vs_runtime.fakes import FakeHub

    loop = app.build_loops(FakeHub(), app.notifier())[a.topic]  # chat never slices media
    result = loop.chat(a.message)
    print(result.reply)
    return 0 if result.ok else 1


def cmd_devices(a: argparse.Namespace) -> int:
    from vs_streamhub.adapters.webcam import list_webcam_devices

    devices = list_webcam_devices()
    if a.json:
        print(json.dumps([
            {"id": d.id, "name": d.name, "kind": d.kind, "backend": d.backend}
            for d in devices
        ], indent=2))
        return 0
    if not devices:
        print("No local webcams or microphones detected.")
        return 0
    for kind in ("video", "audio"):
        group = [d for d in devices if d.kind == kind]
        if not group:
            continue
        print(f"{kind.title()} devices ({group[0].backend}):")
        for d in group:
            print(f"  [{d.id}] {d.name}")
    print("\nQuickstart (native macOS Camera Client + built-in camera & mic):")
    print("  uv run vision-sentinel ui --config examples/mac-camera.toml")
    print("  ./clients/macos-camera/build-app.sh && open ./clients/macos-camera/dist/VisionSentinelCamera.app")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vision-sentinel", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init-topic", help="create a Topic directory")
    i.add_argument("path")
    i.add_argument("--camera", action="append", help="camera_id (repeat for M cameras)")
    i.add_argument("--goal", help="the monitoring goal, in your own words")
    i.add_argument("--goal-file")
    i.add_argument("--from", dest="template", help="copy a template Topic (e.g. examples/topics/…)")
    i.add_argument("--id")
    i.add_argument("--cadence", type=int)
    i.add_argument("--window", type=int)
    i.set_defaults(fn=cmd_init_topic)

    for name, fn, help_ in (("check", cmd_check, "one cycle per Topic, then exit"),
                            ("run", cmd_run, "continuous loop with watchdog")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--config", default="vision-sentinel.toml")
        s.add_argument("--check", action="store_true", help="print the redacted plan and exit")
        s.add_argument("--topic", action="append", help="only these topic ids")
        s.add_argument("--harness", help="override the config's harness (e.g. fake)")
        if name == "check":
            s.add_argument("--settle", type=float,
                           help="seconds to let a fresh buffer fill before slicing (default: window)")
        s.set_defaults(fn=fn)

    u = sub.add_parser("ui", help="launch the Unified Web UI (live wall, Topics, Main Agent chat)")
    u.add_argument("--config", default="vision-sentinel.toml")
    u.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1; do not expose publicly)")
    u.add_argument("--port", type=int, default=8080, help="bind port (default 8080)")
    u.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="extra Host name to accept on a loopback bind (e.g. your private tunnel's name)")
    u.add_argument("--topic", action="append", help="only these topic ids")
    u.add_argument("--harness", help="override the config's harness (e.g. fake)")
    u.add_argument("--no-loop", action="store_true", help="serve UI + live cameras without running Topic ticks")
    u.set_defaults(fn=cmd_ui)

    w = sub.add_parser("watchdog", help="standalone watchdog process, run beside `run`")
    w.add_argument("--config", default="vision-sentinel.toml")
    w.add_argument("--topic", action="append", help="only these topic ids")
    w.add_argument("--interval", type=float, help="seconds between checks")
    w.add_argument("--once", action="store_true", help="one check, printed as JSON")
    w.set_defaults(fn=cmd_watchdog)

    c = sub.add_parser("chat", help="send one message to a Topic's Main Agent")
    c.add_argument("--config", default="vision-sentinel.toml")
    c.add_argument("--topic", required=True)
    c.add_argument("--harness")
    c.add_argument("message")
    c.set_defaults(fn=cmd_chat)

    d = sub.add_parser("devices", help="list local built-in/USB webcams and microphones")
    d.add_argument("--json", action="store_true", help="output machine-readable JSON")
    d.set_defaults(fn=cmd_devices)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return a.fn(a)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        log.error("a port this config needs is already in use (%s). Is another vision-sentinel "
                  "running? Stop it, or change the port in the config.", e.strerror)
        return 2


if __name__ == "__main__":
    sys.exit(main())
