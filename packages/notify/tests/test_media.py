"""M3 authenticated thumbnail & clip endpoints, AlertView media flags, and path confinement."""

from __future__ import annotations

import shutil
import stat

import pytest

TOKEN = "test-household-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _add_clip(topics, bundle_id="b-001", cam="cam1", data=b"\x00\x00\x00\x18ftypmp42FAKECLIP"):
    p = topics / "porch" / "state" / "bundles" / bundle_id / cam / "clip.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_media_endpoints_require_bearer_token(local, remote, topics, alert_body):
    _add_clip(topics)
    alert_id = local.post("/v1/alerts", json=alert_body(camera_ids=["cam1"])).json()["id"]
    for bad in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
        for client in (local, remote):
            assert client.get(f"/v1/alerts/{alert_id}/thumbnail", headers=bad).status_code == 401
            assert client.get(f"/v1/alerts/{alert_id}/clip", headers=bad).status_code == 401


def test_thumbnail_and_clip_delivery_and_alert_view_flags(local, remote, topics, alert_body):
    clip_bytes = b"\x00\x00\x00\x18ftypmp42FAKECLIPDATA"
    _add_clip(topics, data=clip_bytes)
    alert_id = local.post("/v1/alerts", json=alert_body(camera_ids=["cam1"])).json()["id"]

    # GET /v1/alerts and GET /v1/alerts/{id} expose has_clip and has_thumbnail
    listed = remote.get("/v1/alerts", headers=AUTH).json()
    assert len(listed) == 1
    assert listed[0]["has_clip"] is True and listed[0]["has_thumbnail"] is True
    single = remote.get(f"/v1/alerts/{alert_id}", headers=AUTH).json()
    assert single["has_clip"] is True and single["has_thumbnail"] is True

    # Thumbnail returns image/jpeg with the bundle's contact_sheet.jpg
    thumb = remote.get(f"/v1/alerts/{alert_id}/thumbnail", headers=AUTH)
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/jpeg")
    assert thumb.content == b"\xff\xd8fake"

    # Clip returns video/mp4 and supports Range requests (for iOS AVPlayer / HTML5 video)
    clip = remote.get(f"/v1/alerts/{alert_id}/clip", headers=AUTH)
    assert clip.status_code == 200
    assert clip.headers["content-type"].startswith("video/mp4")
    assert clip.content == clip_bytes

    partial = remote.get(f"/v1/alerts/{alert_id}/clip", headers={**AUTH, "Range": "bytes=0-7"})
    assert partial.status_code == 206
    assert partial.content == clip_bytes[:8]


def test_missing_media_returns_404_and_false_flags(local, remote, alert_body):
    alert_id = local.post("/v1/alerts", json=alert_body(bundle_id=None)).json()["id"]
    item = remote.get(f"/v1/alerts/{alert_id}", headers=AUTH).json()
    assert item["has_clip"] is False and item["has_thumbnail"] is False
    assert remote.get(f"/v1/alerts/{alert_id}/thumbnail", headers=AUTH).status_code == 404
    assert remote.get(f"/v1/alerts/{alert_id}/clip", headers=AUTH).status_code == 404
    assert remote.get("/v1/alerts/no-such-id/thumbnail", headers=AUTH).status_code == 404
    assert remote.get("/v1/alerts/no-such-id/clip", headers=AUTH).status_code == 404


def test_saved_bundle_serves_media_after_rolling_expiry(local, remote, topics, alert_body):
    _add_clip(topics, data=b"SAVEDMP4")
    alert_id = local.post("/v1/alerts", json=alert_body(camera_ids=["cam1"])).json()["id"]

    # Feedback pins bundle to state/saved/b-001
    fb = remote.post(f"/v1/alerts/{alert_id}/feedback", json={"rating": "up"}, headers=AUTH)
    assert fb.status_code == 200 and fb.json()["pinned"] is True

    # Delete original rolling bundle
    orig = topics / "porch" / "state" / "bundles" / "b-001"
    for f in orig.rglob("*"):
        if f.is_file():
            f.chmod(stat.S_IWUSR | stat.S_IRUSR)
    shutil.rmtree(orig)

    # Both thumbnail and clip still serve from state/saved/b-001
    assert remote.get(f"/v1/alerts/{alert_id}/thumbnail", headers=AUTH).content == b"\xff\xd8fake"
    assert remote.get(f"/v1/alerts/{alert_id}/clip", headers=AUTH).content == b"SAVEDMP4"


@pytest.mark.parametrize("bad_ref", [
    "../../secret.mp4",
    "../secret.mp4",
    "/etc/passwd",
    "cam1/../../../secret.mp4",
    "cam1\\..\\..\\secret.mp4",
])
def test_path_traversal_clip_ref_blocked(local, remote, topics, alert_body, bad_ref):
    secret = topics / "porch" / "state" / "secret.mp4"
    secret.write_bytes(b"SECRET_DATA")
    _add_clip(topics, data=b"LEGIT")
    alert_id = local.post("/v1/alerts", json=alert_body(clip_ref=bad_ref)).json()["id"]

    view = remote.get(f"/v1/alerts/{alert_id}", headers=AUTH).json()
    assert view["has_clip"] is False and view["has_thumbnail"] is False
    assert remote.get(f"/v1/alerts/{alert_id}/clip", headers=AUTH).status_code == 404
    assert remote.get(f"/v1/alerts/{alert_id}/thumbnail", headers=AUTH).status_code == 404


def test_symlink_escape_inside_bundle_blocked(local, remote, topics, tmp_path, alert_body):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"OUTSIDE_SECRET")
    outside_img = tmp_path / "outside.jpg"
    outside_img.write_bytes(b"OUTSIDE_IMG")

    bdir = topics / "porch" / "state" / "bundles" / "b-sym" / "cam1"
    bdir.mkdir(parents=True)
    (bdir / "clip.mp4").symlink_to(outside)
    (bdir / "frame.jpg").symlink_to(outside_img)

    alert_id = local.post(
        "/v1/alerts", json=alert_body(bundle_id="b-sym", camera_ids=["cam1"])
    ).json()["id"]
    view = remote.get(f"/v1/alerts/{alert_id}", headers=AUTH).json()
    assert view["has_clip"] is False and view["has_thumbnail"] is False
    assert remote.get(f"/v1/alerts/{alert_id}/clip", headers=AUTH).status_code == 404
    assert remote.get(f"/v1/alerts/{alert_id}/thumbnail", headers=AUTH).status_code == 404
