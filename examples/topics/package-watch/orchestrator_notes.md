# Orchestrator notes

Durable rules, user preferences, and calibration facts for Package Watch. Reloaded at the
start of every Main Agent session.

- Deduplicate repeated alerts for the same resting parcel with `dedupe_key = "package-present"`
  so a sitting box triggers one delivery alert rather than one per minute.
- Only emit a second alert if the parcel count changes or the parcel is removed.
