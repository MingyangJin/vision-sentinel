"""Example helper tool: list the bundle's files and sizes (deterministic, no model calls).

Helper tools run with cwd = the bundle directory, a scrubbed environment (VS_BUNDLE_ID,
VS_CAMERA_IDS, VS_WINDOW_START, VS_WINDOW_END, ...) and a 30 s timeout. They print one JSON
object to stdout, which lands in tool_outputs.json for the Vision Sub-Agent.
"""

import json
import os
from pathlib import Path

out = {"cameras": {}}
for cam in os.environ.get("VS_CAMERA_IDS", "").split(","):
    d = Path(cam)
    files = {p.name: p.stat().st_size for p in sorted(d.iterdir())} if d.is_dir() else {}
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").is_file() else {}
    out["cameras"][cam] = {
        "files": files,
        "has_audio": "audio.mp3" in files,
        "audio": {k: v for k, v in meta.items() if "db" in k.lower() or "audio" in k.lower()},
    }
print(json.dumps(out))
