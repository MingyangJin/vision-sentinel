# vs-notify — Notification Service

Self-hosted alert API for Vision Sentinel (ARCHITECTURE.md §5.4, §5.7): dedupe/cooldown, fan-out
to sinks, acknowledgment, thumbs up/down feedback logged to the Topic directory, and
authenticated clip/thumbnail serving for mobile clients and the Web UI.

See [`docs/NOTIFICATIONS_SETUP.md`](../../docs/NOTIFICATIONS_SETUP.md) for setup instructions,
APNs/FCM human checklists, and remote reachability options.

## API

| Route | Who may call | Purpose |
|---|---|---|
| `POST /v1/alerts` | loopback only (runtime, watchdog); forwarded/proxied requests refused | create an `AlertRequest`; returns `AlertRecord` (`sent`, `deduped`, `failed`) |
| `GET /v1/alerts` | household token | list alert views (`has_clip`, `has_thumbnail`, `acked_at`, `feedback`); filters `topic_id`, `severity`, `unacked`, `since`, `limit` |
| `GET /v1/alerts/{id}` | household token | one alert view |
| `GET /v1/alerts/{id}/thumbnail` | household token | JPEG/PNG thumbnail, strictly path-confined to saved/bundle dir |
| `GET /v1/alerts/{id}/clip` | household token | MP4 clip (supports Range requests), strictly path-confined |
| `POST /v1/alerts/{id}/ack` | household token | "seen"; the first ack is kept |
| `POST /v1/alerts/{id}/feedback` | household token | `{"rating": "up"\|"down", "note", "by"}`; "was this right" |
| `POST /v1/devices/register` | household token | `{"platform": "ios"\|"android", "token", "name"}` for APNs/FCM push |
| `GET /healthz` | anyone | liveness |

Device calls carry `Authorization: Bearer <household token>`. With no token configured,
every device call is refused.

## Sinks

Built-in sink types registered in `vs_notify.sinks.SINK_TYPES`:

- `webhook`: JSON POST, headers with `env:NAME` secrets, timeout.
- `desktop`: macOS `osascript`, Linux `notify-send`, otherwise stdout; `dry_run` never spawns; `sound` off by default.
- `apns`: direct outbound HTTP/2 APNs push using `.p8` ES256 JWT, sandbox/prod, `time-sensitive`/`critical` interruption levels, category `VISION_ALERT`, dead-token pruning.
- `fcm`: direct outbound HTTPS FCM HTTP v1 push using GCP service-account OAuth2 JWT, high-priority data-only payloads with `sentinel_urgent`/`sentinel_default` channel ids, dead-token pruning.
- `camera_speaker`: optional opt-in one-way alert chime routed via an injected `StreamHub.play_alert(camera_id, audio_path, volume)` callback (double opt-in + volume clamping).

Each sink may set `min_severity`. Sinks run concurrently; a failing sink is recorded in
`deliveries` and never blocks the others.
