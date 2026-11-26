# `vs-ui` — Vision Sentinel Unified Web UI

Single local web application combining live multi-camera observation, rolling clip playback, Topic management, Vision Sub-Agent inspection, Main Agent chat, and the Alert Inbox (`docs/ARCHITECTURE.md` §5.5, §5.7).

## Running

The React/Tailwind frontend is prebuilt into `src/vs_ui/static/`, so **no Node runtime is needed** to run the Web UI:

```bash
uv run vision-sentinel ui --config examples/vision-sentinel.toml
# or directly:
uv run vs-ui --config examples/vision-sentinel.toml --host 127.0.0.1 --port 8080 --start-hub
```

`vision-sentinel ui` starts camera ingest and the Topic loops next to the UI. `vs-ui` serves the UI
only: pass `--start-hub` to also ingest cameras, and note that it never runs Topic loops.

> **Security (§5.7):** The MVP Web UI has **no login** and binds `127.0.0.1` by default. Binding to `0.0.0.0` or any non-loopback address logs a prominent security warning and displays a banner in the UI. Never expose the Web UI port to the public internet; keep it on your trusted LAN or private tunnel (e.g., Tailscale). On a loopback bind the server also rejects requests whose `Host` header is not a loopback name, which blocks DNS-rebinding pages; if you front it with a private tunnel, name that host with `--allow-host <name>`.

## Frontend Development

Built on **Vite + React + TypeScript + Tailwind CSS + Radix UI primitives + Lucide icons**:

```bash
cd packages/ui/frontend
npm install
npm run dev      # Vite dev server with /api proxy to 127.0.0.1:8080
npm run build    # Type-checks (tsc --noEmit) and builds into ../src/vs_ui/static/
```

## API Surface

- `GET /api/health` — system & bind-host status
- `GET /api/presets` — quick-select Topic presets
- `GET /api/cameras`, `GET /api/cameras/{id}/snapshot.jpg`, `GET /api/cameras/{id}/live.mp4` (native-FPS fragmented MP4 for MSE), `GET /api/cameras/{id}/stream` (MJPEG), `GET /api/cameras/{id}/segments`, `GET /api/cameras/{id}/segments/{name}`, `GET /api/cameras/{id}/clip`
- `GET /api/topics`, `POST /api/topics`, `GET /api/topics/{id}`, `PATCH /api/topics/{id}` (mid-flow config, pause/resume, speaker opt-in, human-owned `goal.md`), `POST /api/topics/{id}/mute` (timed or until-next-event mute), `GET /api/topics/{id}/ticks`, `GET /api/topics/{id}/bundles/{bundle_id}/{asset}` (path-confined), `POST /api/topics/{id}/chat`
- `GET /api/alerts`, `GET /api/alerts/{id}`, `POST /api/alerts/{id}/ack`, `POST /api/alerts/{id}/feedback`, `GET /api/alerts/{id}/thumbnail`, `GET /api/alerts/{id}/clip`

## Testing

```bash
uv run pytest -v packages/ui
```
