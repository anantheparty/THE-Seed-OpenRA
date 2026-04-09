## Current
Continue the future-unit allocator path from the audit result, prioritizing ownership-preserving fixes over larger refactors.

## Queue
- Reassess whether `occupy_target` or a posture wrapper (`hold_units` / `regroup_units`) should be the next action-surface slice.
- If iteration UX becomes the active slice, upgrade `task_replay_request` into a deterministic task debug bundle rendered above the raw trace in `DiagPanel`.
- If allocator ownership work stalls, land the next low-conflict Adjutant slice: expose reservation start-release / reinforcement state in battlefield/coordinator replies consistently.

## Blocked (optional)
