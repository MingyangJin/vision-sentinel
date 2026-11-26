# Security Policy

Vision Sentinel handles live camera video and audio, so please treat security reports with care.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub's **Report a vulnerability** button on this
repository's **Security** tab (private vulnerability reporting). Please do not open a public
issue for a vulnerability, and never attach real camera footage, credentials, or tokens to a
report; use the synthetic clip in `examples/sample-media/` to reproduce.

A useful report says which component is affected (`packages/streamhub`, `packages/notify`,
`packages/ui`, a harness driver, or a client app), the version or commit, the steps to
reproduce, and what an attacker gains.

This is a volunteer-maintained project. Reports are acknowledged on a best-effort basis; there
is no bounty program and no guaranteed response time.

## Supported versions

Only the latest release (currently `v0.1`) receives security fixes.

## Security boundary

The bar for `v0.1` is stated in [docs/ARCHITECTURE.md §5.7](docs/ARCHITECTURE.md): *not
hackable, but not production-deployable*. It is built for one trusted household on its own
network, not for hostile networks, multi-user access, or hosted operation.

What the project is designed to hold:

- **Camera credentials and addresses stay inside the Stream Hub ("The One Rule").** Topics,
  agents, helper tools, notification sinks, and the Web UI see only a logical `camera_id`.
  Credentials are named by `credential_ref` and resolved from the environment, the macOS
  keychain, or a file at launch; they are scrubbed from logs, errors, and API responses.
- **The Vision Sub-Agent is sandboxed by the harness, not by a prompt.** It runs as
  `agy --mode plan` with its working directory set to one Observation Bundle: no shell, no
  writes, no reads outside the bundle.
- **Camera content is observation, never instruction.** Text in frame and speech in audio are
  reported as data to both agents.
- **Device calls need the household token.** Every mobile-app route on the Notification Service
  requires `Authorization: Bearer <token>`, compared in constant time. `POST /v1/alerts` is
  server-internal: it accepts only a direct loopback connection with a loopback `Host` header and
  no forwarding headers.
- **File routes are confined.** Clip, thumbnail, bundle, and segment routes reject path
  traversal and symlink escape.
- **Push ingest needs a token off loopback.** The HTTP segment receiver refuses to start on a
  non-loopback bind without a Bearer token.
- **Camera speaker output is off by default.** It needs an opt-in on both the camera and the
  Topic, and volume is clamped to the camera's `volume_cap`.

Known limits, by design in `v0.1`:

- **The Web UI has no login.** It binds to `127.0.0.1` by default, checks the `Host` header on a
  loopback bind, and warns when bound elsewhere. Anyone who can reach it can view the cameras
  and chat with the Main Agent. Never expose it to the public internet; keep it on a trusted LAN
  or a private tunnel.
- **The Main Agent runs with full harness permissions.** It can do anything the user account
  running it can do, including reading files that account can read. The rule that camera content
  is never an instruction is what stands between a camera feed and those permissions, and
  `goal.md` is read-only to the runtime's agent actions rather than to the agent's own shell.
- **One shared household token.** There are no user accounts, per-device revocable tokens, or
  token rotation.
- **Topic helper tools are not sandboxed.** They run as the same user with a scrubbed
  environment and a hard timeout.
- **Cloud models see your media.** With the default harness, observation frames and audio are
  sent to the model provider under your own account.

Issues that fall inside these stated limits are welcome as hardening proposals in the public
issue tracker; they are not treated as vulnerabilities.

## Not a safety device

Vision Sentinel is a best-effort alerting project. It is not a medical device or a certified
life-safety alarm, and it must not be the only safeguard for a person, animal, or property.
