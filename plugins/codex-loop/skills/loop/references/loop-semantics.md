# Codex Loop Semantics

- Fixed interval: first token is a duration like `5m`, `1h`, or `30s`. Seconds are rounded up to the 60 second minimum.
- Dynamic interval: no duration token. The model chooses the next delay after each iteration, constrained to 60..3600 seconds.
- No catch-up: if the daemon is offline for several periods, only one due run is acquired when it comes back.
- Expiry: tasks expire seven days after creation.
- Per-thread cap: 50 active/running/paused tasks.
- Cancellation: a running task receives `cancel_requested`; it is not interrupted mid-turn by the store.
- Safety: later iterations reuse the task's original cwd/approval/sandbox/model snapshots and do not auto-upgrade permissions.
