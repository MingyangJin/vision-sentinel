# Vision Sentinel

A hierarchical AI monitoring system for continuous camera streams.

I started this project for a specific reason: I wanted an additional safety layer for monitoring my baby at night.

Traditional camera monitors still depend on someone actively watching. Modern computer vision and vision-language models can do more: continuously observe a scene, detect unusual situations, reason about what happened, and escalate when something deserves attention.

The baby-monitor use case is the starting point, but the architecture is intentionally general enough for pets, home security, elderly monitoring, machinery, wildlife cameras, or other always-on camera applications.

> **Vision Sentinel is not a medical device and should not replace supervision, appropriate safety practices, or medical monitoring.**

## Architecture

The core idea is simple: **use cheap specialized models continuously, and increasingly capable models only when needed.**

```mermaid
flowchart TD
    CAM[Camera / IR / Audio] --> BUF[Rolling Video Buffer]

    BUF --> CV[Stage 1: Continuous CV]
    CV -->|Suspicious| QWEN[Stage 2: Local VLM]
    BUF -->|Periodic review| QWEN
    QWEN -->|Escalate| GEMINI[Stage 3: Frontier VLM]

    CV --> RISK[Risk Engine]
    QWEN --> RISK
    GEMINI --> RISK

    RISK --> ALERT[Alerts]

    HEALTH[Independent Watchdog] --> ALERT
```

### Stage 1 — Continuous CV

Small models run continuously at high frame rates and produce structured observations.

For the initial baby-monitor configuration:

* baby / face detection
* nose and mouth visibility
* body pose
* blanket and object segmentation
* object proximity to the face
* motion and respiration-like chest movement
* camera obstruction and image quality

Example:

```text
baby_detected = true
face_visible = true
mouth_nose_visible = true
pose = supine
blanket_face_overlap = 0.03
respiration_motion_present = true
```

Stage 1 detects **known observable conditions** cheaply and continuously.

### Stage 2 — Local VLM

A local multimodal model such as Qwen provides semantic understanding.

It has two independent jobs:

1. **Triggered review** — inspect video around a suspicious CV event.
2. **Retrospective review** — periodically review recent footage for anything the specialized detectors may have missed.

This second path is important: a missed CV trigger should not automatically mean a missed event.

### Stage 3 — Frontier VLM

Ambiguous or higher-risk events can be escalated to a stronger cloud model such as Gemini.

This stage runs rarely, keeping most processing local while still allowing access to frontier-level video reasoning when useful.

Models should remain replaceable as better options become available.

## Escalation is not voting

Higher-level models should add evidence, not necessarily veto lower-level alerts.

A high-confidence critical detector can alert immediately while Qwen and Gemini analyze the event in parallel:

```text
critical CV signal
    ├──> alert
    ├──> local VLM review
    └──> frontier VLM review
```

The system should prefer an occasional false positive over silently suppressing a potentially important event.

## System health

The AI pipeline should not be responsible for deciding whether the AI pipeline itself is alive.

A separate deterministic watchdog should monitor things such as:

```text
camera.last_frame_age
camera.fps

cv.last_inference_age
cv.latency

local_vlm.available
cloud_vlm.available

disk.free_space
notification_service.available
```

Failures such as a dead camera feed or stalled CV process should generate alerts independently of Qwen or Gemini.

Eventually, the watchdog may run on separate hardware so failure of the primary machine does not also disable monitoring.

An optional coding/operations agent could sit above this layer to inspect logs, diagnose failures, restart allowlisted services, and verify recovery—but it should not autonomously change safety thresholds or disable alerts.

## Canary testing

A healthy process can still produce bad results.

Vision Sentinel should periodically run known test clips through the actual pipeline:

```text
test video
  ↓
decoder
  ↓
CV / VLM
  ↓
risk engine
  ↓
expected result
```

This tests actual behavior rather than just checking whether a process returns `200 OK`.

## Initial use case: baby monitoring

The first implementation will focus on nighttime monitoring for observable conditions such as:

* persistent face obstruction
* bedding or objects near the face
* unusual body positions
* unexpected objects or people
* prolonged lack of movement
* loss of visible respiration-like motion
* camera or monitoring failure

Existing infant-specific research models for pose estimation, crib hazards, and video-based respiration may provide useful starting points.

