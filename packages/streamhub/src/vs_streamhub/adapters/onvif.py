"""ONVIF camera adapter: WS-Discovery + GetProfiles / GetStreamUri -> single-pull ingest.

Strictly follows "The One Rule" (ARCHITECTURE §5.1):
- ``source`` is a credential-free ONVIF device service URL
  (``http://192.0.2.20/onvif/device_service`` or ``onvif://192.0.2.20:80/onvif/device_service``)
  or ``"discover"`` / ``"onvif://discover"`` for LAN WS-Discovery.
- Credentials live only in ``credential_ref`` (``user:password``) and are resolved at
  runtime inside ``vs_streamhub``. All SOAP, HTTP, and FFmpeg errors are scrubbed.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from vs_runtime.contracts import StreamHealth

from vs_streamhub import ffmpeg
from vs_streamhub.adapters.base import BaseAdapter, SpeakerUnsupportedError, ffmpeg_base, segment_args
from vs_streamhub.credentials import (
    CredentialError,
    redact_url,
    remember_secret,
    resolve_secret,
    scrub_credentials,
    with_credentials,
)

WSD_MULTICAST = "239.255.255.250"
WSD_PORT = 3702

_SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
_PWD_DIGEST = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
_BASE64_ENC = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"
_TDS = "http://www.onvif.org/ver10/device/wsdl"
_TRT = "http://www.onvif.org/ver10/media/wsdl"
_TT = "http://www.onvif.org/ver10/schema"

PostFn = Callable[[str, bytes, dict[str, str], float], bytes]


class OnvifError(RuntimeError):
    """ONVIF discovery or SOAP call failed (message is always credential-scrubbed)."""


@dataclass(frozen=True)
class OnvifDevice:
    xaddr: str
    scopes: list[str] = field(default_factory=list)
    address: str = ""
    host: str = ""
    port: int | None = None


@dataclass(frozen=True)
class OnvifProfile:
    token: str
    name: str
    encoding: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0


def build_ws_discovery_probe(message_id: str | None = None) -> bytes:
    mid = message_id or f"urn:uuid:{uuid.uuid4()}"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        "<e:Header>"
        f"<w:MessageID>{mid}</w:MessageID>"
        "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
        "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
        "</e:Header>"
        "<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>"
        "</e:Envelope>"
    )
    return xml.encode("utf-8")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_text(elem: ET.Element, local_name: str) -> str:
    for node in elem.iter():
        if _local(node.tag) == local_name and node.text:
            return node.text.strip()
    return ""


def parse_probe_matches(payload: bytes) -> list[OnvifDevice]:
    """Parse one WS-Discovery ProbeMatches SOAP response into OnvifDevice records."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    devices: list[OnvifDevice] = []
    for node in root.iter():
        if _local(node.tag) != "ProbeMatch":
            continue
        xaddrs = _find_text(node, "XAddrs").split()
        scopes = _find_text(node, "Scopes").split()
        address = _find_text(node, "Address")
        for xaddr in xaddrs:
            if not xaddr.lower().startswith(("http://", "https://")):
                continue
            parts = urlsplit(xaddr)
            devices.append(OnvifDevice(
                xaddr=xaddr,
                scopes=scopes,
                address=address,
                host=parts.hostname or "",
                port=parts.port,
            ))
    return devices


def discover_onvif(
    timeout: float = 3.0,
    *,
    multicast_addr: str = WSD_MULTICAST,
    port: int = WSD_PORT,
    transport: Callable[[bytes, float], list[bytes]] | None = None,
) -> list[OnvifDevice]:
    """Send an ONVIF WS-Discovery multicast probe and return discovered devices."""
    probe_xml = build_ws_discovery_probe()
    if transport is not None:
        raw_responses = transport(probe_xml, timeout)
    else:
        raw_responses = _udp_multicast_probe(probe_xml, multicast_addr, port, timeout)
    seen: set[tuple[str, str]] = set()
    found: list[OnvifDevice] = []
    for raw in raw_responses:
        for dev in parse_probe_matches(raw):
            key = (dev.address, dev.xaddr)
            if key not in seen:
                seen.add(key)
                found.append(dev)
    return found


