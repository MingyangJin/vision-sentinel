import json
import urllib.error
import xml.etree.ElementTree as ET

import pytest

from vs_runtime.contracts import CameraConfig
from vs_streamhub import OnvifAdapter, OnvifDevice, OnvifError, StreamHub, build_adapter, discover_onvif, ffmpeg
from vs_streamhub.adapters.onvif import OnvifClient, build_ws_discovery_probe, parse_probe_matches
from vs_streamhub.ffmpeg import MediaInfo

SECRET = "Onv1f-S3cr3t!"

PROBE_MATCH_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <e:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <w:EndpointReference><w:Address>urn:uuid:11111111-2222-3333-4444-555555555555</w:Address></w:EndpointReference>
        <d:Scopes>onvif://www.onvif.org/type/Network_Video_Transmitter onvif://www.onvif.org/name/FrontCam</d:Scopes>
        <d:XAddrs>http://192.0.2.20:80/onvif/device_service</d:XAddrs>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </e:Body>
</e:Envelope>"""

CAPABILITIES_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
            xmlns:tt="http://www.onvif.org/ver10/schema">
  <s:Body><tds:GetCapabilitiesResponse><tds:Capabilities>
    <tt:Media><tt:XAddr>http://192.0.2.20:80/onvif/media_service</tt:XAddr></tt:Media>
  </tds:Capabilities></tds:GetCapabilitiesResponse></s:Body>
</s:Envelope>"""

PROFILES_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
            xmlns:tt="http://www.onvif.org/ver10/schema">
  <s:Body><trt:GetProfilesResponse>
    <trt:Profiles token="prof_main">
      <tt:Name>MainStream</tt:Name>
      <tt:VideoEncoderConfiguration>
        <tt:Encoding>H264</tt:Encoding>
        <tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>
        <tt:RateControl><tt:FrameRateLimit>20</tt:FrameRateLimit></tt:RateControl>
      </tt:VideoEncoderConfiguration>
    </trt:Profiles>
    <trt:Profiles token="prof_sub">
      <tt:Name>SubStream</tt:Name>
      <tt:VideoEncoderConfiguration>
        <tt:Encoding>H264</tt:Encoding>
        <tt:Resolution><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:Resolution>
        <tt:RateControl><tt:FrameRateLimit>15</tt:FrameRateLimit></tt:RateControl>
      </tt:VideoEncoderConfiguration>
    </trt:Profiles>
  </trt:GetProfilesResponse></s:Body>
</s:Envelope>"""

STREAM_URI_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
            xmlns:tt="http://www.onvif.org/ver10/schema">
  <s:Body><trt:GetStreamUriResponse><trt:MediaUri>
    <tt:Uri>rtsp://192.0.2.20:554/onvif/profile_sub</tt:Uri>
  </trt:MediaUri></trt:GetStreamUriResponse></s:Body>
</s:Envelope>"""


def _fake_soap_post(calls: list[tuple[str, bytes]]):
    def post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        calls.append((url, body))
        text = body.decode("utf-8")
        if "GetCapabilities" in text:
            return CAPABILITIES_XML
        if "GetProfiles" in text:
            return PROFILES_XML
        if "GetStreamUri" in text:
            return STREAM_URI_XML
        raise AssertionError(f"unexpected SOAP body: {text}")

    return post


@pytest.fixture
def onvif_cfg(monkeypatch):
    monkeypatch.setenv("VS_TEST_ONVIF", f"camuser:{SECRET}")
    return CameraConfig(
        camera_id="driveway",
        adapter="onvif",
        source="http://192.0.2.20:80/onvif/device_service",
        credential_ref="env:VS_TEST_ONVIF",
        options={"profile": "sub"},
    )


def test_ws_discovery_probe_and_parse():
    probe = build_ws_discovery_probe("urn:uuid:test-1")
    root = ET.fromstring(probe)
    assert any(n.text == "dn:NetworkVideoTransmitter" for n in root.iter())
    devices = parse_probe_matches(PROBE_MATCH_XML)
    assert len(devices) == 1
    d = devices[0]
    assert d.xaddr == "http://192.0.2.20:80/onvif/device_service"
    assert d.host == "192.0.2.20" and d.port == 80
    assert any("FrontCam" in s for s in d.scopes)
    # Deduplicates identical replies from multicast
    found = discover_onvif(transport=lambda payload, t: [PROBE_MATCH_XML, PROBE_MATCH_XML, b"<broken"])
    assert len(found) == 1


def test_source_validation_rejects_embedded_credentials(tmp_path):
    with pytest.raises(ValueError, match="credential_ref"):
        OnvifAdapter(CameraConfig(camera_id="c", adapter="onvif", source="http://u:p@192.0.2.20/onvif/device_service"), tmp_path)
    with pytest.raises(ValueError, match="http"):
        OnvifAdapter(CameraConfig(camera_id="c", adapter="onvif", source="rtsp://192.0.2.20/s"), tmp_path)
    # onvif:// scheme normalizes to http:// and adds default device_service path
    a = build_adapter(CameraConfig(camera_id="c", adapter="onvif", source="onvif://192.0.2.20:8080"), tmp_path)
    assert isinstance(a, OnvifAdapter)
    assert a.describe()["source"] == "http://192.0.2.20:8080/onvif/device_service"


