# Orchestrator notes

- Emit an `info` notification via `send_notification` whenever `person_waving`, `person_entered_or_left`, or `cue_phrase_heard` occurs.
- Respect any active timed or `until_next_event` mute set by the user.
