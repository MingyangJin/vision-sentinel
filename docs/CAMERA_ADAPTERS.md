# Camera Adapters & Stream Hub (`vs-streamhub`)

This guide documents **Layer 1** of Vision Sentinel (`packages/streamhub/`): how cameras are ingested once, normalized into rolling fMP4 segments, exposed safely to Topics and the Web UI, and extended with new vendor or protocol adapters.

---

## 1. The One Rule (Credential Isolation)

> **Only `vs_streamhub` ever knows a camera's IP address, protocol URL, or credentials.**

Every other component — Topics, Observation Bundles, Vision Sub-Agents, Main Agents, helper scripts, Notification Sinks, and the Web UI — interacts strictly through:

- Logical `camera_id` strings (e.g., `"front-porch"`, `"demo"`)
- `StreamHealth` (`ok | degraded | offline`, redacted `detail`)
- Extracted bundle assets (`clip.mp4`, `contact_sheet.jpg`, `frame.jpg`, `audio.mp3`, `meta.json`)
- Credential-free live view helpers (`latest_snapshot`, `iter_fmp4`, `iter_mjpeg`, `list_segments`, `extract_clip`)
- One-way opt-in speaker playback (`StreamHub.play_alert(camera_id, audio_path, volume)`)

### Credential References (`credential_ref`)

Never embed `user:password@` in `CameraConfig.source`. Network adapters (`rtsp`, `onvif`, `rtmp_ingest`) actively reject embedded credentials in `source` with a `ValueError`. Instead, set `credential_ref` to a reference resolved at runtime inside `vs_streamhub.credentials`:

| Format | Resolution |
|---|---|
| `env:VAR_NAME` | Environment variable `VAR_NAME` |
| `keychain:SERVICE` or `keychain:SERVICE/ACCOUNT` | macOS login keychain via `security find-generic-password` |
| `file:/path/to/secret` | First line of a secret file (e.g. container/systemd secret mount) |
| `NAME` (bare name) | Environment variable `NAME`, falling back to macOS keychain service `NAME` |

The resolved secret value is either `username:password` (inserted into `source` at launch) or a full URL (e.g. `rtsp://user:pass@192.0.2.10:554/stream1`).

### Redaction & Scrubbing

- Every resolved secret (raw, password component, and URL-encoded forms) is registered in-process so `scrub_credentials(text)` strips it from all subprocess output, SOAP/HTTP faults, and error messages even if a tool prints it outside a URL.
- `StreamHub.plan()` and `python -m vs_streamhub check` never resolve secrets into printed output and redact all URL userinfo and secret query parameters.
- When MediaMTX is used, pulled camera URLs are passed **only via environment variables** (`MTX_PATHS_<PATH>_SOURCE`), never in `mediamtx.yml` or command-line arguments.
- Note on FFmpeg fallback: FFmpeg accepts RTSP/RTMP input URLs only on its command line (`argv`), which other processes owned by the same OS user can inspect via `ps`. Install `mediamtx` when you want camera credentials kept out of `argv`.

---

## 2. Built-In v1 Adapters

All six built-in adapters are registered in `vs_streamhub.ADAPTERS`:

| Adapter name (aliases) | Use case | Default ingest | MediaMTX optional? |
|---|---|---|---|
| `file_replay` (`file`) | Loop a local `.mp4` or folder of clips for zero-hardware testing & demos | Direct FFmpeg rolling fMP4 | Yes (opt in with `options.ingest = "mediamtx"`) |
| `rtsp` (`rtsps`) | Generic IP cameras serving RTSP / RTSPS | MediaMTX when installed, else supervised FFmpeg single pull | Yes (`auto` falls back to FFmpeg) |
| `onvif` | ONVIF cameras via WS-Discovery + `GetProfiles` / `GetStreamUri` | MediaMTX when installed, else supervised FFmpeg single pull | Yes (`auto` falls back to FFmpeg) |
| `webcam` (`usb`) | Local USB / built-in camera (`avfoundation` on macOS, `v4l2` on Linux) | Direct FFmpeg rolling fMP4 | Yes (opt in with `options.ingest = "mediamtx"`) |
| `rtmp_ingest` (`rtmp`) | Phone / mobile / OBS RTMP or RTSP push into the hub | MediaMTX when installed, else supervised FFmpeg listen | Yes (`auto` falls back to FFmpeg) |
| `http_ingest` (`macos_camera`, `camera_client`) | Native companion camera client (e.g. `clients/macos-camera/`) pushing 2 s fMP4 segments over HTTP POST | In-process HTTP push receiver | No (zero MediaMTX / zero FFmpeg capture) |

### Configuration Examples (`hub.toml` / `vision-sentinel.toml`)

Both files accept cameras either as `[cameras.<camera_id>]` tables (used below) or as a
`[[cameras]]` list with an explicit `camera_id` (used in `examples/*.toml`).

#### File Replay (`file_replay` / `file`)
```toml
[cameras.demo]
adapter = "file_replay"
source = "examples/sample-media/demo.mp4"   # single video file OR directory of .mp4/.mov/.mkv files
[cameras.demo.options]
segment_sec = 2
transcode = true                            # default true: H.264 + AAC with 1 s keyframes
```

