# Orchestrator notes

Durable rules, user preferences, and calibration facts for Door Left Open. Reloaded at the
start of every Main Agent session.

- Rely on `counters.conditions["door_open"].duration_s` supplied by the runtime.
- Alert once `duration_s >= 300` (5 minutes) with `dedupe_key = "door-left-open"`.
- If a person is actively working right next to the open door, you may hold off and re-check
  on subsequent ticks unless the user asks for stricter alerting.
