# Vision Sentinel — Setup Runbook for Coding Agents

> **Audience:** Coding agents (Antigravity, Claude Code, Codex, etc.) setting up Vision Sentinel
> on a user's machine. Every step has exact commands, pass/fail checks, and explicit pauses
> whenever a human account action is unavoidable.

---

## Ground Rules (Read First)

1. **Secret hygiene:** Never commit credentials, tokens, `.p8` keys, or Firebase JSON files into
   the repo or put raw passwords inside `vision-sentinel.toml`. Use OS keychain / environment
   variable references (`credential_ref`, `env:VAR_NAME`).
2. **The One Rule:** Only `vs_streamhub` resolves camera URLs or credentials. Never pass camera
   URLs into Topic configs, helper scripts, or agent prompts.
3. **Permitted harness use:** Launch the official `agy` binary directly. Never read, copy, or
   proxy harness OAuth credentials.
4. **Disclosure to the user:** Remind the user before enabling live cloud models that observation
   frames and audio from configured cameras are sent to the configured model provider under their
   chosen account/API retention tier.
5. **Web UI exposure:** Bind `vision-sentinel ui` to `127.0.0.1` by default. Never expose the
   Web UI directly to the public internet (no login in MVP; use LAN, Tailscale, or a private tunnel).

---

## Phase 1: Prerequisites & Workspace Install (Automated)

### 1.1 Check required local binaries

```bash
python3 --version        # require >= 3.12
uv --version             # require uv installed
ffmpeg -version | head -1 # require ffmpeg in PATH
```

- **Pass:** All three exit 0 (`python3 >= 3.12`).
- **If missing:** Install via Homebrew (`brew install uv ffmpeg`) on macOS or the OS package
  manager on Linux.

### 1.2 Sync the workspace & run offline tests

```bash
uv sync
uv run pytest -q
```

- **Pass:** `uv sync` resolves all workspace members (`vs-runtime`, `vs-streamhub`,
  `vs-harness-antigravity`, `vs-notify`, `vs-ui`) and `pytest` passes with 0 failures (live
  `agy` tests skip automatically unless `VS_LIVE_AGY=1`).

---

## Phase 2: Zero-Hardware Smoke Test (Automated, No Accounts Needed)

Verify the full pipeline (file replay → rolling fMP4 buffer → window slicing → deterministic
helper tool → Vision Sub-Agent → Main Agent → Notification Service) with zero cameras and zero
API/harness calls first:

### 2.1 Print the redacted execution plan

```bash
uv run vision-sentinel check --config examples/vision-sentinel.toml --check
```

- **Pass:** Exits 0, prints JSON listing camera `demo` (`adapter: "file"`), topic `demo-replay`,
  tool `inventory.py`, and sink `desktop`. No credentials appear in output.

### 2.2 Run one full cycle with the fake harness

```bash
uv run vision-sentinel check --config examples/vision-sentinel.toml --harness fake
```

- **Pass:** Exits 0 in ~12–15 s; prints JSON with:
  - `"topic": "demo-replay"`
  - `"camera_status": {"demo": "ok"}`
  - `"assets"` containing `demo/contact_sheet.jpg`, `demo/frame.jpg`, `demo/audio.mp3`, `demo/meta.json`
  - `"degraded": false`, `"delivered": true`

### 2.3 Verify M:N multi-camera & multi-topic execution

```bash
uv run vision-sentinel check --config examples/multi-cam-demo.toml --harness fake
```

- **Pass:** Exits 0; prints JSON for both `multi-cam-demo` (`cam_north` + `cam_south` synchronized)
  and `demo-replay` sharing one Stream Hub process.

### 2.4 Verify standalone watchdog

```bash
uv run vision-sentinel watchdog --config examples/vision-sentinel.toml --once
```

- **Pass:** Exits 0 and outputs JSON with `cameras`, `topics`, `events`, `alerts`.

---

## Phase 3: Antigravity Harness Verification (Automated + Optional Sign-In Gate)

Check whether the official Antigravity CLI (`agy`) is installed and signed in:

```bash
uv run vs-agy-probe --check
```

- **Pass:** JSON reports `"ok": true`, `agy` version, and availability of the default models
  (`gemini-4-hydrogen-low`, `gemini-4-neon-medium`).
- **Human Gate (only if `agy` is missing or not signed in):** Pause and ask the user to install
  Antigravity CLI and complete interactive sign-in, then re-run `uv run vs-agy-probe --check`.

Once `vs-agy-probe --check` passes, run **1 real live cycle** on the synthetic demo clip:

```bash
uv run vision-sentinel check --config examples/vision-sentinel.toml
```

