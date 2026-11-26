# Notifications Setup Guide

This guide covers setting up the self-hosted **Vision Sentinel Notification Service** (`vs-notify`), testing locally with desktop/webhook sinks, enabling direct mobile push via **FCM (Android)** and **APNs (iOS)**, and exposing device endpoints safely for remote acknowledgments and clip previews.

> **Zero hosted relay.** Push notifications go directly from your machine to Apple (APNs HTTP/2) or Google (FCM HTTP v1) using your own credentials. Outbound push works behind NAT with **zero inbound ports open**.

---

## 1. Local Service & Household Token (Agent-Executable)

Device-to-server endpoints require a shared household bearer token (`Authorization: Bearer <token>`). Server-internal alert submission (`POST /v1/alerts`) accepts **loopback requests only** and rejects proxied/forwarded requests (`X-Forwarded-For`, `Forwarded`, `X-Real-IP`).

### Step 1 — Generate the household token

```bash
uv run vs-notify token
```

This creates a `0600` file at `~/.config/vision-sentinel/household_token` (or the `token_file` configured in TOML) and prints **only its path** — never the secret value. Alternatively, inject `VS_HOUSEHOLD_TOKEN` at runtime from your OS keychain / secret wrapper.

To view the token once when entering it into a mobile sample app:

```bash
uv run vs-notify token --show
```

### Step 2 — Configure `[notify]` in `vision-sentinel.toml`

Start with zero external accounts using the `desktop` and/or `webhook` sinks:

```toml
[notify]
host = "127.0.0.1"
port = 8790
db = "~/.local/share/vision-sentinel/notify.sqlite"
topics_root = "examples/topics"
cooldown_sec = 300

[[notify.sinks]]
type = "desktop"
dry_run = false
sound = false

[[notify.sinks]]
type = "webhook"
name = "ntfy"
url = "https://ntfy.sh/your-private-topic"
# Prefix header values with `env:` so secrets stay in secret storage, never in TOML:
headers = { Authorization = "env:NTFY_AUTH" }
min_severity = "warning"
```

`vision-sentinel run` / `ui` deliver alerts in-process from this same `[notify]` table; they do not
open port 8790. Run `vs-notify serve` beside them (same file, same `db`) when phones need the
device API. A relative `db` path is resolved next to the config file by both programs. To make the
runtime post to a separately running service instead, set `url = "http://127.0.0.1:8790"` in
`[notify]`.

### Step 3 — Validate and smoke-test locally

1. Check resolved config (credentials and webhook URLs are redacted):
   ```bash
   uv run vs-notify --config vision-sentinel.toml check
   ```
   Expected: `household_token = set` and your listed sink types/names.

2. Start the Notification Service:
   ```bash
   uv run vs-notify --config vision-sentinel.toml serve
   ```

3. Verify liveness and auth enforcement from a second terminal:
   ```bash
   # Open health check -> {"ok": true, ...}
   curl -s http://127.0.0.1:8790/healthz

   # Device route without token -> HTTP 401
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8790/v1/alerts

   # Send a test alert over loopback -> HTTP 200
   curl -s -X POST http://127.0.0.1:8790/v1/alerts \
     -H "Content-Type: application/json" \
     -d '{"topic_id":"demo-replay","severity":"info","title":"Smoke test","body":"Local notification check"}'
   ```

4. Test device listing, acknowledgment, and feedback using your household token:
   ```bash
   TOKEN="$(cat ~/.config/vision-sentinel/household_token)"
   curl -s http://127.0.0.1:8790/v1/alerts -H "Authorization: Bearer $TOKEN"
   ```

---

## 2. Android Push (FCM HTTP v1) — Human Checklist

Outbound Android push uses Firebase Cloud Messaging HTTP v1 with a GCP service account (OAuth2 JWT). A coding agent cannot create your Firebase project for you; complete these steps once, then let your agent finish wiring the config.

### Human Steps (Firebase Console)

