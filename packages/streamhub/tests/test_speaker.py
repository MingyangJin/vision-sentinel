from pathlib import Path

import pytest

from vs_runtime.contracts import CameraConfig
from vs_streamhub import (
    FileReplayAdapter,
    OnvifAdapter,
    RtspAdapter,
    SpeakerDisabledError,
    SpeakerUnsupportedError,
    StreamHub,
    ffmpeg,
)
from vs_streamhub.adapters import BaseAdapter


class SpeakerAdapter(BaseAdapter):
    kind = "fake-speaker"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.played: list[tuple[Path, float]] = []

    def _play_alert(self, audio: Path, volume: float) -> None:
        self.played.append((audio, volume))


def test_speaker_disabled_by_default(tmp_path):
    for adapter in (
        FileReplayAdapter(CameraConfig(camera_id="f", adapter="file", source="x.mp4"), tmp_path),
        RtspAdapter(CameraConfig(camera_id="r", adapter="rtsp", source="rtsp://192.0.2.1/s"), tmp_path),
        OnvifAdapter(CameraConfig(camera_id="o", adapter="onvif", source="http://192.0.2.2/onvif/device_service"), tmp_path),
        SpeakerAdapter(CameraConfig(camera_id="s", adapter="fake"), tmp_path),
    ):
        with pytest.raises(SpeakerDisabledError):
            adapter.play_alert(tmp_path / "chime.mp3", 0.1)
    assert not adapter.played


def test_opted_in_adapters_without_hardware_say_so(tmp_path):
    cfg = CameraConfig(camera_id="f", adapter="file", source="x.mp4", speaker_enabled=True)
    with pytest.raises(SpeakerUnsupportedError, match="no speaker"):
        FileReplayAdapter(cfg, tmp_path).play_alert(tmp_path / "chime.mp3", 0.1)
    # SpeakerUnsupportedError is also a NotImplementedError for backward compatibility
    with pytest.raises(NotImplementedError):
        FileReplayAdapter(cfg, tmp_path).play_alert(tmp_path / "chime.mp3", 0.1)


@pytest.mark.parametrize(("asked", "played"), [(1.0, 0.25), (0.1, 0.1), (-3, 0.0)])
def test_volume_is_capped(tmp_path, asked, played):
    a = SpeakerAdapter(CameraConfig(camera_id="s", adapter="fake", speaker_enabled=True, volume_cap=0.25), tmp_path)
    got = a.play_alert(tmp_path / "chime.mp3", asked)
    assert got == played
    assert a.played == [(tmp_path / "chime.mp3", played)]


def test_hub_play_alert_enforces_double_opt_in_and_volume_cap(tmp_path):
    cams = [
        CameraConfig(camera_id="quiet", adapter="file_replay", source="x.mp4", speaker_enabled=False, volume_cap=0.3),
        CameraConfig(camera_id="porch", adapter="file_replay", source="x.mp4", speaker_enabled=True, volume_cap=0.25),
    ]
    hub = StreamHub(cams, tmp_path / "rec", mediamtx="never")
    played: list[tuple[Path, float]] = []
    hub.adapter("porch").speaker_hook = lambda audio, vol: played.append((audio, vol))

    # Camera disabled -> blocked even if topic opts in
    with pytest.raises(SpeakerDisabledError):
        hub.play_alert("quiet", tmp_path / "alert.wav", 0.2, topic_speaker_enabled=True)
    # Topic disabled -> blocked even if camera opts in
    with pytest.raises(SpeakerDisabledError):
        hub.play_alert("porch", tmp_path / "alert.wav", 0.2, topic_speaker_enabled=False)
    assert not played
    # Both opted in -> plays with volume clamped to 0.25
    vol = hub.play_alert("porch", tmp_path / "alert.wav", 0.9, topic_speaker_enabled=True)
    assert vol == 0.25 and played == [(tmp_path / "alert.wav", 0.25)]
    # Unknown camera -> KeyError
    with pytest.raises(KeyError):
        hub.play_alert("unknown", tmp_path / "alert.wav", 0.1)


def test_rtsp_backchannel_extension_point_and_credential_scrub(monkeypatch, tmp_path):
    monkeypatch.setenv("VS_SPK_CRED", "talker:SpkSecret77")
    audio = tmp_path / "warn.mp3"
    audio.write_bytes(b"fake-audio")
    cfg = CameraConfig(
        camera_id="gate",
        adapter="rtsp",
        source="rtsp://192.0.2.30:554/stream1",
        credential_ref="env:VS_SPK_CRED",
        speaker_enabled=True,
        volume_cap=0.3,
        options={"speaker_backchannel_url": "rtsp://192.0.2.30:554/backchannel"},
    )
    hub = StreamHub([cfg], tmp_path / "rec", mediamtx="never")
    seen_args: list[list[str]] = []

    def fake_run(args, **kw):
        seen_args.append(args)
        return None

    monkeypatch.setattr(ffmpeg, "run", fake_run)
    clamped = hub.play_alert("gate", audio, 0.85, topic_speaker_enabled=True)
    assert clamped == 0.3
    assert seen_args and "volume=0.300" in seen_args[0]
    assert "rtsp://talker:SpkSecret77@192.0.2.30:554/backchannel" in seen_args[0]
