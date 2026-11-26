# Orchestrator notes

Durable rules for the Multi-Camera Demo Topic. Reloaded at the start of every Main Agent
session.

- Both `cam_north` and `cam_south` replay the synthetic 10 s demo clip, so counter resets
  every 10 s are normal.
- Correlate `camera_status` across cameras before alerting so a single brief hiccup is not
  mistaken for a full outage.
