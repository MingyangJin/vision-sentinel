"""Bundle Builder: slice a window from the Stream Hub, run the Topic's helper tools, and
write an immutable Observation Bundle (ARCHITECTURE.md §4 step 2, §5.2, §8.4).

Bundle layout, ``<topic>/state/bundles/<bundle_id>/``:

    bundle.json           the ObservationBundle (asset paths are bundle-relative)
    tool_outputs.json     tool name -> {ok, output, error, duration_s}
    <camera_id>/...       whatever the Stream Hub wrote for that camera

Helper tools run with cwd = the bundle directory, a scrubbed environment (no camera
credentials, nothing inherited beyond PATH/HOME/locale) and a hard timeout. A tool that
times out or fails marks the bundle degraded; the tick goes on.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from vs_runtime.contracts import (
    MediaCapabilities,
    ObservationAsset,
    ObservationBundle,
    StreamHubClient,
    utcnow,
)
from vs_runtime.topic import Topic

PASSTHROUGH_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
MAX_OUTPUT = 64 * 1024


def tool_env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: os.environ[k] for k in PASSTHROUGH_ENV if k in os.environ}
    return env | extra


def _command(tool: Path) -> list[str]:
    if tool.suffix == ".py":
        return [sys.executable, str(tool)]
    if tool.suffix == ".sh":
        return ["/bin/sh", str(tool)]
    return [str(tool)]


def run_tool(tool: Path, cwd: Path, env: dict[str, str], timeout: float) -> dict[str, Any]:
    """Run one helper. stdout is parsed as JSON when it is JSON, else kept as text."""
    t0 = time.monotonic()
    proc = subprocess.Popen(
        _command(tool), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)  # the whole group, grandchildren included
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return {"ok": False, "output": None, "error": f"timed out after {timeout:g}s",
                "duration_s": round(time.monotonic() - t0, 3)}
    out = out[:MAX_OUTPUT]
    try:
        output: Any = json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        output = out
    result = {"ok": proc.returncode == 0, "output": output, "error": "",
              "duration_s": round(time.monotonic() - t0, 3)}
    if proc.returncode != 0:
        result["error"] = f"exit {proc.returncode}: {err.strip()[-500:]}"
    return result


def run_tools(
    topic: Topic, bundle_dir: Path, bundle: ObservationBundle, only: str | None = None
) -> dict[str, dict[str, Any]]:
    env = tool_env({
        "VS_TOPIC_ID": topic.id,
        "VS_BUNDLE_ID": bundle.bundle_id,
        "VS_BUNDLE_DIR": str(bundle_dir),
        "VS_CAMERA_IDS": ",".join(bundle.camera_ids),
        "VS_WINDOW_START": bundle.window_start.isoformat(),
        "VS_WINDOW_END": bundle.window_end.isoformat(),
    })
    tools = [t for t in topic.tools() if only is None or t.name == Path(only).name]
    return {
        t.name: run_tool(t, bundle_dir, env, topic.config.tool_timeout_sec) for t in tools
    }


def _freeze(path: Path) -> None:
    for p in path.rglob("*"):
        if p.is_file():
            p.chmod(0o444)


class BundleBuilder:
    def __init__(self, topic: Topic, hub: StreamHubClient, capabilities: MediaCapabilities):
        self.topic = topic
        self.hub = hub
        self.capabilities = capabilities

    def build(self, now: datetime | None = None) -> ObservationBundle:
        topic, cfg = self.topic, self.topic.config
        end = now or utcnow()
        start = end - timedelta(seconds=cfg.window_sec)
        bundle_id = f"{end:%Y%m%dT%H%M%S}Z-{uuid.uuid4().hex[:6]}"
        bdir = topic.bundles_dir / bundle_id
        bdir.mkdir(parents=True)
        # Keep "video" when configured even if the vision harness does not ingest video directly,
        # so Alert Inbox & mobile clients always have an MP4 clip for human playback.
        kinds = {k for k in cfg.asset_kinds if self.capabilities.allows(k) or k == "video"}
        bundle = ObservationBundle(
            bundle_id=bundle_id, topic_id=topic.id, camera_ids=list(cfg.camera_ids),
            window_start=start, window_end=end, path=str(bdir),
        )
        def _slice_one(cam: str):
            try:
                status = self.hub.health(cam).status
                assets = self.hub.extract_window(cam, start, end, bdir / cam, kinds)
                return cam, status, assets, None
            except Exception as e:  # a hub failure on one camera must not stop the tick
                return cam, "offline", [], f"{cam}: slicing failed: {type(e).__name__}: {e}"

        if len(cfg.camera_ids) > 1:
            with ThreadPoolExecutor(max_workers=len(cfg.camera_ids)) as pool:
                sliced = list(pool.map(_slice_one, cfg.camera_ids))
        else:
            sliced = [_slice_one(cam) for cam in cfg.camera_ids]

        for cam, status, assets, err in sliced:
            if err:
                bundle.notes.append(err)
            bundle.camera_status[cam] = status
            bundle.assets += [
                ObservationAsset(**a.model_dump() | {"path": f"{cam}/{a.path}"}) for a in assets
            ]
            if status != "ok":
                bundle.notes.append(f"{cam}: {status}")
        bundle.degraded = any(s != "ok" for s in bundle.camera_status.values())

        (bdir / "bundle.json").write_text(bundle.model_dump_json(indent=2))
        outputs = run_tools(topic, bdir, bundle)
        for name, r in outputs.items():
            if not r["ok"]:
                bundle.degraded = True
                bundle.notes.append(f"tool {name}: {r['error']}")
        bundle.tool_outputs = {name: r["output"] for name, r in outputs.items() if r["ok"]}
        (bdir / "tool_outputs.json").write_text(json.dumps(outputs, indent=2, default=str))
        (bdir / "bundle.json").write_text(bundle.model_dump_json(indent=2))
        _freeze(bdir)
        return bundle


def prune_bundles(topic: Topic, now: datetime | None = None) -> int:
    """Delete bundles older than the retention; never touches state/saved/."""
    if not topic.bundles_dir.is_dir():
        return 0
    cutoff = (now or utcnow()).timestamp() - topic.config.bundle_retention_hours * 3600
    removed = 0
    for d in topic.bundles_dir.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed
