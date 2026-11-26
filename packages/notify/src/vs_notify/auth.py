"""The shared household token for device -> server calls (ARCHITECTURE.md §5.7), and the
loopback-only guard for server-internal calls.

The token is generated once at setup and kept in secret storage: either injected as
``VS_HOUSEHOLD_TOKEN`` by the user's secret wrapper, or a 0600 file under the user's config dir.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
from pathlib import Path

TOKEN_ENV = "VS_HOUSEHOLD_TOKEN"
FORWARD_HEADERS = ("forwarded", "x-forwarded-for", "x-real-ip")


def default_token_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "vision-sentinel" / "household_token"


def load_household_token(path: Path | None = None) -> str | None:
    if os.environ.get(TOKEN_ENV):
        return os.environ[TOKEN_ENV].strip()
    path = path or default_token_path()
    if path.is_file():
        return path.read_text().strip() or None
    return None


def ensure_household_token(path: Path | None = None) -> Path:
    """Create the token file (0600) if missing; return its path. Never prints the value."""
    path = path or default_token_path()
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
    return path


def bearer_ok(authorization: str | None, expected: str | None) -> bool:
    """Constant-time check of ``Authorization: Bearer <token>``. No configured token: deny."""
    if not expected or not authorization:
        return False
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(presented.strip().encode(), expected.encode())


def is_loopback_request(client_host: str | None, headers) -> bool:
    """True only for a direct connection from this machine. A request relayed by a local
    reverse proxy or tunnel carries forwarding headers and is treated as remote."""
    if not client_host or any(h in headers for h in FORWARD_HEADERS):
        return False
    try:
        ip = ipaddress.ip_address(client_host.split("%")[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_loopback


def is_loopback_host_header(host: str | None) -> bool:
    """True when a request's ``Host`` header names this machine's loopback (``localhost``,
    ``127.0.0.1``, ``[::1]``, any port). A browser tricked by DNS rebinding reaches a local
    port under the attacker's host name, so a loopback-only route must check this as well as
    the peer address."""
    if not host:
        return False
    name = host.strip().lower()
    if name.startswith("["):
        name = name[1:].partition("]")[0]
    elif name.count(":") == 1:
        name = name.partition(":")[0]
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False