#### Generic RTSP / RTSPS (`rtsp`)
```toml
[cameras.front_porch]
adapter = "rtsp"
source = "rtsp://192.0.2.10:554/stream1"    # NO credentials here
credential_ref = "keychain:vs-front-porch"  # stores "user:password"
[cameras.front_porch.options]
ingest = "auto"                             # "auto" | "mediamtx" | "ffmpeg"
transport = "tcp"                           # "tcp" | "udp"
timeout_sec = 10
```

#### ONVIF (`onvif`)
```toml
[cameras.driveway]
adapter = "onvif"
source = "http://192.0.2.20/onvif/device_service"   # or "discover" for LAN WS-Discovery
credential_ref = "env:VS_DRIVEWAY_ONVIF"            # stores "user:password"
[cameras.driveway.options]
profile = "sub"                                     # "main" | "sub" | or omit for first profile
# profile_token = "Profile_1"                       # optional exact ONVIF token
# match_scope = "onvif://www.onvif.org/name/..."    # optional filter when source = "discover"
transport = "tcp"
```

#### Native macOS Camera Client / HTTP Segment Push (`http_ingest` / `macos_camera` / `camera_client`)
```toml
[cameras.mac_cam]
adapter = "macos_camera"
# credential_ref = "env:VS_MAC_CAM_TOKEN"   # Bearer token; optional on loopback, REQUIRED for any other bind
[cameras.mac_cam.options]
bind = "127.0.0.1"                          # "0.0.0.0" accepts pushes from another Mac over LAN/Tailscale (needs credential_ref)
port = 8560                                 # multiple http_ingest cameras can share one (bind, port)
```
Pair with `clients/macos-camera/build-app.sh` → `VisionSentinelCamera.app` for clean native macOS Camera & Microphone permissions, hardware H.264 + AAC encoding, live preview, and zero FFmpeg/MediaMTX setup on the camera host.

#### Local Webcam / USB (`webcam` / `usb`)
```toml
# macOS (avfoundation; defaults to 30 fps + nv12; "default:default" grabs built-in camera + mic)
[cameras.desk_mac]
adapter = "webcam"
source = "0:0"                              # video:audio index, "default:default", or device name
[cameras.desk_mac.options]
framerate = 30
resolution = "1280x720"
pixel_format = "nv12"

# Linux (v4l2)
[cameras.desk_linux]
adapter = "webcam"
source = "/dev/video0"
[cameras.desk_linux.options]
framerate = 15
resolution = "1280x720"
# audio_device = "default"
# audio_backend = "alsa"                    # "alsa" | "pulse"
```

#### Phone / Mobile Push Ingest (`rtmp_ingest` / `rtmp`)
```toml
[cameras.phone_cam]
adapter = "rtmp_ingest"
source = "live/phone_cam"                   # optional path; push URL shown by `plan()` / `check`
# credential_ref = "env:VS_PHONE_KEY"       # optional stream key / publish auth
[cameras.phone_cam.options]
protocol = "rtmp"                           # "rtmp" | "rtsp"
ingest = "auto"                             # "auto" (MediaMTX) | "mediamtx" | "ffmpeg"
bind_host = "127.0.0.1"                     # set "0.0.0.0" explicitly for LAN phone push
port = 1935
# publish_ips = ["192.0.2.0/24"]            # required when accepting LAN pushes into MediaMTX
```

### Port Preflight (MediaMTX)

Before launching MediaMTX, `MediaMTXServer.preflight()` checks that `rtsp_port` (default `8554`), `api_port` (default `9997`), and `rtmp_port` (default `1935`, when RTMP is enabled) are free. If another process (such as a second MediaMTX instance) already holds a port, `StreamHub.start()` fails fast with a `RuntimeError` naming the busy port and the `StreamHub` option to override it.

---

## 3. Adapter Lifecycle Contract

Every adapter satisfies `vs_runtime.contracts.CameraAdapter`:

```python
class CameraAdapter(Protocol):
    camera_id: str
    def probe(self) -> StreamHealth: ...
    def start(self) -> None: ...
    def health(self) -> StreamHealth: ...
    def stop(self) -> None: ...
```

- **`probe()`**: Non-destructive connectivity/configuration check returning `StreamHealth(camera_id, status, detail)` with redacted detail.
- **`start()`**: Starts supervised ingestion into `<recordings_root>/<camera_id>/` as rolling fMP4 segments (`YYYY-MM-DD_HH-MM-SS[-ffffff].mp4`).
- **`health()`**: Evaluates segment freshness (`newest_write` mtime) and supervisor/MediaMTX state into `ok`, `degraded`, or `offline`.
- **`stop()`**: Terminates child processes cleanly (`SIGTERM`, finalizing the open segment, then `SIGKILL` fallback).
- **`play_alert(audio, volume)`** *(optional)*: One-way speaker alert output (see §5).

