"""Stream Hub on its own, for setup and debugging.

    python -m vs_streamhub check  hub.toml            # redacted plan + probe of each camera
    python -m vs_streamhub run    hub.toml            # ingest until Ctrl-C, printing health
    python -m vs_streamhub slice  hub.toml CAMERA_ID --window 30 --out DIR

``hub.toml``: the keys of ``StreamHub.from_config`` at the root or under ``[streamhub]``,
cameras as ``[cameras.<camera_id>]`` tables or a ``[[cameras]]`` list.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import tomllib
from pathlib import Path

from vs_streamhub.credentials import scrub_credentials
from vs_streamhub.hub import StreamHub


def load(path: Path) -> StreamHub:
    data = tomllib.loads(path.read_text())
    cfg = dict(data.get("streamhub", {}))
    cfg.setdefault("cameras", data.get("cameras", []))
    return StreamHub.from_config(cfg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vs_streamhub", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("command", choices=["check", "run", "slice"])
    ap.add_argument("config", type=Path)
    ap.add_argument("camera_id", nargs="?")
    ap.add_argument("--window", type=float, default=30.0)
    ap.add_argument("--out", type=Path, default=Path("slice-out"))
    ap.add_argument("--no-probe", action="store_true", help="check: skip opening the cameras")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        hub = load(args.config)
        if args.command == "check":
            plan = hub.plan()
            if not args.no_probe:
                for cam in plan["cameras"]:
                    cam["probe"] = hub.probe(cam["camera_id"]).model_dump(mode="json")
            print(scrub_credentials(json.dumps(plan, indent=2)))
            return 0
        with hub:
            if args.command == "slice":
                if not args.camera_id:
                    ap.error("slice needs CAMERA_ID")
                hub.wait_until_ok(args.camera_id, args.window + 15)
                time.sleep(args.window)
                res = hub.slice_latest(args.camera_id, args.window, args.out)
                print(json.dumps(res.meta, indent=2))
                return 0 if res.status != "offline" else 1
            while True:
                for cid in hub.camera_ids:
                    h = hub.health(cid)
                    print(f"{cid}: {h.status} {h.detail}", flush=True)
                time.sleep(10)
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # report without leaking: every message is scrubbed
        print(f"error: {scrub_credentials(str(e))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