def _udp_multicast_probe(probe_xml: bytes, addr: str, port: int, timeout: float) -> list[bytes]:
    responses: list[bytes] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        try:
            sock.sendto(probe_xml, (addr, port))
        except OSError as e:
            raise OnvifError(f"WS-Discovery send failed: {scrub_credentials(str(e))}") from None
        deadline = time.monotonic() + max(timeout, 0.2)
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(65535)
                responses.append(data)
            except socket.timeout:
                continue
            except OSError:
                break
    return responses


def _wsse_header(username: str, password: str) -> str:
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest_raw = hashlib.sha1(nonce + created.encode("utf-8") + password.encode("utf-8")).digest()
    digest_b64 = base64.b64encode(digest_raw).decode("ascii")
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    remember_secret(digest_b64)
    return (
        f'<s:Header><wsse:Security s:mustUnderstand="1" xmlns:wsse="{_WSSE}" xmlns:wsu="{_WSU}">'
        "<wsse:UsernameToken>"
        f"<wsse:Username>{_xml_escape(username)}</wsse:Username>"
        f'<wsse:Password Type="{_PWD_DIGEST}">{digest_b64}</wsse:Password>'
        f'<wsse:Nonce EncodingType="{_BASE64_ENC}">{nonce_b64}</wsse:Nonce>'
        f"<wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security></s:Header>"
    )


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _soap_envelope(body_xml: str, username: str = "", password: str = "") -> bytes:
    header = _wsse_header(username, password) if username else ""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{_SOAP_ENV}" xmlns:tds="{_TDS}" xmlns:trt="{_TRT}" xmlns:tt="{_TT}">'
        f"{header}<s:Body>{body_xml}</s:Body></s:Envelope>"
    )
    return xml.encode("utf-8")


def _default_post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


