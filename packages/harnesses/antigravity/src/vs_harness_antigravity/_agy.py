"""Launch the official ``agy`` binary and parse its ``stream-json`` output.

``agy`` stays the only client of the user's sign-in: this module starts the process
and reads its stdout, nothing more. It never reads, copies or forwards credentials,
and it passes the child only an allowlisted environment (no API keys, no camera
secrets). Output format, verified on agy 1.5.17 (2026-11-25):

* ``step_update`` events: ``agent_response`` steps carry per-step ``usage`` on DONE;
  ``tool`` steps carry ``tool_name`` and ``tool_info.parameters`` (state DONE does
  not mean the call was allowed: denied calls are listed in the result).
* one ``result`` event per turn: ``conversation_id``, ``status``, ``response``,
  ``structured_output`` (with ``--json-schema``), ``denied_actions`` and ``usage``.
  On ``--resume`` the result's usage is cumulative over the whole conversation, so
  per-turn usage is summed from this invocation's steps instead.
* ``--resume <unknown id>`` does not fail: agy warns on stderr and starts a NEW
  conversation, so callers must compare the returned ``conversation_id``.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vs_runtime.contracts import TokenUsage

ENV_ALLOWLIST = (
    "HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TZ", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
)


class AgyError(RuntimeError):
    pass


class AgyTimeout(AgyError):
    pass


class AgyClosed(AgyError):
    pass


def find_agy(binary: str | None = None) -> str:
    """The official binary: an explicit path, ``$VS_AGY_BIN``, ``agy`` on PATH, ~/.local/bin."""
    for cand in (binary, os.environ.get("VS_AGY_BIN"), shutil.which("agy")):
        if cand:
            return cand
    local = Path.home() / ".local" / "bin" / "agy"
    if local.exists():
        return str(local)
    raise AgyError("official Antigravity CLI `agy` not found (install it, or set VS_AGY_BIN)")


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}
    env.update(extra or {})
    return env


@dataclass
class ToolCall:
    name: str
    params: dict[str, Any]
    output: str = ""


@dataclass
class AgyRun:
    returncode: int
    wall_s: float
    stderr: str
    conversation_id: str = ""
    status: str = ""
    response: str = ""
    structured_output: Any = None
    denied: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    result_usage: dict[str, Any] = field(default_factory=dict)
    has_result: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.has_result and self.status == "SUCCESS"

    def error(self) -> str:
        if self.ok:
            return ""
        tail = " ".join(self.stderr.split())[-600:]
        if not self.has_result:
            return f"agy exited {self.returncode} without a result event: {tail}".strip()
        return f"agy status {self.status or '?'} (exit {self.returncode}): {tail}".strip()


def parse_stream(stdout: str, run: AgyRun) -> AgyRun:
    step_usage: dict[int, dict[str, Any]] = {}
    tools: dict[int, ToolCall] = {}
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("event") == "init" and not run.conversation_id:
            run.conversation_id = ev.get("conversation_id") or ""
        elif ev.get("event") == "step_update":
            st = ev.get("step_update") or {}
            idx = st.get("step_index", -1)
            if st.get("step_type") == "tool":
                info = st.get("tool_info") or {}
                tools[idx] = ToolCall(
                    name=st.get("tool_name") or info.get("name") or "",
                    params=info.get("parameters") or {},
                    output=str(info.get("output") or ""),
                )
            if st.get("state") == "DONE" and isinstance(st.get("usage"), dict):
                step_usage[idx] = st["usage"]
        elif ev.get("event") == "result":
            res = ev.get("result") or {}
            run.has_result = True
            run.conversation_id = res.get("conversation_id") or run.conversation_id
            run.status = res.get("status") or ""
            run.response = res.get("response") or ""
            run.structured_output = res.get("structured_output")
            run.result_usage = res.get("usage") or {}
            run.denied = [d.get("action", "") for d in res.get("denied_actions") or []]
    run.tool_calls = [tools[i] for i in sorted(tools)]
    totals = list(step_usage.values()) or [run.result_usage]
    run.usage = TokenUsage(
        input=sum(int(u.get("input_tokens") or 0) for u in totals),
        cached=sum(int(u.get("cache_read_tokens") or 0) for u in totals),
        output=sum(int(u.get("output_tokens") or 0) for u in totals),
        thinking=sum(int(u.get("thinking_tokens") or 0) for u in totals),
        duration_s=round(run.wall_s, 3),
    )
    return run


def run_agy(
    binary: str,
    args: list[str],
    *,
    cwd: str | Path,
    timeout_s: float,
    env: dict[str, str],
    registry: "ProcessRegistry | None" = None,
) -> AgyRun:
    """Run one ``agy`` invocation to completion; kill its process group on timeout.
    With a registry, ``registry.close()`` from another thread ends the run early."""
    reg = registry or ProcessRegistry()
    t0 = time.monotonic()
    proc = reg.spawn(
        [binary, *args],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise AgyTimeout(f"agy timed out after {timeout_s:g} s") from None
    finally:
        reg.forget(proc)
    if reg.closed:
        raise AgyClosed("harness closed while agy was running; the turn was cancelled")
    run = AgyRun(returncode=proc.returncode, wall_s=time.monotonic() - t0, stderr=err or "")
    return parse_stream(out or "", run)


class ProcessRegistry:
    """Live ``agy`` processes of one harness, so shutdown can end them (no orphans
    spending quota after the runtime exits)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: set[subprocess.Popen] = set()
        self.closed = False

    def spawn(self, argv: list[str], **kw: Any) -> subprocess.Popen:
        with self._lock:
            if self.closed:
                raise AgyClosed("harness is closed")
            proc = subprocess.Popen(argv, **kw)
            self._procs.add(proc)
            return proc

    def forget(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.discard(proc)

    def close(self, grace_s: float = 3.0) -> None:
        """Idempotent and thread-safe: SIGTERM every live agy process group, SIGKILL after grace."""
        with self._lock:
            self.closed = True
            procs = list(self._procs)
        for p in procs:
            _signal_group(p, signal.SIGTERM)
        deadline = time.monotonic() + grace_s
        for p in procs:
            while p.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if p.poll() is None:
                _signal_group(p, signal.SIGKILL)


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_group(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            proc.communicate(timeout=3)
            break
        except subprocess.TimeoutExpired:
            continue
