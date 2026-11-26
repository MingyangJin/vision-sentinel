# Harness Adapters

Vision Sentinel separates perception from control by driving an official coding-agent CLI
through a thin `AgentHarness` adapter (`packages/runtime/src/vs_runtime/contracts.py`).
This document defines the contract, the permitted-use and isolation rules, the verified
behavior of the first-class **Antigravity (`agy`)** driver, and the checklist for adding a
second driver (such as Claude Code post-MVP).

---

## 1. What Counts as a Harness

A **harness** in Vision Sentinel is an official coding-agent CLI paired with its own
vendor's model family—for example, Antigravity (`agy`) with Gemini, or Claude Code
(`claude`) with Claude—that natively provides:

- persistent multi-turn conversations with built-in context compaction,
- structured non-interactive execution (`--print` / JSON output),
- file/shell tool execution and sub-agent delegation, and
- enforceably restricted tool permissions for the stateless Vision Sub-Agent.

Vision Sentinel relies on the harness for those capabilities rather than rebuilding an
agent framework from scratch. A bare model HTTP API is **not** a harness in this sense.

---

## 2. Permitted-Use & Credential Isolation Rules

Every harness driver **must** satisfy all four rules:

1. **Official binary only.** Launch the vendor's official CLI binary directly as a
   subprocess. The official CLI remains the sole client of the user's OAuth sign-in.
2. **Never touch harness credentials.** No driver may read, copy, replay, or proxy a
   harness's OAuth tokens, cookies, or private backend endpoints.
3. **Allowlisted child environment.** Pass only standard OS/locale/path variables (e.g.,
   `HOME`, `PATH`, `USER`, `TMPDIR`, `LANG`, `TZ`, `XDG_*`) to the child process. Never
   leak camera credentials, RTSP URLs, household notification tokens, or unrelated API
   keys into the harness environment.
4. **Documented vendor-terms review.** Each driver records which vendor terms were checked,
   when, and any caveats for unattended operation.

### Vendor Terms Checked