- [ ] **1. Create a Firebase project:** Open [Firebase Console](https://console.firebase.google.com/) and create a project (Google Analytics is not required).
- [ ] **2. Register the Android app:**
  - Add an Android app matching the `applicationId` in `mobile/android/app/build.gradle.kts` (default `com.example.visionsentinel`, or your own package ID).
  - Download **`google-services.json`** and place it at `mobile/android/app/google-services.json` (this path is gitignored).
- [ ] **3. Enable Cloud Messaging API (V1):** In Firebase Console → **Project Settings → Cloud Messaging**, confirm **Firebase Cloud Messaging API (V1)** is enabled.
- [ ] **4. Generate a Service Account key:**
  - Go to **Project Settings → Service Accounts** → click **Generate new private key**.
  - Save the downloaded JSON outside the repository with owner-only permissions:
    ```bash
    mkdir -p ~/.config/vision-sentinel && chmod 700 ~/.config/vision-sentinel
    mv ~/Downloads/*-firebase-adminsdk-*.json ~/.config/vision-sentinel/fcm-service-account.json
    chmod 600 ~/.config/vision-sentinel/fcm-service-account.json
    ```
  - *(Optional)* Instead of a file on disk, store the JSON in your OS keychain and pass `service_account_json = "env:VS_FCM_SA_JSON"`.

### Agent-Resumable Configuration

Add the FCM sink to `vision-sentinel.toml`:

```toml
[[notify.sinks]]
type = "fcm"
service_account_path = "~/.config/vision-sentinel/fcm-service-account.json"
```

Verify with `uv run vs-notify --config vision-sentinel.toml check`.

When you install `mobile/android` and enter your server URL + household token, the app calls `POST /v1/devices/register` with `platform = "android"`. Vision Sentinel sends high-priority data-only FCM messages so the app routes `urgent` alerts to the `sentinel_urgent` channel (with optional DND bypass) and attaches inline **Acknowledge**, **Thumbs Up**, and **Thumbs Down** actions on both phone and paired Wear OS watches.

---

## 3. iOS Push (APNs HTTP/2) — Human Checklist

Outbound iOS push talks directly to Apple's APNs HTTP/2 API using an ES256 `.p8` Auth Key.

### Human Steps (Apple Developer & Xcode)

- [ ] **1. Apple Developer Account:** Sign in at [developer.apple.com](https://developer.apple.com/) (Apple Developer Program membership is required for APNs keys).
- [ ] **2. Create an App ID:**
  - Go to **Certificates, Identifiers & Profiles → Identifiers → + → App IDs → App**.
  - Choose your Bundle ID (e.g. `com.yourname.visionsentinel`) and enable the **Push Notifications** capability.
  - Note your **Team ID** (shown in the top-right of the Apple Developer portal).
- [ ] **3. Create an APNs Auth Key (`.p8`):**
  - Go to **Keys → +**, name it (e.g. `Vision Sentinel APNs`), check **Apple Push Notifications service (APNs)**, and register.
  - Download the `.p8` file (**Apple only lets you download it once**) and note the 10-character **Key ID**.
  - Move it outside the repository with `0600` permissions:
    ```bash
    mkdir -p ~/.config/vision-sentinel && chmod 700 ~/.config/vision-sentinel
    mv ~/Downloads/AuthKey_*.p8 ~/.config/vision-sentinel/apns-key.p8
    chmod 600 ~/.config/vision-sentinel/apns-key.p8
    ```
  - *(Optional)* Store the PEM text in your OS keychain and pass `private_key = "env:VS_APNS_KEY"`.
- [ ] **4. Build & sign in Xcode:**
  - Follow `mobile/ios/README.md` to set your Signing Team and Bundle ID and deploy to your iPhone.
  - Use `sandbox = true` while running Debug builds from Xcode; switch to `sandbox = false` for TestFlight / Release builds.

### Agent-Resumable Configuration

Add the APNs sink to `vision-sentinel.toml`:

```toml
[[notify.sinks]]
type = "apns"
team_id = "YOUR_TEAM_ID"
key_id = "YOUR_KEY_ID"
bundle_id = "com.yourname.visionsentinel"
key_path = "~/.config/vision-sentinel/apns-key.p8"
sandbox = true          # true for Xcode debug builds; false for Release/TestFlight
critical_alerts = false # true only if Apple granted the Critical Alerts entitlement
```

Default interruption levels: `info` → `active`, `warning` and `urgent` → `time-sensitive` (works without special entitlements). All alerts carry `aps.category = "VISION_ALERT"` so iOS shows inline **Acknowledge**, **Thumbs Up**, and **Thumbs Down** actions.

---

## 4. Remote Reachability (Ack, Feedback & Clip Previews Outside LAN)

- **Push delivery (server → phone):** Always works behind NAT with zero open ports.
- **Callbacks & media (phone → server):** Acknowledging alerts, rating thumbs up/down, and viewing thumbnails/clips (`GET /v1/alerts/{id}/thumbnail`, `GET /v1/alerts/{id}/clip`) when away from home Wi-Fi require your phone to reach the Notification Service over HTTPS.

Pick **one** of these self-hosted options:

### Option A — Tailscale (Recommended for Households)

No public ports or domain names required. Install Tailscale on your server and phones, then expose only the Notification Service over your private tailnet with automatic HTTPS:

```bash
tailscale serve --bg http://127.0.0.1:8790
```

Enter your `https://<machine>.<tailnet>.ts.net` URL and household token in the mobile app.

> **Security note:** `POST /v1/alerts` rejects requests containing `X-Forwarded-For`, `Forwarded`, or `X-Real-IP` headers, so a proxy/tunnel running on localhost can never submit alerts — only local processes connecting directly to `127.0.0.1` can.

### Option B — Cloudflare Tunnel (`cloudflared`)

If you already manage a domain on Cloudflare, run an outbound `cloudflared` tunnel pointing at `http://127.0.0.1:8790`. Every device endpoint requires `Authorization: Bearer <household_token>`, and `POST /v1/alerts` is automatically blocked for tunneled requests because `cloudflared` adds forwarding headers.

### Option C — Reverse Proxy with TLS (Caddy / Nginx)

Terminate TLS at Caddy or Nginx, preserve `X-Forwarded-For` (so `POST /v1/alerts` stays blocked for remote callers), and proxy only the Notification Service port (`8790`).

> **Important (ARCHITECTURE.md §5.7):** Do **not** expose the Unified Web UI (`vs-ui`) port to the public internet — the Web UI has no login in the MVP and is meant for your LAN or private VPN/tailnet only.

---

## 5. Optional One-Way Camera Speaker Sink

To allow a Topic's Main Agent to trigger a one-way warning chime on a supported camera speaker, opt in at **all three layers**:

1. Camera config (`streamhub`): `speaker_enabled = true` and `volume_cap = 0.3` (hard ceiling).
2. Topic config (`topic.toml`): `speaker_enabled = true`.
3. Notification sink (`vision-sentinel.toml`):
   ```toml
   [[notify.sinks]]
   type = "camera_speaker"
   enabled_cameras = ["front_porch"]
   volume = 0.25
   max_volume = 0.30
   # audio_path = "/path/to/custom-chime.wav"  # optional; defaults to built-in two-tone WAV
   ```

The sink never touches camera URLs or credentials; it calls `StreamHub.play_alert(camera_id, audio_path, volume)` and fires **only** when `alert.request.speaker == True` and the camera is listed in `enabled_cameras`.

---

## 6. HTTP API Reference

| Route | Auth | Description |
|---|---|---|
| `GET /healthz` | Open | Liveness check (`{"ok": true, "sinks": [...]}`) |
| `POST /v1/alerts` | Loopback only | Create alert (`AlertRequest` JSON → `AlertRecord`) |
| `GET /v1/alerts` | Bearer token | List alerts (`topic_id`, `severity`, `unacked`, `since`, `limit`); includes `has_clip`, `has_thumbnail`, `acked_at`, `feedback` |
| `GET /v1/alerts/{id}` | Bearer token | Fetch one alert view |
| `GET /v1/alerts/{id}/thumbnail` | Bearer token | Authenticated JPEG/PNG thumbnail (path-confined to saved/bundle dir) |
| `GET /v1/alerts/{id}/clip` | Bearer token | Authenticated MP4 clip with HTTP Range support (path-confined) |
| `POST /v1/alerts/{id}/ack` | Bearer token | Mark alert seen (`{"by": "optional"}`); first ack sticks |
| `POST /v1/alerts/{id}/feedback` | Bearer token | Rate alert (`{"rating": "up"\|"down", "note": "", "by": ""}`); logs to `<topic>/state/feedback.jsonl` and pins bundle to `state/saved/` |
| `POST /v1/devices/register` | Bearer token | Register/update mobile push token (`{"platform": "ios"\|"android", "token": "...", "name": "..."}`) |
