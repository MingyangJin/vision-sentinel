"""Check that the official ``agy`` binary is usable: ``python -m vs_harness_antigravity.cli_probe --check``.

Prints the binary path, its version, the media capabilities this driver declares,
and whether the default models are offered. Spends no model quota.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from ._agy import AgyError, child_env, find_agy
from .harness import AGY_MEDIA
from .orchestrator import DEFAULT_MAIN_MODEL
from .vision import DEFAULT_VISION_MODEL

TESTED_VERSIONS = ("1.5.16", "1.5.17")


def probe(binary: str | None = None, timeout_s: float = 30.0) -> dict:
    report: dict = {"ok": False, "binary": None, "version": None, "tested_versions": TESTED_VERSIONS,
                    "media": AGY_MEDIA.model_dump(),
                    "models": {DEFAULT_VISION_MODEL: None, DEFAULT_MAIN_MODEL: None}}
    try:
        report["binary"] = bin_ = find_agy(binary)
        env = child_env()
        ver = subprocess.run([bin_, "--version"], capture_output=True, text=True, timeout=timeout_s, env=env)
        report["version"] = ver.stdout.strip() or None
        models = subprocess.run([bin_, "models"], capture_output=True, text=True, timeout=timeout_s, env=env)
        listed = {line.split()[0] for line in models.stdout.splitlines() if line.strip() and "\t" in line}
        for m in report["models"]:
            report["models"][m] = m in listed
        report["ok"] = ver.returncode == 0 and all(report["models"].values())
        if not report["ok"]:
            report["error"] = "agy --version failed" if ver.returncode else "a default model is not offered"
    except (AgyError, OSError, subprocess.TimeoutExpired) as e:
        report["error"] = str(e)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="probe agy and print a JSON report (default)")
    ap.add_argument("--binary", default=None, help="path to agy (default: $VS_AGY_BIN or PATH)")
    args = ap.parse_args(argv)
    report = probe(args.binary)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