def test_onvif_client_wsse_and_stream_resolution():
    calls: list[tuple[str, bytes]] = []
    client = OnvifClient("http://192.0.2.20/onvif/device_service", "camuser", SECRET, post_fn=_fake_soap_post(calls))
    media_xaddr = client.get_media_xaddr()
    assert media_xaddr == "http://192.0.2.20:80/onvif/media_service"
    profiles = client.get_profiles(media_xaddr)
    assert [(p.token, p.width, p.height) for p in profiles] == [("prof_main", 1920, 1080), ("prof_sub", 640, 360)]
    uri = client.get_stream_uri("prof_sub", media_xaddr)
    assert uri == "rtsp://192.0.2.20:554/onvif/profile_sub"
    # Verify WS-Security PasswordDigest header was sent and never contained raw password
    for _, body in calls:
        text = body.decode("utf-8")
        assert "PasswordDigest" in text and "camuser" in text
        assert SECRET not in text


def test_onvif_adapter_resolves_profile_and_isolates_credentials(onvif_cfg, tmp_path, monkeypatch):
    calls: list[tuple[str, bytes]] = []
    adapter = OnvifAdapter(onvif_cfg, tmp_path / "driveway", post_fn=_fake_soap_post(calls))
    # describe() is credential-free and does not talk to the camera
    desc = adapter.describe()
    assert SECRET not in json.dumps(desc) and not calls
    # Resolve stream picks "sub" profile (640x360) and injects credentials only in camera_url()
    url = adapter.camera_url()
    assert url == "rtsp://camuser:Onv1f-S3cr3t%21@192.0.2.20:554/onvif/profile_sub"
    assert any(b"<trt:ProfileToken>prof_sub</trt:ProfileToken>" in b for _, b in calls)
    # FFmpeg fallback writer uses resolved URL
    argv, _ = adapter.writer_command()
    assert url in argv and "-rtsp_transport" in argv
    # probe() scrubs credentials and reports profile + video info
    monkeypatch.setattr(ffmpeg, "probe", lambda target, **kw: MediaInfo(10, "h264", 640, 360, 15.0, "aac"))
    h = adapter.probe()
    assert h.status == "ok" and "prof_sub" in h.detail and "640x360" in h.detail
    assert SECRET not in h.model_dump_json()


def test_onvif_mediamtx_wiring_keeps_secret_in_env_only(onvif_cfg, tmp_path):
    fake_mtx = tmp_path / "mediamtx"
    fake_mtx.write_text("#!/bin/sh\n")
    fake_mtx.chmod(0o755)
    hub = StreamHub([onvif_cfg], tmp_path / "rec", mediamtx_binary=str(fake_mtx), rtsp_port=18701, api_port=18702)
    adapter = hub.adapter("driveway")
    assert isinstance(adapter, OnvifAdapter)
    adapter._post_fn = _fake_soap_post([])
    plan = hub.plan()
    assert SECRET not in json.dumps(plan)
    assert plan["cameras"][0]["ingest"] == "mediamtx"
    assert not adapter.needs_writer()
    cfg_file = hub.mediamtx.write_config()
    assert SECRET not in cfg_file.read_text()
    argv, env = hub.mediamtx.command()
    assert SECRET not in " ".join(argv)
    assert env["MTX_PATHS_DRIVEWAY_SOURCE"].startswith("rtsp://camuser:Onv1f-S3cr3t%21@192.0.2.20:554/")


def test_onvif_discover_mode_and_soap_error_scrubbing(monkeypatch, tmp_path):
    monkeypatch.setenv("VS_TEST_ONVIF2", f"admin:{SECRET}")
    cfg = CameraConfig(
        camera_id="auto_cam",
        adapter="onvif",
        source="discover",
        credential_ref="env:VS_TEST_ONVIF2",
        options={"match_scope": "FrontCam"},
    )
    devs = [
        OnvifDevice(xaddr="http://192.0.2.99/onvif/device_service", scopes=["onvif://www.onvif.org/name/Other"]),
        OnvifDevice(xaddr="http://192.0.2.20/onvif/device_service", scopes=["onvif://www.onvif.org/name/FrontCam"]),
    ]
    calls: list[tuple[str, bytes]] = []
    adapter = OnvifAdapter(cfg, tmp_path, post_fn=_fake_soap_post(calls), discover_fn=lambda t: devs)
    prof, uri = adapter.resolve_stream()
    assert prof.token == "prof_main" and uri == "rtsp://192.0.2.20:554/onvif/profile_sub"
    assert calls[0][0] == "http://192.0.2.20/onvif/device_service"

    # SOAP fault / HTTP error containing secret gets scrubbed cleanly
    def failing_post(url, body, headers, timeout):
        raise urllib.error.URLError(f"auth failed with {SECRET} on rtsp://admin:{SECRET}@192.0.2.20/s")

    bad = OnvifAdapter(cfg, tmp_path, post_fn=failing_post, discover_fn=lambda t: devs)
    h = bad.probe()
    assert h.status == "offline" and SECRET not in h.detail and "auth failed" in h.detail
    with pytest.raises(OnvifError) as exc:
        bad.resolve_stream()
    assert SECRET not in str(exc.value)