- **Pass:** Exits 0; Vision Sub-Agent (`gemini-4-hydrogen-low` in `--mode plan`, `cwd = bundle_dir`)
  reports `"status": "clear"` describing the test pattern and tone, and Main Agent
  (`gemini-4-neon-medium`) completes review with non-zero token usage logged in
  `examples/topics/demo-replay/state/ticks.jsonl`.

See [docs/HARNESS_ADAPTERS.md](docs/HARNESS_ADAPTERS.md) for harness invariants, media
capabilities, and adding other official coding-agent CLIs.

---

## Phase 4: Configure Real Cameras & Topics (Interactive With User)

1. Read [docs/CAMERA_ADAPTERS.md](docs/CAMERA_ADAPTERS.md) for all 6 built-in adapters
   (`file_replay`, `rtsp`, `onvif`, `webcam`, `rtmp_ingest`, `http_ingest` / `macos_camera`),
   credential storage (`keychain:` / `env:` / `file:`), and optional one-way camera speaker opt-in
   (`speaker_enabled = false` by default).
   - **No IP camera yet on macOS?** Build and open the native macOS Camera Client (`clients/macos-camera/build-app.sh` → `open clients/macos-camera/dist/VisionSentinelCamera.app`) paired with `examples/mac-camera.toml` for native Camera & Microphone capture with zero MediaMTX/FFmpeg capture setup.
2. Create a local config file (outside Git or gitignored, e.g. `vision-sentinel.local.toml`) and
   initialize Topics from templates:

   ```bash
   uv run vision-sentinel init-topic topics/front-porch \
     --from examples/topics/package-watch \
     --camera front_porch \
     --goal "Notify me when a package arrives or is picked up."
   ```

3. Verify the config cleanly before starting:

   ```bash
   uv run vision-sentinel check --config vision-sentinel.local.toml --check
   ```

   Confirm every password/token is redacted (`***`) and camera sources hold no inline credentials.

4. *(Optional — zero-token deterministic triggers)* For threshold or signal-driven goals (such as
   sound volume alerts or motion pre-filters), mention in `goal.md` (or via Topic chat) that the
   Main Agent should write a deterministic script in `tools/` returning `"skip_vision": true` and
   `"trigger": false|true`. Quiet ticks consume 0 Vision tokens and 0 Main Agent tokens, while
   triggered ticks wake the Main Agent immediately.

---

## Phase 5: Launch Unified Web UI, Loop, and Watchdog

### Option A: All-in-one Unified Web UI + live loops (recommended for interactive setup)

```bash
uv run vision-sentinel ui --config examples/vision-sentinel.toml --host 127.0.0.1 --port 8080
```

- Open `http://127.0.0.1:8080` to view the Live Wall, Timeline, Topics & Main Agent Chat, and
  Alert Inbox (with Acknowledge and Thumbs Up/Down feedback).
- Pass `--no-loop` to run the UI and live cameras without running Topic ticks.

### Option B: Headless daemon + independent watchdog process

Run these as two separate supervised processes (e.g., `launchd` on macOS or `systemd` on Linux)
so a crash or hang of the main runtime still triggers a watchdog system-health alert:

```bash
# Process 1: Stream Hub + M:N Topic loops + in-process camera-drop watchdog
uv run vision-sentinel run --config vision-sentinel.local.toml

# Process 2: Independent liveness watchdog (reads ticks.jsonl & segment ages from disk)
uv run vision-sentinel watchdog --config vision-sentinel.local.toml
```

---

## Phase 6: Notifications & Sample Mobile Apps (Human-Gate Checklists)

For local desktop alerts, webhooks (ntfy/Slack/Discord/Pushover/Home Assistant), self-hosted
`vs-notify` HTTP API, or direct mobile push notifications (iOS APNs / Android FCM), follow
[docs/NOTIFICATIONS_SETUP.md](docs/NOTIFICATIONS_SETUP.md).

Unavoidable human gates isolated in `docs/NOTIFICATIONS_SETUP.md`:
- **macOS / OS permissions:** Approve Local Network / Camera permissions if prompted.
- **Android Push (FCM):** Create a Firebase/GCP project, download `google-services.json` and the
  service-account JSON, and build/sideload `mobile/android/`.
- **iOS Push (APNs):** Enroll in Apple Developer, enable Push Notifications on your App ID,
  generate an APNs Auth Key (`.p8`), and select your signing team in Xcode for `mobile/ios/`.

---

## Rollback / Clean Reset

All runtime state lives inside gitignored `data/` and `topics/<id>/state/` directories. To reset
a demo run without touching Topic rules or goals:

```bash
rm -rf data/ examples/topics/demo-replay/state/ examples/topics/multi-cam-demo/state/
```