Visual monitoring cannot reliably determine oxygen saturation, airway patency, or whether an infant is medically safe; those are outside the scope of this project.

## Generalizing the system

The core pipeline should be independent of the monitoring domain.

A monitor configuration defines:

1. what signals matter
2. which models produce them
3. what counts as suspicious
4. what context the VLM receives
5. how risk is scored
6. when humans are alerted

Potential future configurations include baby, pet, security, elderly, wildlife, and equipment monitoring.

Vision Sentinel also does not need to become another full NVR. Mature projects such as Frigate already handle RTSP ingestion, recording, motion detection, hardware acceleration, and camera management well. Ideally, Vision Sentinel can integrate with those systems and focus on **reasoning, escalation, and reliability**.

## Camera input

The first implemented layer. See [docs/reolink-e1-pro.md](docs/reolink-e1-pro.md)
for what the hardware actually does, measured rather than assumed.

```
sources/    vendor integration - how to reach a stream
recorder/   buffer and fan-out to consumers
view.py     development MJPEG viewer (a bus consumer)
```

`sources/` rather than `cameras/` because canary testing replays known clips
through the real pipeline: a test clip is a source but not a camera, and should
be an ordinary implementation rather than a special case.

There is deliberately no `Source` base class yet. One vendor is too small a
sample to design an interface from — the directory is the commitment to the
layering, and the protocol gets extracted when a second source shows what
actually varies.

`FrameBus` applies no backpressure: a slow consumer skips frames rather than
stalling the decoder, so a laggy debug viewer can never slow the CV path. It
holds one frame rather than a queue, which is why the decode loop has no
accumulating state.

### Setup

Credentials live in `.env`, which is gitignored:

```bash
cp .env.example .env   # then fill in REOLINK_HOST / USER / PASSWORD
uv sync
```

Enable RTSP on the camera first (recent Reolink firmware ships it off, under
Network -> Advanced -> Port Settings). Use the camera's own device account, not
a Reolink cloud login.

### Usage

```bash
uv run vs-camera probe          # report what each stream really is
uv run vs-camera view           # live MJPEG at http://127.0.0.1:8080/
uv run vs-camera view --stream main --port 8080
```

`view` binds to localhost by default, so the feed is not exposed on the LAN.

### What the first long run taught us

A 12-hour run streamed 318,912 frames at a flat 10.00 fps for 8h52m, then
stopped delivering — while ping, TCP on 554, `RTSP/1.0 200 OK`, the camera's
HTTP API, and its own `"online": 1` status all continued to report healthy.

The cause turned out to be host-side, not the camera: ffmpeg on the monitoring
machine could no longer reach that address, while every other tool could. That
makes the lesson sharper rather than weaker — **a watchdog has to detect its
own inability to obtain frames, not the camera's health.** Only the frame count
caught it, and a watchdog sharing the host would have been blind to it.

See [docs/reolink-e1-pro.md](docs/reolink-e1-pro.md) for the full trace.

## Current status

Very early.

The initial roadmap:

* [x] Camera / RTSP input
* [ ] Rolling buffer
* [ ] Continuous CV pipeline
* [ ] Local Qwen video inference
* [ ] Triggered VLM review
* [ ] Periodic retrospective review
* [ ] Risk engine
* [ ] Notifications
* [ ] Frontier-model escalation
* [ ] Independent watchdog
* [ ] Canary tests
* [ ] Baby-monitor configuration
* [ ] IR / night-vision evaluation (see the dusk failure above)
* [ ] Measure false positives and false negatives
* [ ] Generalize the monitor interface

## Design principles

* **Local first** — keep most footage and inference local.
* **Models are fallible** — no single model is an oracle.
* **Cheap perception before expensive reasoning.**
* **Independent detection paths reduce common failure modes.**
* **Fail loudly** — monitoring failures should never look healthy.
* **Human escalation is the goal**, not autonomous judgment.

## Why I'm building this

The immediate motivation is practical: I want something watching the camera at 3 a.m. when I'm asleep.

The broader question is more interesting:

> **What does a reliable always-on AI perception system look like when we assume every individual model can fail?**

Vision models are becoming capable enough for continuous semantic understanding of the physical world. I want to explore the reliability architecture that makes that useful in practice.
