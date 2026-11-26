import pytest

from vs_streamhub.credentials import (
    CredentialError,
    redact_url,
    resolve_secret,
    scrub_credentials,
    with_credentials,
)


def test_scrubs_url_userinfo_including_at_in_password():
    text = "Connection to rtsp://admin:p@ss@w0rd@192.0.2.10:554/stream1 failed"
    out = scrub_credentials(text)
    assert "p@ss" not in out and "admin" not in out
    assert "rtsp://***@192.0.2.10:554/stream1" in out


@pytest.mark.parametrize("url", ["rtsps://u:secret1@cam.example:322/live", "rtmp://u:secret1@host/app",
                                 "http://u:secret1@host/onvif"])  # fmt: skip
def test_scrubs_other_schemes(url):
    assert "secret1" not in scrub_credentials(f"open {url}: timeout")


def test_scrubs_secret_query_parameters():
    out = redact_url("rtsp://192.0.2.10/cam?user=admin&password=topsecret&channel=1")
    assert "topsecret" not in out and "admin" not in out and "channel=1" in out


def test_redact_url_leaves_clean_urls_alone():
    assert redact_url("rtsp://127.0.0.1:8554/demo") == "rtsp://127.0.0.1:8554/demo"


def test_resolved_secret_is_scrubbed_anywhere(monkeypatch):
    monkeypatch.setenv("VS_TEST_CAM", "operator:Zq9-unique-pass")
    value = resolve_secret("env:VS_TEST_CAM")
    assert value == "operator:Zq9-unique-pass"
    # even outside any URL, e.g. echoed alone by a tool
    assert "Zq9-unique-pass" not in scrub_credentials("auth failed for Zq9-unique-pass")


def test_bare_ref_reads_environment(monkeypatch):
    monkeypatch.setenv("VS_TEST_BARE", "u:pw-bare-123")
    assert resolve_secret("VS_TEST_BARE") == "u:pw-bare-123"


def test_file_ref(tmp_path):
    f = tmp_path / "cam.secret"
    f.write_text("rtsp://u:file-secret-9@192.0.2.5/s\n")
    assert resolve_secret(f"file:{f}") == "rtsp://u:file-secret-9@192.0.2.5/s"
    assert "file-secret-9" not in scrub_credentials("x file-secret-9 y")


def test_missing_and_unknown_refs_raise_without_value(monkeypatch):
    monkeypatch.delenv("VS_TEST_MISSING", raising=False)
    with pytest.raises(CredentialError, match="VS_TEST_MISSING"):
        resolve_secret("env:VS_TEST_MISSING")
    with pytest.raises(CredentialError, match="unknown"):
        resolve_secret("vault:x")


def test_with_credentials_inserts_and_encodes():
    url = with_credentials("rtsp://192.0.2.10:554/stream1", "admin:p@ss:word")
    assert url == "rtsp://admin:p%40ss%3Aword@192.0.2.10:554/stream1"
    assert with_credentials("rtsp://h/s", "rtsp://a:b@h2/s2") == "rtsp://a:b@h2/s2"
    assert "p%40ss" not in scrub_credentials(url)


def test_url_encoded_password_is_scrubbed(monkeypatch):
    monkeypatch.setenv("VS_TEST_ENC", "cam:a b/c?d")
    resolve_secret("env:VS_TEST_ENC")
    assert "a%20b%2Fc%3Fd" not in scrub_credentials("sent a%20b%2Fc%3Fd")


def test_scrub_addresses_removes_urls_ips_and_known_hosts():
    from vs_streamhub.credentials import remember_address, scrub_addresses

    remember_address("rtsp://porch-cam.lan:554/stream1")
    remember_address("/srv/private/clips/demo.mp4")
    text = ("Connection to tcp://192.0.2.10:554?timeout=0 failed; rtsp://***@192.0.2.10:554/s refused; "
            "cannot resolve porch-cam.lan; [fe80::1%en0]:554 unreachable; /srv/private/clips/demo.mp4: no such file; "
            "restarts=3 at 12:30:05")  # fmt: skip
    out = scrub_addresses(text)
    for leaked in ("192.0.2.10", "rtsp://", "tcp://", "porch-cam.lan", "fe80", "/srv/private"):
        assert leaked not in out
    assert "restarts=3 at 12:30:05" in out and "refused" in out  # diagnostics survive
    assert scrub_addresses("") == "" and scrub_addresses("no segments yet") == "no segments yet"
