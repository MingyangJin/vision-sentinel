# vs-runtime

The Topic engine of Vision Sentinel (ARCHITECTURE.md §5.2, §5.3, §5.6) and the contracts
every other package imports.

| Module | What it does |
|---|---|
| `contracts.py` | Pydantic models and Protocols shared across packages (cameras, bundles, reports, harness, alerts) |
| `topic.py` | Topic directory loader/initializer (`paused`, `speaker_enabled` default false); agent write paths strictly forbid `goal.md`; `write_goal_as_user()` is human-only |
| `counters.py` | `state/ticks.jsonl` (tick, review, alert, action records) and the deterministic counters attached to every report: status/condition streaks and durations, seconds since the last alert per dedupe key, newest reviewed tick |
| `bundle.py` | Bundle Builder: slices synchronized wall-clock windows across M cameras in parallel from the Stream Hub into `state/bundles/<id>/<camera_id>/`, runs `tools/*` (cwd = bundle, scrubbed env, 30 s timeout per tool, whole process group killed), writes `bundle.json` + `tool_outputs.json`, then makes the files read-only |
| `loop.py` | `TopicLoop`: Vision Sub-Agent then Main Agent review every tick; optional Main-Agent-set quiet policy (digest of consecutive all-clear reports); late replay of reports the Main Agent missed, from `ticks.jsonl`; pause/resume; double-opt-in speaker alert gating; dispatcher for the Main Agent's actions |
| `manager.py` | `TopicManager` & `TOPIC_PRESETS`: thread-safe Topic CRUD, presets, human-only `goal.md` updates, path-confined bundle asset resolution, and Main Agent chat for Web UI & CLI |
| `watchdog.py` | Camera drops/recoveries go to the Main Agent; pauses cleanly when a Topic is paused; direct system-health alert only when an active Topic's newest reviewed tick is older than `review_stale_sec + cadence + digest interval` |
| `cli.py` | `vision-sentinel init-topic / check / run / ui / watchdog / chat / devices` (`--check` prints a redacted plan; `run` runs N Topics concurrently sharing one StreamHub and NotifyService) |
| `fakes.py` | `FakeHarness`, `FakeHub`, `FakeNotifier` for tests and `--harness fake` |

```bash
uv run pytest -q packages/runtime
uv run vision-sentinel check --config examples/vision-sentinel.toml --harness fake
```

`run` includes a watchdog thread, which tells the Main Agent about camera drops. Run
`vision-sentinel watchdog --config …` as its own process (launchd, systemd) as well: it
reads `ticks.jsonl` and segment ages from disk, so it still sends the system-health alert
when `run` itself has crashed or hung. Both use the same dedupe key, so with the same
`[notify]` db (or url) a stall is one alert.

On SIGINT/SIGTERM, `run` calls the harness's `close()` (if it has one) to kill in-flight
agent processes, then exits; a report produced during shutdown stays unreviewed and is
replayed late on the next start.

Helper tool contract: an executable file or a `.py`/`.sh` script directly in `tools/`,
run with cwd = the bundle directory and only `PATH`, `HOME`, locale, `TMPDIR` and
`VS_TOPIC_ID`, `VS_BUNDLE_ID`, `VS_BUNDLE_DIR`, `VS_CAMERA_IDS`, `VS_WINDOW_START`,
`VS_WINDOW_END` in its environment. It prints one JSON value to stdout. A timeout or a
non-zero exit marks the tick degraded; the tick still runs.

Deterministic trigger mode: if a tool's JSON stdout is an object with `"skip_vision": true`,
`TopicLoop` skips the Vision Sub-Agent (0 Vision tokens) and synthesizes a `VisionReport`
directly from the tool output (`status`, `summary`, `observations`, `conditions`, `trigger`,
`wake_main`). When `trigger` and `wake_main` are false and the bundle is not degraded, the
Main Agent is also skipped and a zero-token `tool_bypass` review is logged so Watchdog
liveness stays healthy. When `trigger` is true (or the bundle is degraded), the Main Agent
is woken immediately to judge and decide whether to alert. Returning `"skip_vision": false`
on a triggered tick lets the Vision Sub-Agent inspect only triggered windows.