class OnvifClient:
    """Minimal stdlib ONVIF SOAP client: GetCapabilities, GetProfiles, GetStreamUri."""

    def __init__(
        self,
        device_url: str,
        username: str = "",
        password: str = "",
        *,
        timeout: float = 10.0,
        post_fn: PostFn | None = None,
    ):
        self.device_url = device_url
        self._username = username
        self._password = password
        self.timeout = timeout
        self._post = post_fn or _default_post

    def _call(self, url: str, body_xml: str, action: str) -> ET.Element:
        envelope = _soap_envelope(body_xml, self._username, self._password)
        headers = {"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'}
        try:
            raw = self._post(url, envelope, headers, self.timeout)
        except urllib.error.HTTPError as e:
            raise OnvifError(f"ONVIF HTTP {e.code} on {redact_url(url)}: {scrub_credentials(str(e.reason))}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise OnvifError(f"ONVIF request failed on {redact_url(url)}: {scrub_credentials(str(e))}") from None
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise OnvifError(f"ONVIF invalid XML response: {scrub_credentials(str(e))}") from None
        for node in root.iter():
            if _local(node.tag) == "Fault":
                reason = _find_text(node, "Text") or _find_text(node, "faultstring") or "SOAP Fault"
                raise OnvifError(f"ONVIF SOAP fault: {scrub_credentials(reason)}")
        return root

    def get_media_xaddr(self) -> str:
        body = "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>"
        try:
            root = self._call(self.device_url, body, f"{_TDS}/GetCapabilities")
        except OnvifError:
            return self.device_url
        for node in root.iter():
            if _local(node.tag) == "Media":
                xaddr = _find_text(node, "XAddr")
                if xaddr:
                    return xaddr
        return self.device_url

    def get_profiles(self, media_url: str | None = None) -> list[OnvifProfile]:
        url = media_url or self.get_media_xaddr()
        root = self._call(url, "<trt:GetProfiles/>", f"{_TRT}/GetProfiles")
        profiles: list[OnvifProfile] = []
        for node in root.iter():
            if _local(node.tag) != "Profiles":
                continue
            token = node.attrib.get("token", "").strip()
            if not token:
                continue
            name = _find_text(node, "Name") or token
            encoding = _find_text(node, "Encoding")
            width = _parse_int(_find_text(node, "Width"))
            height = _parse_int(_find_text(node, "Height"))
            fps = _parse_float(_find_text(node, "FrameRateLimit"))
            profiles.append(OnvifProfile(token=token, name=name, encoding=encoding, width=width, height=height, fps=fps))
        return profiles

    def get_stream_uri(self, profile_token: str, media_url: str | None = None, *, transport: str = "RTSP") -> str:
        url = media_url or self.get_media_xaddr()
        body = (
            "<trt:GetStreamUri>"
            "<trt:StreamSetup>"
            "<tt:Stream>RTP-Unicast</tt:Stream>"
            f"<tt:Transport><tt:Protocol>{_xml_escape(transport)}</tt:Protocol></tt:Transport>"
            "</trt:StreamSetup>"
            f"<trt:ProfileToken>{_xml_escape(profile_token)}</trt:ProfileToken>"
            "</trt:GetStreamUri>"
        )
        root = self._call(url, body, f"{_TRT}/GetStreamUri")
        uri = _find_text(root, "Uri")
        if not uri:
            raise OnvifError(f"ONVIF GetStreamUri returned no URI for profile {profile_token!r}")
        return uri


def _parse_int(val: str) -> int:
    try:
        return int(val)
    except ValueError:
        return 0


def _parse_float(val: str) -> float:
    try:
        return float(val)
    except ValueError:
        return 0.0


def _normalize_device_url(source: str) -> str:
    s = source.strip()
    if s.lower() in ("", "discover", "onvif://discover"):
        return "discover"
    if s.lower().startswith("onvif://"):
        s = "http://" + s[len("onvif://"):]
    if not s.lower().startswith(("http://", "https://")):
        raise ValueError(f"onvif source must be an http(s)://, onvif:// URL, or 'discover' (got {source!r})")
    if "@" in s.partition("://")[2].partition("/")[0]:
        raise ValueError("put ONVIF credentials in credential_ref, not in source")
    parts = urlsplit(s)
    if not parts.path or parts.path == "/":
        return s.rstrip("/") + "/onvif/device_service"
    return s


def _pick_profile(profiles: list[OnvifProfile], *, token: str | None = None, selector: str | None = None) -> OnvifProfile:
    if not profiles:
        raise OnvifError("ONVIF camera returned no media profiles")
    if token:
        for p in profiles:
            if p.token == token:
                return p
        raise OnvifError(f"ONVIF profile token {token!r} not found (available: {[p.token for p in profiles]})")
    if selector == "sub" or selector == "lowest":
        return min(profiles, key=lambda p: (p.width * p.height) or 10**9)
    if selector == "main" or selector == "highest":
        return max(profiles, key=lambda p: p.width * p.height)
    return profiles[0]


class OnvifAdapter(BaseAdapter):
    kind = "onvif"

    def __init__(
        self,
        config,
        segment_dir,
        *,
        post_fn: PostFn | None = None,
        discover_fn: Callable[[float], list[OnvifDevice]] | None = None,
        **kw,
    ):
        super().__init__(config, segment_dir, **kw)
        try:
            self._normalized_source = _normalize_device_url(config.source)
        except ValueError as e:
            raise ValueError(f"camera {config.camera_id!r}: {e}") from None
        self._post_fn = post_fn
        self._discover_fn = discover_fn
        self._resolved_profile: OnvifProfile | None = None

    @property
    def wants_mediamtx(self) -> bool:
        return self.config.options.get("ingest", "auto") in ("auto", "mediamtx")

    def needs_writer(self) -> bool:
        return self.ingest == "ffmpeg"

    def mediamtx_source(self) -> Callable[[], str] | None:
        return self.camera_url

    def _credentials(self) -> tuple[str, str]:
        ref = self.config.credential_ref
        if not ref:
            return "", ""
        secret = resolve_secret(ref)
        if "://" in secret:
            parts = urlsplit(secret)
            return parts.username or "", parts.password or ""
        user, _, password = secret.partition(":")
        return user, password

    def _resolve_device_url(self) -> str:
        if self._normalized_source != "discover":
            return self._normalized_source
        timeout = float(self.config.options.get("discovery_timeout_sec", 3.0))
        devices = self._discover_fn(timeout) if self._discover_fn else discover_onvif(timeout)
        match_addr = self.config.options.get("device_address")
        match_scope = self.config.options.get("match_scope")
        for dev in devices:
            if match_addr and dev.address != match_addr:
                continue
            if match_scope and not any(match_scope.lower() in s.lower() for s in dev.scopes):
                continue
            return dev.xaddr
        raise OnvifError("no matching ONVIF camera found via WS-Discovery")

    def resolve_stream(self) -> tuple[OnvifProfile, str]:
        """Resolve ONVIF profile and credential-free RTSP URI via SOAP."""
        device_url = self._resolve_device_url()
        user, password = self._credentials()
        client = OnvifClient(
            device_url,
            user,
            password,
            timeout=float(self.config.options.get("timeout_sec", 10.0)),
            post_fn=self._post_fn,
        )
        media_url = client.get_media_xaddr()
        profiles = client.get_profiles(media_url)
        profile = _pick_profile(
            profiles,
            token=self.config.options.get("profile_token"),
            selector=self.config.options.get("profile"),
        )
        uri = client.get_stream_uri(profile.token, media_url)
        self._resolved_profile = profile
        return profile, uri

    def camera_url(self) -> str:
        """Resolved RTSP URL with credentials injected. Only process launchers call this."""
        _, uri = self.resolve_stream()
        ref = self.config.credential_ref
        if not ref:
            return uri
        user, password = self._credentials()
        if not user:
            return uri
        return with_credentials(uri, f"{user}:{password}")

    def _input_args(self) -> list[str]:
        timeout_us = int(float(self.config.options.get("timeout_sec", 10)) * 1_000_000)
        return ["-rtsp_transport", self.config.options.get("transport", "tcp"), "-timeout", str(timeout_us)]

    def writer_command(self) -> tuple[list[str], dict[str, str] | None]:
        argv = [
            *ffmpeg_base(), *self._input_args(), "-i", self.camera_url(),
            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-ac", "1",
            *segment_args(self.segment_dir, self.segment_sec),
        ]
        return argv, None

    def probe(self) -> StreamHealth:
        try:
            profile, raw_uri = self.resolve_stream()
            user, password = self._credentials()
            url = with_credentials(raw_uri, f"{user}:{password}") if user else raw_uri
        except (CredentialError, OnvifError) as e:
            return StreamHealth(camera_id=self.camera_id, status="offline", detail=scrub_credentials(str(e)))
        info = ffmpeg.probe(
            url,
            timeout=float(self.config.options.get("timeout_sec", 10)) + 5,
            extra=self._input_args(),
        )
        if info is None or info.video_codec is None:
            return StreamHealth(
                camera_id=self.camera_id,
                status="offline",
                detail=scrub_credentials(f"ONVIF profile {profile.token}: no video from {redact_url(raw_uri)}"),
            )
        return StreamHealth(
            camera_id=self.camera_id,
            status="ok",
            detail=scrub_credentials(
                f"ONVIF {profile.token}; {info.video_codec} {info.width}x{info.height} "
                f"@ {info.fps:g} fps, {info.audio_codec or 'no audio'}"
            ),
        )

    def _play_alert(self, audio: Path, volume: float) -> None:
        """Optional ONVIF/RTSP audio backchannel push when ``options.speaker_backchannel_url`` is set."""
        target = self.config.options.get("speaker_backchannel_url")
        if not target:
            raise SpeakerUnsupportedError(f"ONVIF camera {self.camera_id!r} has no speaker backchannel configured")
        if not audio.is_file():
            raise FileNotFoundError(f"alert audio not found: {audio}")
        user, password = self._credentials()
        url = with_credentials(str(target), f"{user}:{password}") if user else str(target)
        codec = str(self.config.options.get("speaker_codec", "pcm_mulaw"))
        rate = str(int(self.config.options.get("speaker_sample_rate", 8000)))
        ffmpeg.run(
            [
                "ffmpeg", "-y", "-v", "error", "-re", "-i", str(audio),
                "-vn", "-af", f"volume={volume:.3f}", "-ac", "1", "-ar", rate,
                "-c:a", codec, "-f", "rtsp", "-rtsp_transport", self.config.options.get("transport", "tcp"), url,
            ],
            timeout=float(self.config.options.get("speaker_timeout_sec", 30.0)),
        )

    def describe(self) -> dict:
        source = redact_url(self._normalized_source)
        return {
            **super().describe(),
            "source": source,
            "credential_ref": self.config.credential_ref,
            "profile_token": self.config.options.get("profile_token"),
            "profile": self.config.options.get("profile"),
            "transport": self.config.options.get("transport", "tcp"),
        }
