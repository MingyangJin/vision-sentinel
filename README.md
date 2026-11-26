# Vision Sentinel

> **Status:** `v0.1` MVP — Milestones M1–M4 implemented (local-first, MIT License).
> **License:** [MIT](LICENSE)

An open-source, **camera-agnostic**, **task-agnostic**, and **harness-agnostic** vision-based AI
alerting system. Local-first on user-owned hardware, with first-class support for the official
**Antigravity (`agy`) harness + Gemini** model family.

## Core Thesis: Split Perception from Control

Continuous monitoring with AI fails when you either hardcode brittle CV pipelines or stream raw
video into a single long-lived VLM session. Vision Sentinel separates the two:

- **Camera Adapter & Stream Hub (`packages/streamhub`):** Pulls each camera once (RTSP, ONVIF,
  USB webcam, phone RTMP push, HTTP segment push from a companion camera client, or local file
  replay) into normalized rolling fMP4 segments and live views, fanning out **M:N** across Topics
  with strict credential isolation ("The One Rule").
- **Ephemeral Vision Sub-Agent** *(default: Gemini 4 Hydrogen Low)*: Spawned fresh and stateless
  per observation window inside a read-only bundle directory (`agy --mode plan`, no shell/writes,
  no reads outside the bundle), returns a compact structured report, and terminates.
- **Stateful Main Agent / Orchestrator** *(default: Gemini 4 Neon Medium+)*: Reviews reports with
  deterministic runtime counters (`packages/runtime`), keeps durable state in the Topic directory
  (the user's `goal.md`, which no agent action can write, plus `vision_rubric.md`,
  `orchestrator_notes.md`, and deterministic `tools/` with 30 s timeouts — including zero-token
  trigger scripts via `"skip_vision": true` that wake the Main Agent only on real events), inspects
  clips on demand, and is the **sole business-logic authority for alerts**. It runs with full
  harness permissions by design; see [SECURITY.md](SECURITY.md) for what that means.
- **Notification Service & Sample Mobile Apps (`packages/notify`, `mobile/ios`, `mobile/android`):**
  Self-hosted notification API with bearer-authenticated device routes, separate Acknowledge and
  Thumbs Up/Down feedback (pinning rated bundles), direct APNs/FCM/Webhook/Desktop sinks, and
  optional double-opt-in one-way camera speaker warnings.
- **Unified Web UI (`packages/ui`):** Native-frame-rate H.264 MSE live multi-camera wall (with
  MJPEG/snapshot fallback), rolling timeline, Topic workspace with presets and mid-flow settings,
  Vision Sub-Agent tick logs, direct Main Agent chat, and Alert Inbox with inline video playback,
  timed/AI mute controls, and one-click "Teach Agent" feedback.
- **Minimal Liveness Watchdog:** Independent non-AI process tracking Stream Hub health and Main
  Agent review progress (`ticks.jsonl`), queuing and replaying missed reports marked `late=True`,
  and sending a fallback system-health alert only when Main Agent review stalls.

> **Disclaimer:** Vision Sentinel is an educational, best-effort AI alerting project—not a medical
> device or certified life-safety alarm. With default cloud-backed models, observation frames and
> audio are sent to the configured provider under your chosen account/API retention tier.

---

## Zero-Hardware Quickstart

