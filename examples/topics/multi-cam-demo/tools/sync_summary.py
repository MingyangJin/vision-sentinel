"""Deterministic multi-camera sync helper: summarizes per-camera assets, coverage, and health."""

import json
import os
from pathlib import Path

bundle = json.loads(Path("bundle.json").read_text()) if Path("bundle.json").is_file() else {}
statuses = bundle.get("camera_status", {})
cams = [c for c in os.environ.get("VS_CAMERA_IDS", "").split(",") if c]

summary = {"camera_count": len(cams), "all_ok": True, "cameras": {}}
for cam in cams:
    d = Path(cam)
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").is_file() else {}
    status = statuses.get(cam, meta.get("status", "unknown"))
    if status != "ok":
        summary["all_ok"] = False
    summary["cameras"][cam] = {
        "status": status,
        "covered_sec": (meta.get("covered") or {}).get("sec", 0.0),
        "segment_count": meta.get("segment_count", 0),
        "has_audio": (d / "audio.mp3").is_file(),
        "has_contact_sheet": (d / "contact_sheet.jpg").is_file(),
    }

print(json.dumps(summary))
