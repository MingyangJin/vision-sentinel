# Vision Sentinel Camera (macOS Client)

A lightweight native macOS SwiftUI app that turns **any Mac's built-in camera + microphone** (or Continuity Camera / USB webcam) into a first-class Vision Sentinel camera—with zero IP camera, zero MediaMTX, and clean native macOS Camera & Microphone permission prompts.

## How It Works

1. Uses Apple's native `AVCaptureSession` + `AVAssetWriter` (CMAF fragmented MP4, hardware H.264 + AAC mono) inside a signed `.app` bundle with `NSCameraUsageDescription` and `NSMicrophoneUsageDescription`.
2. Every 2 seconds, emits a self-contained `.mp4` segment (`init + media`) and `POST`s it to Vision Sentinel's `macos_camera` / `http_ingest` adapter:
   `POST http://<host>:8560/v1/ingest/<camera_id>/segment`
3. Works both on `127.0.0.1` (same Mac) and over your LAN / Tailscale (turn any spare Mac into a remote camera).
   For a remote camera, the server's ingest `bind` must be non-loopback, and then a Bearer token is required: set `credential_ref` on the camera (see [`docs/CAMERA_ADAPTERS.md`](../../docs/CAMERA_ADAPTERS.md)) and enter the same token in the app. The app keeps its settings, including that token, in its macOS preferences (`UserDefaults`), not in the Keychain.

## Quickstart (60 Seconds, Zero IP Camera)

1. **Start Vision Sentinel with the built-in Mac camera config** (Terminal 1):
   ```bash
   cd /path/to/vision-sentinel
   uv run vision-sentinel ui --config examples/mac-camera.toml
   ```
2. **Build and open the macOS Camera app** (Terminal 2):
   ```bash
   ./clients/macos-camera/build-app.sh
   open ./clients/macos-camera/dist/VisionSentinelCamera.app
   ```
3. Approve macOS's Camera & Microphone prompts, keep the default `http://127.0.0.1:8560` + `mac_cam`, and click **Start Streaming**.
4. Open **http://127.0.0.1:8080** — your Mac's live camera, audio, AI alerts (with inline video playback), and Main Agent chat are live immediately.

## Tests

```bash
cd clients/macos-camera
swift test
```

## License

[MIT](../../LICENSE), the same as the rest of this repository.