All FFmpeg-backed adapters use `Supervisor`, which restarts crashed or stalled writers with exponential backoff (1 s → 30 s) and scrubs every log line through `scrub_credentials()`.

---

## 4. Live View & Rolling Buffer API (Web UI & Topics)

`StreamHub` implements `vs_runtime.contracts.StreamHubClient` for Topics (`health`, `extract_window`) and exposes `vs_streamhub.live.LiveStreamProvider` (also delegated on `StreamHub`) for the Web UI:

- `hub.list_cameras() -> list[dict]` — credential-free camera status summaries.
- `hub.latest_snapshot(camera_id, *, width=960, quality=5) -> bytes | None` — fresh JPEG extracted safely from a snapshot copy of the newest readable rolling segment.
- `hub.iter_fmp4(camera_id) -> AsyncIterator[bytes]` — native-frame-rate fragmented MP4 stream (H.264 remuxed with `-c:v copy` for browser Media Source Extensions, zero video transcoding), reading from the local loopback MediaMTX restream when ready or chaining finalized rolling fMP4 segments cleanly.
- `hub.iter_mjpeg(camera_id, *, fps=2.0, width=960, quality=5) -> AsyncIterator[bytes]` — multipart MJPEG fallback stream (`MJPEG_MEDIA_TYPE = "multipart/x-mixed-replace; boundary=frame"`), reading from the local loopback MediaMTX restream when ready or polling rolling segments safely.
- `hub.list_segments(camera_id, *, limit=None) -> list[SegmentInfo]` — rolling segment metadata (`name`, `start`, `end`, `duration_sec`, `size_bytes`, `is_active`).
- `hub.segment_file(camera_id, segment_name) -> Path` — path-confined lookup for a single segment.
- `hub.extract_clip(camera_id, start, end, out_path, *, width=960) -> Path | None` — browser-playable `+faststart` MP4 clip sliced safely without modifying active segments.

---

## 5. Camera Speaker One-Way Alert Guardrails

One-way camera speaker output (for warnings or deterrence, **never** two-way intercom) is strictly guarded:

1. **Disabled by default:** `CameraConfig.speaker_enabled` defaults to `False` and `TopicConfig.speaker_enabled` defaults to `False`.
2. **Double opt-in:** `StreamHub.play_alert(camera_id, audio_path, volume, topic_speaker_enabled=...)` requires **both** the topic and the camera to opt in; otherwise it raises `SpeakerDisabledError` (`PermissionError`).
3. **Hard volume cap:** Requested volumes are unconditionally clamped to `[0.0, camera.volume_cap]` (default cap `0.30`).
4. **Explicit capability check:** Adapters without a configured speaker backend or hook raise `SpeakerUnsupportedError` (a subclass of `NotImplementedError`).
5. **Extension points:**
   - Attach `adapter.speaker_hook = fn(audio_path, clamped_volume)` for custom hardware bridges or tests.
   - `RtspAdapter` and `OnvifAdapter` support an optional FFmpeg audio backchannel when `options.speaker_backchannel_url` is configured alongside `speaker_enabled = true`.

---

## 6. How to Add a Custom Camera Adapter

1. **Subclass `BaseAdapter`** in `packages/streamhub/src/vs_streamhub/adapters/<my_adapter>.py`:
   ```python
   from pathlib import Path
   from vs_runtime.contracts import StreamHealth
   from vs_streamhub.adapters.base import BaseAdapter, ffmpeg_base, segment_args

   class MyVendorAdapter(BaseAdapter):
       kind = "my_vendor"

       @property
       def wants_mediamtx(self) -> bool:
           return False  # True if MediaMTX should pull or receive a push

       def writer_command(self) -> tuple[list[str], dict[str, str] | None]:
           # Resolve secrets ONLY here via vs_streamhub.credentials.resolve_secret()
           argv = [*ffmpeg_base(), "-i", ..., *segment_args(self.segment_dir, self.segment_sec)]
           return argv, None

       def probe(self) -> StreamHealth:
           return StreamHealth(camera_id=self.camera_id, status="ok", detail="ready")

       # Optional: implement _play_alert(self, audio: Path, volume: float) -> None
       # BaseAdapter.play_alert() already enforces speaker_enabled and volume_cap!
   ```
2. **Register it** in `packages/streamhub/src/vs_streamhub/adapters/__init__.py` inside `ADAPTERS["my_vendor"] = MyVendorAdapter`.
3. **Respect The One Rule:**
   - Reject credentials embedded in `config.source`.
   - Resolve secrets strictly via `vs_streamhub.credentials.resolve_secret()`.
   - Run `scrub_credentials()` on any error message or log line.
   - Keep `describe()` credential-free.
   - If you override `health()`, pass its `detail` through `scrub_addresses()` as `BaseAdapter.health()` does: health reaches the Web UI and Observation Bundles, which must never see a camera URL, host, or IP.
4. **Add offline tests** under `packages/streamhub/tests/` covering config validation, credential redaction, command/plan generation, `probe()`, health transitions, and speaker guards without requiring physical hardware.
