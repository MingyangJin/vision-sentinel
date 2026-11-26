from vs_runtime.contracts import CameraConfig
from vs_streamhub import StreamHub, WebcamAdapter, WebcamDevice, build_adapter
from vs_streamhub.adapters.webcam import list_v4l2_devices, parse_avfoundation_devices

AVF_STDERR = """
[AVFoundation indev @ 0x142004200] AVFoundation video devices:
[AVFoundation indev @ 0x142004200] [0] FaceTime HD Camera
[AVFoundation indev @ 0x142004200] [1] USB Capture Card
[AVFoundation indev @ 0x142004200] AVFoundation audio devices:
[AVFoundation indev @ 0x142004200] [0] Built-in Microphone
"""


def test_parse_avfoundation_devices():
    devs = parse_avfoundation_devices(AVF_STDERR)
    assert [(d.id, d.name, d.kind, d.backend) for d in devs] == [
        ("0", "FaceTime HD Camera", "video", "avfoundation"),
        ("1", "USB Capture Card", "video", "avfoundation"),
        ("0", "Built-in Microphone", "audio", "avfoundation"),
    ]


def test_list_v4l2_devices(tmp_path):
    dev_root = tmp_path / "dev"
    sys_root = tmp_path / "sys"
    dev_root.mkdir()
    for name, label in [("video2", "USB Webcam"), ("video0", "Integrated Camera"), ("null", None)]:
        (dev_root / name).write_text("")
        if label:
            (sys_root / name).mkdir(parents=True)
            (sys_root / name / "name").write_text(label + "\n")
    devs = list_v4l2_devices(dev_root=dev_root, sys_root=sys_root)
    assert [(d.id, d.name, d.backend) for d in devs] == [
        (str(dev_root / "video0"), "Integrated Camera", "v4l2"),
        (str(dev_root / "video2"), "USB Webcam", "v4l2"),
    ]


def test_webcam_macos_command_and_probe(tmp_path):
    cfg = CameraConfig(
        camera_id="desk",
        adapter="webcam",
        source="0",
        options={"framerate": 20, "resolution": "1280x720", "audio_device": "0", "pixel_format": "uyvy422"},
    )
    devices = parse_avfoundation_devices(AVF_STDERR)
    adapter = WebcamAdapter(cfg, tmp_path / "desk", system="Darwin", list_devices_fn=lambda: devices)
    assert not adapter.wants_mediamtx
    h = adapter.probe()
    assert h.status == "ok" and "FaceTime HD Camera" in h.detail and "Built-in Microphone" in h.detail
    argv, env = adapter.writer_command()
    assert env is None
    assert [
        "-f", "avfoundation", "-framerate", "20", "-video_size", "1280x720",
        "-pixel_format", "uyvy422", "-thread_queue_size", "512", "-i", "0:0",
    ] == argv[argv.index("-f"):argv.index("-i") + 2]
    assert "-c:v" in argv and "libx264" in argv and "-c:a" in argv and "-segment_format" in argv


def test_webcam_macos_defaults_and_default_mic(tmp_path):
    cfg = CameraConfig(
        camera_id="mac_default",
        adapter="webcam",
        source="default:default",
    )
    devices = parse_avfoundation_devices(AVF_STDERR)
    adapter = WebcamAdapter(cfg, tmp_path / "mac", system="Darwin", list_devices_fn=lambda: devices)
    assert adapter.probe().status == "ok"
    argv, _ = adapter.writer_command()
    assert [
        "-f", "avfoundation", "-framerate", "30", "-pixel_format", "nv12",
        "-thread_queue_size", "512", "-i", "0:0",
    ] == argv[argv.index("-f"):argv.index("-i") + 2]


def test_webcam_linux_command_and_probe_missing(tmp_path):
    cfg = CameraConfig(
        camera_id="usb_cam",
        adapter="usb",
        source="0",
        options={"framerate": 15, "resolution": "640x480", "audio_device": "hw:1,0", "audio_backend": "alsa"},
    )
    a = build_adapter(cfg, tmp_path / "usb_cam")
    assert isinstance(a, WebcamAdapter)
    adapter = WebcamAdapter(
        cfg,
        tmp_path / "usb_cam",
        system="Linux",
        list_devices_fn=lambda: [WebcamDevice(id="/dev/video0", name="Logi USB", kind="video", backend="v4l2")],
    )
    assert adapter.probe().status == "ok"
    argv, _ = adapter.writer_command()
    assert "-f" in argv and "v4l2" in argv and "/dev/video0" in argv
    assert "alsa" in argv and "hw:1,0" in argv and "-map" in argv and "1:a:0?" in argv

    # Missing device reports offline cleanly
    missing = WebcamAdapter(
        cfg.model_copy(update={"source": "/dev/video9"}),
        tmp_path / "m",
        system="Linux",
        list_devices_fn=lambda: [WebcamDevice(id="/dev/video0", name="Logi USB", kind="video", backend="v4l2")],
    )
    h = missing.probe()
    assert h.status == "offline" and "/dev/video9" in h.detail and "/dev/video0" in h.detail


def test_webcam_mediamtx_publish_mode(tmp_path):
    fake_mtx = tmp_path / "mediamtx"
    fake_mtx.write_text("#!/bin/sh\n")
    fake_mtx.chmod(0o755)
    cfg = CameraConfig(
        camera_id="mac_cam",
        adapter="webcam",
        source="0",
        options={"ingest": "mediamtx", "platform": "Darwin"},
    )
    hub = StreamHub([cfg], tmp_path / "rec", mediamtx_binary=str(fake_mtx), rtsp_port=18711, api_port=18712)
    plan = hub.plan()
    assert plan["cameras"][0]["ingest"] == "mediamtx"
    assert plan["cameras"][0]["live_url"] == "rtsp://127.0.0.1:18711/maccam"
    argv, _ = hub.adapter("mac_cam").writer_command()
    assert "rtsp://127.0.0.1:18711/maccam" in argv