Requires **Python 3.12+**, **[uv](https://docs.astral.sh/uv/)**, and **FFmpeg**. Clone this
repository and run everything below from its root.

```bash
# 1. Install all workspace packages and verify offline tests
uv sync
uv run pytest -q

# 2. Inspect the redacted demo plan (synthetic 10 s clip, zero hardware)
uv run vision-sentinel check --config examples/vision-sentinel.toml --check

# 3. Run one full end-to-end cycle with no model calls (fake harness)
uv run vision-sentinel check --config examples/vision-sentinel.toml --harness fake

# 4. Run one live cycle with Antigravity (`agy`)
uv run vision-sentinel check --config examples/vision-sentinel.toml

# 5. Launch the Unified Web UI (http://127.0.0.1:8080)
uv run vision-sentinel ui --config examples/vision-sentinel.toml

# 6. Run headless loop + independent watchdog (two processes)
uv run vision-sentinel run --config examples/vision-sentinel.toml
uv run vision-sentinel watchdog --config examples/vision-sentinel.toml
```

Try the multi-camera, multi-Topic demo (two cameras feeding one Topic, alongside a second Topic on
the same Stream Hub) with zero hardware:

```bash
uv run vision-sentinel check --config examples/multi-cam-demo.toml --harness fake
```

### 60-Second Mac Quickstart (Built-in Camera + Microphone, No IP Camera)

Want to test live video + audio immediately on a Mac without setting up an IP camera? Use the native macOS Camera Client (`clients/macos-camera/`), which captures your Mac's camera + mic with native macOS permissions and streams 2 s fMP4 segments into the `macos_camera` (`http_ingest`) adapter:

```bash
# Terminal 1: start Stream Hub + desk-watch Topic + Unified Web UI (http://127.0.0.1:8080)
uv run vision-sentinel ui --config examples/mac-camera.toml

# Terminal 2: build and open the native macOS Camera app (zero Xcode GUI needed)
./clients/macos-camera/build-app.sh
open ./clients/macos-camera/dist/VisionSentinelCamera.app
```

Approve Camera & Microphone access, click **Start Streaming**, and wave or say *"alert test"* — alerts appear with inline looping video playback in the Unified Web UI.

---

## CLI Commands

| Command | Purpose |
|---|---|
| `vision-sentinel init-topic <path> --camera <id> --goal "..."` | Create a new Topic directory (or copy `--from examples/topics/<name>`) |
| `vision-sentinel check --config <toml> [--check] [--harness fake]` | Print redacted plan (`--check`) or run one observation/review cycle per Topic |
| `vision-sentinel run --config <toml> [--harness fake]` | Run Stream Hub + concurrent M:N Topic loops + in-process watchdog |
| `vision-sentinel ui --config <toml> [--host 127.0.0.1] [--port 8080] [--allow-host NAME]` | Launch the Unified Web UI alongside Stream Hub and live Topic loops |
| `vision-sentinel devices [--json]` | List local built-in/USB webcams and microphones |
| `vision-sentinel watchdog --config <toml> [--once]` | Independent liveness watchdog process (alerts if `run` crashes or review stalls) |
| `vision-sentinel chat --config <toml> --topic <id> "message"` | Send a one-off message to a Topic's Main Agent |

---

## Repository Layout

```text
packages/
  runtime/                # Topic engine, shared contracts, M:N bundle builder, loop, manager, watchdog, CLI
  streamhub/              # Camera adapters (file, RTSP, ONVIF, webcam, RTMP, HTTP push), rolling fMP4, live view, speaker
  harnesses/antigravity/  # Official Antigravity (`agy`) + Gemini driver (vision isolation + --resume orchestrator)
  notify/                 # Self-hosted Notification Service + APNs, FCM, Webhook, Desktop, CameraSpeaker sinks
  ui/                     # Unified Web UI (FastAPI backend + React/TypeScript/Tailwind frontend)
clients/
  macos-camera/           # Native macOS camera client (built-in camera + mic) pushing to the HTTP ingest adapter
mobile/
  ios/                    # Open-source Swift / SwiftUI sample app (APNs push, Ack, Thumbs Up/Down)
  android/                # Open-source Kotlin / Jetpack Compose sample app (FCM push, channels, Wear OS actions)
examples/
  topics/                 # Generic Topic templates (demo-replay, desk-watch, package-watch, door-left-open, multi-cam-demo)
  sample-media/           # Synthetic FFmpeg demo clip (zero personal media)
assets/                   # App icon source
docs/                     # Architecture & setup documentation
```

---

## Documentation & Milestones

- [Setup Runbook for Coding Agents](SETUP_FOR_AGENTS.md) — Step-by-step agent-executable onboarding & verification.
- [Architecture Blueprint](docs/ARCHITECTURE.md) — Core thesis, explicit scope, system diagram, contracts, measurements.
- [Camera Adapters](docs/CAMERA_ADAPTERS.md) — The One Rule, all 6 v1 adapters, credential refs, speaker opt-in, adding adapters.
- [Harness Adapters](docs/HARNESS_ADAPTERS.md) — `AgentHarness` interface, `agy` isolation invariants, vendor terms, adding drivers.
- [Notifications Setup](docs/NOTIFICATIONS_SETUP.md) — Self-hosted `vs-notify`, desktop/webhooks, APNs/FCM human-gate checklists, remote reachability.
- [Security Policy](SECURITY.md) — Security boundary, known limits, and how to report a vulnerability.
- [Contributing](CONTRIBUTING.md) — Development setup, checks to run, and contribution rules.

| Milestone | Scope | Status |
|---|---|---|
| **M1: The Loop** | File replay & RTSP adapters, Topic engine, Antigravity driver, webhook/desktop sinks, watchdog with late replay | Verified |
| **M2: See and Talk** | Unified Web UI (live multi-camera wall, timeline, Topic workspace/presets, Main Agent chat, Alert Inbox feedback) | Implemented |
| **M3: Phone** | Direct APNs & FCM push sinks, authenticated clip/thumbnail endpoints, open-source iOS & Android sample apps | Implemented |
| **M4: Breadth** | ONVIF, USB webcam, RTMP ingest & HTTP push ingest adapters, M:N multi-camera/multi-topic engine, opt-in camera speaker alert sink | Implemented |
