"""A stand-in for the ``agy`` binary, driven by a scenario file.

``$FAKE_AGY_DIR/scenario.json`` is a list of turn specs consumed in order (the last one
repeats). Every call is logged to ``$FAKE_AGY_DIR/calls.jsonl`` with its argv, cwd and
environment keys. Spec keys: cid ("$resume" echoes --resume), output, status, reads,
tools, denied, sleep, exit, stderr, usage, result_usage, no_result, response.
"""

import json
import os
import sys
import time
from pathlib import Path

argv = sys.argv[1:]

if argv[:1] == ["--version"]:
    print("1.5.17")
    sys.exit(0)
if argv[:1] == ["models"]:
    print("Fetching available models...")
    print("gemini-4-neon-medium\tGemini 4 Neon (Medium)")
    print("gemini-4-hydrogen-low\tGemini 4 Hydrogen (Low)")
    sys.exit(0)

d = Path(os.environ["FAKE_AGY_DIR"])
counter = d / "counter"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
specs = json.loads((d / "scenario.json").read_text())
spec = specs[min(n, len(specs) - 1)]

t0 = time.time()
with open(d / "pids", "a") as fh:
    fh.write(f"{os.getpid()}\n")
if spec.get("grandchild"):  # a child process that only a process-group kill reaches
    import subprocess

    child = subprocess.Popen(["sleep", str(spec.get("sleep", 30))])
    with open(d / "pids", "a") as fh:
        fh.write(f"{child.pid}\n")
    child.wait()
else:
    time.sleep(spec.get("sleep", 0))


def flag(name):
    return argv[argv.index(name) + 1] if name in argv else None


with open(d / "calls.jsonl", "a") as fh:
    fh.write(json.dumps({
        "argv": argv, "cwd": os.getcwd(), "env": sorted(os.environ), "start": t0, "end": time.time(),
    }) + "\n")

cid = spec.get("cid", "conv-1")
if cid == "$resume":
    cid = flag("--resume")


def emit(ev):
    print(json.dumps(ev), flush=True)


emit({"event": "init", "conversation_id": cid, "init": {"cwd": os.getcwd()}})
emit({"event": "step_update", "step_update": {"step_index": 0, "state": "DONE", "step_type": "user_input"}})
usage = spec.get("usage", {"input_tokens": 100, "output_tokens": 10, "thinking_tokens": 5,
                           "cache_read_tokens": 50, "total_tokens": 110})
emit({"event": "step_update", "step_update": {"step_index": 1, "state": "DONE",
      "step_type": "agent_response", "usage": usage}})
i = 2
for p in spec.get("reads", []):
    path = p if os.path.isabs(p) else os.path.join(os.getcwd(), p)
    for state in ("ACTIVE", "DONE"):
        emit({"event": "step_update", "step_update": {"step_index": i, "state": state, "step_type": "tool",
              "tool_name": "read_file", "tool_info": {"name": "read_file", "parameters": {"Path": path}}}})
    i += 1
for t in spec.get("tools", []):
    emit({"event": "step_update", "step_update": {"step_index": i, "state": "DONE", "step_type": "tool",
          "tool_name": t, "tool_info": {"name": t, "parameters": {}}}})
    i += 1
for w in spec.get("writes", []):
    w = w.replace("$brain", str(Path.home() / ".gemini" / "antigravity-cli" / "brain" / cid))
    emit({"event": "step_update", "step_update": {"step_index": i, "state": "DONE", "step_type": "tool",
          "tool_name": "write_file", "tool_info": {"name": "write_file", "parameters": {"Path": w}}}})
    i += 1
if not spec.get("no_result"):
    result = {"conversation_id": cid, "status": spec.get("status", "SUCCESS"),
              "response": spec.get("response", json.dumps(spec.get("output"))),
              "usage": spec.get("result_usage", usage), "num_turns": 1,
              "denied_actions": [{"action": a} for a in spec.get("denied", [])]}
    if spec.get("output") is not None:
        result["structured_output"] = spec["output"]
    emit({"event": "result", "result": result})
if spec.get("stderr"):
    print(spec["stderr"], file=sys.stderr)
sys.exit(spec.get("exit", 0))
