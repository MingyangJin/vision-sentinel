"""Credential handling: the only place camera secrets are resolved (ARCHITECTURE §5.1).

A camera's ``credential_ref`` names a secret; it is never the secret itself:

- ``env:NAME``                 environment variable NAME
- ``keychain:SERVICE[/ACCOUNT]`` macOS login keychain (``security find-generic-password``)
- ``file:/path``               first line of a file (e.g. a container secret mount)
- ``NAME``                     environment variable NAME, else keychain service NAME (macOS)

The secret is either a full URL with credentials (``rtsp://user:pass@host/path``)
or ``user:password`` to insert into the camera's credential-free ``source`` URL.

Every resolved value is remembered so ``scrub_credentials`` removes it from any text
(subprocess logs, error messages) even where it does not appear inside a URL.

``scrub_addresses`` goes further for text that leaves the Stream Hub (camera health shown
in the Web UI and written into Observation Bundles): it also removes camera hosts, IPs
and URLs, so consumers only ever see a logical ``camera_id``.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s'\"]+@")  # greedy: a password may hold '@'
_QUERY_SECRET = re.compile(r"(?i)\b(password|passwd|pwd|pass|token|secret|key|auth|user|username)=[^&\s'\"]+")
_SECRETS: set[str] = set()  # values resolved in this process, scrubbed wherever they appear


class CredentialError(RuntimeError):
    """A credential_ref could not be resolved. The message never holds the value."""


def remember_secret(value: str) -> None:
    for v in (value, quote(value, safe=""), unquote(value)):
        if len(v) >= 3:
            _SECRETS.add(v)


def scrub_credentials(text: str) -> str:
    """Remove URL userinfo, secret-looking query parameters and every known secret value."""
    if not text:
        return text
    text = _URL_USERINFO.sub(r"\1***@", text)
    text = _QUERY_SECRET.sub(r"\1=***", text)
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


_ANY_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s'\",;)\]]+")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])(?::\d+)?")
_IPV6 = re.compile(r"\[[0-9A-Fa-f:.]+(?:%[\w.\-]+)?\](?::\d+)?")
_ADDRESSES: set[str] = set()  # camera hosts and local source paths known to this process


def remember_address(source: str) -> None:
    """Remember where a camera lives (the host of a URL, or a local source path) so
    ``scrub_addresses`` removes it even where it appears outside a URL."""
    if not source:
        return
    if "://" in source:
        try:
            host = urlsplit(source).hostname
        except ValueError:
            host = None
        if host:
            _ADDRESSES.add(host)
    elif "/" in source:
        _ADDRESSES.add(source)


def scrub_addresses(text: str) -> str:
    """Remove camera locations from text shown outside the Stream Hub (ARCHITECTURE §5.1):
    every URL, IP literal, and known camera host or source path. Health details reach the
    Web UI and Observation Bundles, which may only know a camera by its ``camera_id``."""
    if not text:
        return text
    text = _ANY_URL.sub("<camera-url>", text)
    for addr in sorted(_ADDRESSES, key=len, reverse=True):
        text = re.sub(rf"(?<![\w.\-]){re.escape(addr)}(?![\w\-])", "<camera-address>", text)
    text = _IPV6.sub("<camera-address>", text)
    return _IPV4.sub("<camera-address>", text)


def redact_url(url: str) -> str:
    """A URL safe to print: credentials and secret query parameters replaced by ***."""
    return scrub_credentials(url)


def resolve_secret(ref: str) -> str:
    scheme, sep, name = ref.partition(":")
    if not sep:
        scheme, name = "", ref
    if scheme == "env":
        value = os.environ.get(name)
    elif scheme == "file":
        p = Path(name).expanduser()
        value = p.read_text().splitlines()[0].strip() if p.is_file() else None
    elif scheme == "keychain":
        value = _keychain(name)
    elif scheme == "":
        value = os.environ.get(name) or _keychain(name)
    else:
        raise CredentialError(f"unknown credential_ref scheme {scheme!r} (use env:, keychain: or file:)")
    if not value:
        raise CredentialError(f"credential_ref {ref!r} did not resolve to a value")
    remember_secret(value)
    if "://" in value:
        password = urlsplit(value).password
    else:
        password = value.partition(":")[2]
    if password:
        remember_secret(password)
    return value


def _keychain(name: str) -> str | None:
    if platform.system() != "Darwin":
        return None
    service, _, account = name.partition("/")
    cmd = ["security", "find-generic-password", "-s", service, "-w"]
    if account:
        cmd[3:3] = ["-a", account]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def with_credentials(source: str, secret: str) -> str:
    """The URL to open: ``secret`` itself when it is a full URL, else ``source`` with
    ``user:password`` inserted as its userinfo."""
    if "://" in secret:
        remember_address(secret)
        return secret
    user, _, password = secret.partition(":")
    parts = urlsplit(source)
    host = parts.netloc.rpartition("@")[2]
    userinfo = quote(user, safe="") + (f":{quote(password, safe='')}" if password else "")
    return urlunsplit((parts.scheme, f"{userinfo}@{host}", parts.path, parts.query, parts.fragment))
