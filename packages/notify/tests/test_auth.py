"""Household token storage and the CLI's redacted check."""

from __future__ import annotations

import stat

from vs_notify.auth import (bearer_ok, ensure_household_token, is_loopback_request,
                            load_household_token)
from vs_notify.server import main


def test_token_file_created_0600_and_loaded(tmp_path, monkeypatch):
    monkeypatch.delenv("VS_HOUSEHOLD_TOKEN", raising=False)
    path = ensure_household_token(tmp_path / "cfg" / "household_token")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    token = load_household_token(path)
    assert token and len(token) >= 40
    assert ensure_household_token(path) == path and load_household_token(path) == token  # stable
    monkeypatch.setenv("VS_HOUSEHOLD_TOKEN", "from-env")
    assert load_household_token(path) == "from-env"


def test_bearer_ok():
    assert bearer_ok("Bearer abc", "abc") and bearer_ok("bearer abc", "abc")
    assert not bearer_ok("Bearer abd", "abc")
    assert not bearer_ok(None, "abc") and not bearer_ok("Bearer abc", None)
    assert not bearer_ok("Bearer ", "") and not bearer_ok("abc", "abc")


def test_is_loopback_request():
    assert is_loopback_request("127.0.0.1", {}) and is_loopback_request("::1", {})
    assert not is_loopback_request("192.168.0.2", {})
    assert not is_loopback_request(None, {}) and not is_loopback_request("localhost-ish", {})
    assert not is_loopback_request("127.0.0.1", {"x-forwarded-for": "1.2.3.4"})


def test_cli_check_redacts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VS_HOUSEHOLD_TOKEN", "super-secret-token")
    cfg = tmp_path / "vs.toml"
    cfg.write_text(
        '[notify]\ndb = ":memory:"\n'
        '[[notify.sinks]]\ntype = "webhook"\nurl = "https://hooks.example/T0KEN-IN-URL"\n'
        '[[notify.sinks]]\ntype = "apns"\nteam_id = "T"\nkey_id = "K"\n'
        'bundle_id = "com.example.app"\nprivate_key = "SECRET_APNS_PEM"\n'
        '[[notify.sinks]]\ntype = "fcm"\nservice_account_path = "/secret/sa.json"\n'
    )
    assert main(["--config", str(cfg), "check"]) == 0
    out = capsys.readouterr().out
    assert "super-secret-token" not in out and "T0KEN-IN-URL" not in out
    assert "SECRET_APNS_PEM" not in out and "/secret/sa.json" not in out
    assert "household_token = set" in out and "webhook" in out and "apns" in out and "fcm" in out


def test_cli_token_prints_path_not_value(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("VS_HOUSEHOLD_TOKEN", raising=False)
    cfg = tmp_path / "vs.toml"
    cfg.write_text(f'[notify]\ntoken_file = "{tmp_path / "tok"}"\n')
    assert main(["--config", str(cfg), "token"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == str(tmp_path / "tok")
    assert load_household_token(tmp_path / "tok") not in out
