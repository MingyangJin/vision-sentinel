"""vs-camera: inspect and view camera streams."""

import argparse
import os
import sys

from vision_sentinel.config import load_env
from vision_sentinel.sources import find, open_stream, redact
from vision_sentinel.view import serve


def _creds(args: argparse.Namespace) -> tuple[str, str, str] | None:
    """Resolve the camera, preferring its MAC over a possibly-stale IP.

    A DHCP lease can move - this one did, mid-project - so REOLINK_MAC is the
    durable identity and REOLINK_HOST is only a hint.
    """
    host = getattr(args, "host", None) or os.environ.get("REOLINK_HOST")
    mac = os.environ.get("REOLINK_MAC")
    user = os.environ.get("REOLINK_USER", "admin")
    password = os.environ.get("REOLINK_PASSWORD")

    if not password:
        print("Set REOLINK_PASSWORD in .env", file=sys.stderr)
        return None

    if mac and (found := find(mac)):
        if host and found != host:
            print(f"  camera moved: {host} -> {found}", file=sys.stderr)
        host = found
    elif mac and not host:
        print(f"Could not locate {mac} on the network", file=sys.stderr)
        return None

    if not host:
        print("Set REOLINK_HOST or REOLINK_MAC in .env", file=sys.stderr)
        return None
    return host, user, password


def cmd_probe(args: argparse.Namespace) -> int:
    """Report what each stream actually is, not what the spec sheet claims."""
    if not (c := _creds(args)):
        return 2
    found = False
    for kind, role in (("sub", "continuous CV"), ("main", "VLM clips")):
        info = open_stream(*c, kind)
        if info:
            found = True
            print(f"  {kind:5s} {info}   <- {role}")
            print(f"        {redact(info.url)}")
        else:
            print(f"  {kind:5s} unavailable")
    return 0 if found else 1


def cmd_view(args: argparse.Namespace) -> int:
    if not (c := _creds(args)):
        return 2
    info = open_stream(*c, args.stream)
    if not info:
        print(f"Could not open {args.stream} stream", file=sys.stderr)
        return 1
    serve(info.url, args.stream, f"{info.width}x{info.height}",
          args.bind, args.port, args.fps, args.quality)
    return 0


def main() -> int:
    load_env()
    p = argparse.ArgumentParser(prog="vs-camera")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("probe", help="report both streams")
    pr.add_argument("host", nargs="?")
    pr.set_defaults(func=cmd_probe)

    v = sub.add_parser("view", help="live MJPEG view in the browser")
    v.add_argument("host", nargs="?")
    v.add_argument("--stream", choices=["sub", "main"], default="sub")
    v.add_argument("--port", type=int, default=8080)
    v.add_argument("--bind", default="127.0.0.1",
                   help="default keeps the feed off the LAN")
    v.add_argument("--fps", type=int, default=10)
    v.add_argument("--quality", type=int, default=5, help="mjpeg q:v, 2=best 31=worst")
    v.set_defaults(func=cmd_view)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