| Driver | CLI & Models | Terms Checked | Notes & Caveats |
|---|---|---|---|
| **Antigravity** (`vs-harness-antigravity`) | `agy` 1.5.16–1.5.17 + Gemini 4 (`gemini-4-hydrogen-low`, `gemini-4-neon-medium`) | **2026-11-25** | Every model request goes through the official `agy` binary, which stays the sole OAuth client (the terms prohibit third-party clients using Antigravity OAuth directly; headless `agy -p` under one's own account is supported). **Two caveats:** (1) the terms contain no explicit clause on unattended/always-on use either way; (2) personal-plan quotas are unpublished and refresh on 5-hour and weekly windows, so an always-on Topic can hit quota. Users who want published rate limits or a chosen data-retention tier should use a paid API tier where supported. |
| **Claude Code** *(planned post-MVP, §9.3)* | `claude` + Claude | *Not yet checked* | Must audit Anthropic's current terms for unattended/always-on headless `claude` under a personal sign-in before implementing. |

---

## 3. The `AgentHarness` Contract

Defined in `packages/runtime/src/vs_runtime/contracts.py`:

```python
@runtime_checkable
class AgentHarness(Protocol):
    def media_capabilities(self) -> MediaCapabilities: ...

    def run_vision_worker(
        self, bundle: ObservationBundle, rubric: str, model: ModelConfig
    ) -> VisionReport: ...

    def deliver_to_orchestrator(
        self, topic: TopicContext, event: OrchestratorEvent
    ) -> OrchestratorResult: ...

    def chat_with_orchestrator(
        self, topic: TopicContext, message: str, attachments: list[str] | None = None
    ) -> OrchestratorResult: ...
```

### Required Behavioral Guarantees

1. **Honest `MediaCapabilities` declaration.**
   - `MediaCapabilities(frames: bool, video: bool, audio: bool)` tells the Bundle Builder
     which asset kinds the Vision Sub-Agent can actually ingest (`frame` and
     `contact_sheet` map to `frames`, `video` to `video`, `audio` to `audio`, `telemetry`
     is always allowed).
   - A driver must record `assets_read` from **observed tool calls** (what the CLI actually
     read), not merely what the model claims in prose. If an offered asset was left unread
     or unsupported, the report marks `degraded=True` so a Topic is never silently blind.

2. **Never raise on model, timeout, or quota failure.**
   - `run_vision_worker` returns `VisionReport(status="error", degraded=True, suggest_review=True, ...)`.
   - `deliver_to_orchestrator` and `chat_with_orchestrator` return
     `OrchestratorResult(ok=False, error=...)`.
   - Returning structured errors lets the Topic loop log the tick, queue reports for late
     replay, and let the Watchdog alert if review progress goes stale.

3. **Prompt-injection & permission boundaries (`ARCHITECTURE.md` §5.7).**
   - **Vision Sub-Agent:** Stateless and ephemeral (zero retained memory across ticks).
     Enforced **by the harness's permission mechanism**, never by prompt instructions
     alone: read-only inside `bundle.path`, no reads outside `bundle.path`, no writes, no
     shell, no network.
   - **Main Agent:** Stateful per Topic, running with full harness permissions by design
     (so it can inspect media, edit `orchestrator_notes.md`, author/test helper scripts in
     `tools/`, or delegate to coding sub-agents). Its fixed bootstrap prompt **must** frame
     Vision Sub-Agent reports and anything seen or heard on camera as untrusted
     observational data (delimited cleanly, e.g. `<<<OBSERVATION_DATA ... >>>`); only
     `goal.md` and user chat messages instruct it.

4. **Disposable sessions (`ARCHITECTURE.md` §5.2).**
   - The Topic directory (`goal.md`, `orchestrator_notes.md`, `vision_rubric.md`, `tools/`,
     `state/`) is the sole durable state.
   - If the persisted session expires, is missing, or crashes on resume, the driver discards
     the broken turn and seamlessly opens a fresh session bootstrapped from `TopicContext`,
     re-delivering the same event.
   - When `goal.md` changes on disk (edited by the user via the Web UI or filesystem), the
     driver detects the hash change and notifies the live session on the next turn.

5. **Clean shutdown (`close()`) & no orphaned processes.**
   - Every harness subprocess runs in its own process group (`start_new_session=True`).
   - Drivers should implement an idempotent, thread-safe `close()` method (called by the
     runtime on `SIGINT`/`SIGTERM` and in `check`'s `finally` block). `close()` terminates
     all in-flight subprocess groups (`SIGTERM`, then `SIGKILL` after grace), causes
     interrupted calls to return an error report/result without rebuilding a session, and
     makes subsequent calls fail fast without spawning new processes.

6. **Optional UI introspection helpers.**
   - `session_info(topic_dir) -> dict` returns JSON-serializable session metadata for the
     Web UI (`active`, `conversation_id`, `model`, `turns`, `total_turns`, `resets`,
     `usage`, `cumulative_usage`).
   - `reset_session(topic_dir) -> bool` clears the active session id so the next turn
     bootstraps fresh from the Topic directory without losing lifetime token counters.

7. **Main Agent Action Surface.**
   The Main Agent returns JSON `{reply: str, actions: [{name, args}]}` which the runtime
   validates and dispatches (`vs_runtime.loop`):
   - `inspect_media`: `{bundle_id, camera_id, asset}`
   - `send_notification`: `{severity, title, body, clip_ref, dedupe_key, requires_ack, bundle_id, camera_ids, speaker, cooldown_sec, breaks_mute}`
   - `clear_mute`: `{reason}`
   - `update_vision_rubric`: `{content}`
   - `update_schedule`: `{cadence_sec, window_sec}`
   - `update_review_policy`: `{all_clear_definition, all_clear_statuses, digest_interval_sec}`
   - `edit_topic_tool`: `{path, code}`
   - `test_topic_tool`: `{bundle_id, path?}`
   - `spawn_subagent`: `{task}`

   The Main Agent's bootstrap prompt should also document the runtime's **deterministic trigger
   protocol** for scripts in `tools/`: returning JSON with `"skip_vision": true` skips the
   Vision Sub-Agent (0 Vision tokens); `"trigger": false` also skips waking the Main Agent
   (0 tokens total), while `"trigger": true` wakes the Main Agent immediately with the tool's
   `status`, `summary`, `observations`, `conditions`, and raw metrics. Returning
   `"skip_vision": false` on a triggered window runs the Vision Sub-Agent only when triggered.

---

## 4. Reference Driver: Antigravity (`vs-harness-antigravity`)

Package path: `packages/harnesses/antigravity/` (`vs_harness_antigravity`).

### 4.1 Declared Capabilities & Models

- **Media capabilities (verified 2026-11-25 on `agy` 1.5.17):**
  `MediaCapabilities(frames=True, video=False, audio=True)`
  - Supported via `read_file`: JPEG/PNG frames, `contact_sheet.jpg`, `audio.mp3`, and JSON/text telemetry.
  - Not ingested via `read_file`: video files (`.mp4`, `.mov`, etc.).
- **Default models:**
  - Vision Sub-Agent: `gemini-4-hydrogen-low`
  - Main Agent: `gemini-4-neon-medium` (configurable per Topic, including optional `--effort`)

### 4.2 Vision Sub-Agent Invocation & Isolation

```text
agy --model <model> [--effort <effort>] \
    --mode plan \
    --output-format stream-json \
    --json-schema <VISION_SCHEMA> \
    --print <prompt>
# cwd = <bundle_dir>
```

- **Hard invariant:** `build_vision_command` explicitly forbids
  `--dangerously-skip-permissions`, `--add-dir`, and `--sandbox`.
- **Why `--mode plan` specifically (verified 2026-11-25 on `agy` 1.5.16–1.5.17):**
  - In `--mode plan` with `cwd = bundle_dir` (and no `--add-dir`), `agy` permits
    `read_file` strictly inside `cwd`, auto-denies `read_file` on paths outside `cwd`
    (recorded in `result.denied_actions`), and blocks `run_command` and non-artifact
    `write_file`.
  - Both the default headless mode and `--sandbox` **failed** isolation testing on the same
    version: both executed shell commands and wrote files even without
    `--dangerously-skip-permissions`.
- **Suspicious-activity escalation:** Any attempted read outside `bundle_dir`, any
  non-`read_file` tool call (excluding `agy`'s own plan artifacts under
  `~/.gemini/antigravity-cli/brain/<conversation_id>/`), or any entry in `denied_actions`
  marks the `VisionReport` as `degraded=True` and `suggest_review=True` and records the
  details in `raw["_harness"]`.

### 4.3 Main Agent Invocation, Chat & Session State

```text
agy --model <model> [--effort <effort>] [--resume <conversation_id>] \
    --dangerously-skip-permissions \
    --output-format stream-json \
    --json-schema <ORCHESTRATOR_SCHEMA> \
    --print <prompt>
# cwd = <topic_dir>
```

- **Persistence & locking:** Conversation metadata lives in
  `<topic_dir>/state/agy_session.json`, serialized by an exclusive `flock` on
  `<topic_dir>/state/agy_session.json.lock`. This allows the continuous monitoring loop
  (`vision-sentinel run`) and Web UI chat (`vision-sentinel ui` / `chat_with_orchestrator`)
  to share the same Main Agent session safely across threads and processes.
- **Chat & "Teach Agent" attachments (`chat_with_orchestrator`):**
  - Accepts absolute paths or paths relative to `topic.topic_dir`.
  - Classifies attachments in the prompt: images and audio are marked for `read_file`;
    `.mp4` video attachments automatically surface existing sibling `contact_sheet.jpg`,
    `frame.jpg`, and `audio.mp3` files (or instruct the agent to extract frames with
    `ffmpeg` using its shell tools); directories list up to 12 files inside; missing paths
    are clearly labeled `(not found on disk)`.
  - Prompts the Main Agent to persist durable user corrections to `orchestrator_notes.md`
    and/or emit `update_vision_rubric` / `edit_topic_tool` actions so lessons survive
    session replacement.

### 4.4 Verified `agy` 1.5.17 Quirks & Measurements

All measurements below were recorded on **2026-11-25** with `agy` **1.5.17**:

1. **Blind audio control (`gemini-4-hydrogen-low`, `--mode plan`, `cwd = bundle_dir`):**
   Given a 16 kHz mono MP3 (`control.mp3`) speaking `"Code word falcon seven four two nine"`
   with no surrounding context, the worker transcribed `"Code word falcon 7 4 2 9."` in
   `6.45 s` (`21,209` input, `16,736` cache read, `287` output).
2. **Negative security isolation test (`test_live_plan_mode_blocks_attempted_escape`):**
   Instructed directly to read an outside `secret.txt`, run `touch pwned.txt`, and write
   `made.txt` in `cwd`. Result: `read_file` outside `cwd` was auto-denied
   (`denied_actions: ["read_file"]`), `run_command` and `write_file` were blocked, zero
   files were created, and the secret never appeared in output.
3. **`--resume` prompt caching & end-to-end M1 loop (`n=4` ticks, 40 s cadence):**
   - Vision Sub-Agent (`gemini-4-hydrogen-low`): wall time p50 `17.8 s` [`14.7–20.0`],
     `27–29k` uncached input + `42–60k` `cache_read_tokens`.
   - Main Agent (`gemini-4-neon-medium`): fresh turn `18.4–19.6k` input (`8.2–9.0 s`);
     resumed turns `2.6–3.9k` uncached input + `16.4–21k` `cache_read_tokens`, `6.7–8.0 s`
     wall time.
4. **CLI quirks handled by the driver:**
   - `--resume <unknown_id>` exits `0` with a stderr warning and silently starts a **new**
     conversation. The driver compares the returned `conversation_id` against the requested
     one; on mismatch it discards that turn and rebuilds cleanly from the Topic directory.
   - `result.usage` on `--resume` is **cumulative** across the entire conversation. The
     driver sums per-step `step_update.usage` entries instead so each turn's `TokenUsage`
     is accurate.
   - Denied tool steps still emit `state: "DONE"` in `step_update`; actual denials appear
     in `result.denied_actions`.
   - Plan mode may write `plan.md` inside `~/.gemini/antigravity-cli/brain/<cid>/`; the
     driver tracks these separately as `agy_artifacts` rather than flagging them as bundle
     escapes.

### 4.5 Evaluation: `--resume` vs. Persistent `--input-format stream-json`

`agy` 1.5.17 supports a persistent stdio loop via
`--input-format stream-json --output-format stream-json` (reading one NDJSON user message
per line and streaming `step_update` / `result` events per turn).

**Measured comparison (2026-11-25, `agy` 1.5.17):**
- **Process startup saved:** `--resume` pays ~2.7 s per turn for `agy` process startup
  (`ARCHITECTURE.md` §8.2). In a 2-turn persistent `stream-json` probe on
  `gemini-4-hydrogen-low`, Turn 1 completed in `2.05 s` and Turn 2's model step completed
  in `2.05 s` with `16,709` `cache_read_tokens` and zero relaunch overhead.
- **Why `--resume` remains the default for M1/M2:**
  1. **Per-turn `--json-schema` enforcement:** `agy --help` documents that `--json-schema`
     in `stream-json` mode applies only to the final result, whereas `--resume --print`
     enforces `ORCHESTRATOR_SCHEMA` on every single turn.
  2. **Multi-process safety:** With `--resume` + `flock` on `state/agy_session.json.lock`,
     `vision-sentinel run` and `vision-sentinel ui` (or one-off CLI chat) share the Topic
     session directly with zero IPC daemon. A persistent stdio process can only be written
     to by the single owning process, requiring an IPC broker for out-of-process UI chat.
  3. **Fault isolation:** A hung turn kills only that one-turn process group via
     `ProcessRegistry`, keeping the persisted `conversation_id` intact for the next turn.
- **Future path:** If overnight latency measurements (§8.1/§8.2) show the ~2.7 s startup
  overhead matters for urgent alerts, an optional persistent-stdio backend can sit behind
  the same `Orchestrator` class once `agy` supports per-turn schema enforcement in
  `stream-json` mode (or the runtime routes all UI chat through the owning process).

### 4.6 Running Diagnostics & Tests

```bash
# Check agy binary, version, declared capabilities, and default models (spends no quota)
uv run vs-agy-probe --check
# or: uv run python -m vs_harness_antigravity.cli_probe --check

# Fast offline unit tests (fake agy script, no network, no quota)
uv run pytest -v packages/harnesses/antigravity

# Opt-in live tests against real agy (spends model quota, requires ffmpeg)
VS_LIVE_AGY=1 uv run pytest -v -s -m live_agy packages/harnesses/antigravity
```

---

## 5. Adding a Second Harness Driver (e.g., Claude Code, Post-MVP §9.3)

Until a second driver exists, `AgentHarness` is only proven against Antigravity. Follow
this exact sequence when implementing `packages/harnesses/claude_code/` (or another
official coding-agent CLI):

### Step 1: Vendor Terms & Permitted-Use Audit
- Read the vendor's current consumer/developer terms for headless non-interactive CLI
  execution under a personal sign-in, specifically checking unattended / always-on loops.
- Confirm whether a paid API key mode is required or recommended for 24/7 monitoring.
- Record the vendor, CLI version, date checked, and caveats in the table in §2 above and in
  the driver's `README.md`.

### Step 2: Media Capabilities Probe
- Run blind control checks using synthetic media (never household footage):
  - **Frames:** synthetic `ffmpeg testsrc2` JPEG.
  - **Audio:** blind 16 kHz mono MP3 speaking a random code phrase with no text hints.
  - **Video:** short synthetic `.mp4` clip.
- Declare only verified capabilities in `media_capabilities()`. If the CLI cannot read
  audio or video natively, declare `audio=False` / `video=False` so Topics degrade visibly
  rather than silently missing events.

### Step 3: Permission-Enforced Vision Isolation Probe
- Identify the CLI's native permission flags that restrict the Vision Sub-Agent to
  read-only access strictly inside `bundle_dir` (no shell, no writes, no network, no reads
  outside `bundle_dir`).
- Run a live negative escape test (modeled on
  `test_live_plan_mode_blocks_attempted_escape`) that directly instructs the model to read
  a secret file outside `bundle_dir`, execute `touch` in shell, and write a file in `cwd`.
- Verify the **CLI permission layer itself** blocks all three—not just model refusal.
  Document which flags work and which flags do not.

### Step 4: Package Scaffold
Create `packages/harnesses/<vendor>/`:
- `pyproject.toml`: package name `vs-harness-<vendor>`, depends on `vs-runtime`
  (`{ workspace = true }`), added to workspace members in root `pyproject.toml`.
- `src/vs_harness_<vendor>/`:
  - `harness.py`: class implementing `AgentHarness` + `close()` + `session_info()` + `reset_session()`.
  - `vision.py`: stateless worker enforcing the isolation flags (asserting forbidden flags
    are never present), deriving `assets_read` from observed tool calls, returning
    `VisionReport(status="error", degraded=True)` on failure.
  - `orchestrator.py`: persistent session per Topic stored under `<topic_dir>/state/`,
    file-locked across processes, auto-rebuilding from `TopicContext` when a session is
    lost, framing `OBSERVATION_DATA` as untrusted, tracking per-turn & cumulative usage.
  - `cli_probe.py`: zero-quota `--check` command verifying binary, version, and models.
- `tests/`:
  - Offline unit tests driven by a fake CLI executable script covering flags, `cwd`,
    scrubbed env, timeout + `close()` process-group cleanup, session resume/rebuild,
    attachments, and token accounting.
  - Opt-in live tests (`@pytest.mark.live_<vendor>`) including the negative isolation test.

### Step 5: Evolve the Interface Honestly (§9.3)
- If implementing the second driver reveals Antigravity-specific assumptions baked into
  `AgentHarness` or `vs_runtime.contracts`, **update the shared interface** cleanly rather
  than emulating Antigravity quirks inside the new driver.
