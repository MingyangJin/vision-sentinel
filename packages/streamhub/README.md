# vs-streamhub

The Stream Hub (ARCHITECTURE.md §5.1): camera adapters, single-pull ingest, a rolling fMP4
buffer per camera, window slicing for Observation Bundles, Web UI live view helpers,
one-way camera speaker guards, and the credential boundary.

See [docs/CAMERA_ADAPTERS.md](../../docs/CAMERA_ADAPTERS.md) for full adapter documentation,
configuration examples, and how to add custom adapters.

**The one rule.** Only this package resolves camera URLs and credentials. Everything else
knows a camera by its `camera_id` and gets health, live snapshots/MJPEG, local restream
URLs, and extracted media files. `CameraConfig.source` is the camera URL *without*
credentials; `credential_ref` names the secret (`env:NAME`, `keychain:SERVICE[/ACCOUNT]`
on macOS, `file:/path`, or a bare name). `scrub_credentials()` is applied to every
subprocess log line and error message, and removes every secret value resolved in the
process, inside a URL or not.

## Layout on disk

```text
<recordings_root>/<camera_id>/YYYY-MM-DD_HH-MM-SS[-ffffff].mp4   rolling fMP4 segments (local time)
<recordings_root>/.mediamtx/mediamtx.yml                         generated, holds no secret
```

Segments older than `retention_sec` are pruned (never a camera's newest one). The segment
still being written is fragmented and flushed per keyframe, and readers only ever copy it.

## Adapters (v1)

| adapter (aliases) | ingest | notes |
|---|---|---|
| `file_replay` (`file`) | FFmpeg writes segments directly (no daemon); `options.ingest="mediamtx"` publishes to MediaMTX | loops one file or a folder in real time; H.264 + AAC, keyframe every 1 s |
| `rtsp` (`rtsps`) | MediaMTX when installed (`options.ingest="auto"`), else supervised FFmpeg single pull | MediaMTX gets the credentialed URL via env only; optional `speaker_backchannel_url` |
| `onvif` | MediaMTX when installed, else supervised FFmpeg single pull | WS-Discovery (`discover_onvif`) + SOAP `GetCapabilities` / `GetProfiles` / `GetStreamUri` |
| `webcam` (`usb`) | FFmpeg writes segments directly (`avfoundation` on macOS, `v4l2` on Linux) | `list_webcam_devices()`, optional audio capture & MediaMTX publish |
| `rtmp_ingest` (`rtmp`) | MediaMTX RTMP/RTSP listener when installed, else supervised FFmpeg listen fallback | accepts phone/OBS push streams into rolling fMP4 segments |
| `http_ingest` (`macos_camera`, `camera_client`) | in-process HTTP receiver (no FFmpeg capture, no MediaMTX) | a companion client such as `clients/macos-camera` POSTs 2 s fMP4 segments; a Bearer token (`credential_ref`) is required unless bound to loopback |

## Live View, Window Slicing & Speaker API

```python
from vs_runtime.contracts import CameraConfig
from vs_streamhub import StreamHub, MJPEG_MEDIA_TYPE

hub = StreamHub(
    [CameraConfig(camera_id="demo", adapter="file_replay", source="examples/sample-media/demo.mp4")],
    recordings_root="recordings",
    retention_sec=3600,
)
with hub:
    hub.wait_until_ok("demo", 20)
    assets = hub.extract_window("demo", start, end, bundle_dir / "demo", {"video", "contact_sheet", "audio", "telemetry"})
    jpeg = hub.latest_snapshot("demo")
    segs = hub.list_segments("demo")
    clip = hub.extract_clip("demo", start, end, out_dir / "playback.mp4")
    # async for chunk in hub.iter_mjpeg("demo", fps=2.0): ...
    # hub.play_alert("demo", chime_path, volume=0.2, topic_speaker_enabled=True)
```

`play_alert()` enforces double opt-in (topic + camera `speaker_enabled=True`) and clamps
volume to `camera.volume_cap`, raising `SpeakerDisabledError` when disabled and
`SpeakerUnsupportedError` (subclass of `NotImplementedError`) when no speaker backend/hook
is configured.

## Standalone CLI

```bash
python -m vs_streamhub check hub.toml        # redacted plan + probe of each camera
python -m vs_streamhub run   hub.toml        # ingest until Ctrl-C
python -m vs_streamhub slice hub.toml demo --window 30 --out /tmp/slice
```

Requires `ffmpeg`/`ffprobe` on PATH; `mediamtx` is optional.
