# Contributing to Vision Sentinel

Thanks for helping. This page covers setup, the checks to run before a pull request, and the
few rules that are not negotiable.

## Development setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and FFmpeg. The Web UI needs Node.js
only if you change the frontend; the clients need Xcode (Swift) or the Android SDK with JDK 17.

```bash
uv sync                # install every workspace package into .venv
uv run pytest -q       # offline tests: no camera, no model calls, no accounts
```

## Checks before a pull request

Run the ones that cover what you touched:

| Area | Command |
|---|---|
| Python packages (`packages/*`) | `uv run pytest -q` |
| Web UI frontend (`packages/ui/frontend`) | `npm ci && npx tsc --noEmit && npm run build` |
| macOS camera client (`clients/macos-camera`) | `swift test` |
| iOS sample app core (`mobile/ios`) | `swift test` |
| Android sample app (`mobile/android`) | `./gradlew testDebugUnitTest assembleDebug --no-daemon` |
| Example configs | `uv run vision-sentinel check --config examples/vision-sentinel.toml --check` |

The built Web UI in `packages/ui/src/vs_ui/static/` is committed on purpose, so the Python
package serves the UI without Node. If you change the frontend, run `npm run build` and commit
the regenerated `static/` files with your change. Builds have sourcemaps off; keep it that way.

Tests that call a real model are skipped unless you set `VS_LIVE_AGY=1`.

## Rules that are not negotiable

- **No personal data, ever.** No real camera footage, recordings, photos, household details,
  real names, personal email addresses, private IP addresses, device serials, or local paths
  such as `/Users/<name>/...`. Test media must be synthetic (see
  `examples/sample-media/README.md`). Use documentation addresses (`192.0.2.x`, `198.51.100.x`,
  `203.0.113.x`, `example.com`) in tests and docs.
- **No secrets.** No API keys, tokens, `.env` files, `.p8`/`.p12`/`.pem` keys,
  `google-services.json`, or service-account JSON. Fake test values must look fake
  (`test-token`).
- **English only** in code, comments, UI strings, docs, and test fixtures.
- **Core packages stay task-agnostic.** A specific use case ships as an example Topic under
  `examples/topics/`, not as logic in `packages/`.
- **Keep the documented invariants.** Changes must not weaken the security boundary in
  [SECURITY.md](SECURITY.md) and [docs/ARCHITECTURE.md §5.7](docs/ARCHITECTURE.md): camera
  credentials and addresses stay inside `packages/streamhub`, the Vision Sub-Agent keeps its
  harness sandbox, device routes keep their token check, and speaker output stays opt-in. If a
  change touches one of these, add or extend a test that proves it still holds.

## Adding things

- A camera type: [docs/CAMERA_ADAPTERS.md](docs/CAMERA_ADAPTERS.md).
- A harness driver: [docs/HARNESS_ADAPTERS.md](docs/HARNESS_ADAPTERS.md). Record the vendor
  terms you checked and the date.
- A notification sink: `packages/notify/src/vs_notify/sinks/`.
- An example Topic: copy one under `examples/topics/` and list it in
  `examples/topics/README.md`.

## Pull requests

Keep a pull request to one change, say what you tested, and update the docs that describe the
behavior you changed. Report security problems privately as described in
[SECURITY.md](SECURITY.md), not in a pull request.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
