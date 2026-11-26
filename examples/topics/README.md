# Example Topics

A Topic is a self-contained directory (see [ARCHITECTURE.md](../../docs/ARCHITECTURE.md) §5.2).
Copy any template to start your own:

```bash
uv run vision-sentinel init-topic topics/my-topic --from examples/topics/package-watch \
  --camera front_porch --goal "Notify me when a parcel is delivered at the front door."
```

## Included Templates

| Template | Cameras | What it demonstrates |
|---|---|---|
| `demo-replay/` | `["demo"]` | Zero-hardware smoke test on `examples/sample-media/demo.mp4` with deterministic `tools/inventory.py`. |
| `desk-watch/` | `["mac_cam"]` | Built-in Mac camera + microphone via the macOS Camera Client (`examples/mac-camera.toml`): wave or cue-phrase alerts. |
| `package-watch/` | `["front_porch"]` | Entryway parcel delivery & removal monitoring with deduplication notes. |
| `door-left-open/` | `["garage"]` | Duration-based alerting using runtime `counters.conditions["door_open"].duration_s`. |
| `multi-cam-demo/` | `["cam_north", "cam_south"]` | Synchronized **M cameras → 1 Topic** observation with `tools/sync_summary.py`. |

## Quick Runs (Zero Hardware)

Single camera + single Topic:

```bash
uv run vision-sentinel check --config examples/vision-sentinel.toml --check         # redacted plan
uv run vision-sentinel check --config examples/vision-sentinel.toml --harness fake  # no model calls
uv run vision-sentinel check --config examples/vision-sentinel.toml                 # 1 live cycle on agy
uv run vision-sentinel run   --config examples/vision-sentinel.toml                 # continuous loop
uv run vision-sentinel ui    --config examples/vision-sentinel.toml                 # Unified Web UI
uv run vision-sentinel watchdog --config examples/vision-sentinel.toml              # standalone watchdog
```

Multi-camera & multi-Topic (M:N):

```bash
uv run vision-sentinel check --config examples/multi-cam-demo.toml --check
uv run vision-sentinel check --config examples/multi-cam-demo.toml --harness fake
```

## Topic Directory Layout

| File | Written by | Purpose |
|---|---|---|
| `topic.toml` | User; Main Agent via `update_schedule` / `update_review_policy` | Cameras, cadence, window, models, `paused`, `speaker_enabled`, review policy |
| `goal.md` | **User only** (Web UI or direct edit) | The goal in your own words; no agent tool writes it |
| `vision_rubric.md` | Main Agent via `update_vision_rubric` | Instructions & condition labels for the ephemeral Vision Sub-Agent |
| `orchestrator_notes.md` | Main Agent | Durable rules & calibration facts that survive session restarts/compaction |
| `tools/` | Main Agent via `edit_topic_tool` | Deterministic helpers run on every bundle (30 s timeout, scrubbed env, JSON stdout) |
| `state/` | Runtime | `ticks.jsonl`, `bundles/`, `saved/`, `feedback.jsonl`, `health.json` (gitignored) |
