# Replay / Triage Next Slice Audit

Date: 2026-04-09  
Author: yu

Scope reviewed:
- `main.py`
- `logging_system/core.py`
- `ws_server/server.py`
- `web-console-v2/src/components/TaskPanel.vue`
- `web-console-v2/src/components/DiagPanel.vue`
- persisted runtime sessions under `Logs/runtime/`
- prior audits:
  - `docs/yu/logging_diagnostics_iteration_audit_20260409.md`
  - `docs/yu/logging_diagnostics_next_slice_20260409.md`

## Short conclusion

The previous “structured task triage snapshot” recommendation has already landed:

- `RuntimeBridge._build_task_triage()` exists in `main.py`
- `TaskPanel.vue` now uses `task.triage.status_line` first
- `DiagPanel.vue` already shows a triage summary block above the raw trace

So the next iteration-UX bottleneck is no longer “what is the task doing right now?”.

The next bottleneck is:

> once the session ends, hangs, or restarts, the useful evidence is on disk, but the debugging UI cannot replay it; the developer drops back to scrolling raw `tasks/*.jsonl`.

The smallest next slice that would materially improve daily E2E debugging is therefore:

> add **disk-backed persisted task replay** for the current/latest session, wired into `DiagPanel`, using the existing per-task JSONL logs.

This is smaller and more useful than a full session browser, and more urgent than adding another layer of triage semantics.

---

## 1. What is already solved

### 1.1 Live task triage is already in place

`main.py::_task_to_dict()` now includes:

- `triage.state`
- `triage.phase`
- `triage.status_line`
- `triage.waiting_reason`
- `triage.blocking_reason`
- `triage.active_expert`
- `triage.active_job_id`
- `triage.reservation_ids`
- `triage.world_stale`
- `triage.active_group_size`

And the frontend is already consuming it:

- `TaskPanel.vue` prefers `task.triage.status_line`
- `DiagPanel.vue` shows the triage summary for the selected task

That means the current live-session question:

> “What is this task doing now?”

is substantially better answered than before.

### 1.2 Durable task logs already exist

Each runtime session already persists:

- `Logs/runtime/session-<timestamp>/all.jsonl`
- `Logs/runtime/session-<timestamp>/session.json`
- `Logs/runtime/session-<timestamp>/tasks/<task_id>.jsonl`
- `Logs/runtime/session-<timestamp>/components/*.jsonl`

`main.py::_task_to_dict()` also already exposes:

- `task.log_path`

So the raw material for replay is already present and sharded by task.

Concrete evidence from the latest persisted session:

- `Logs/runtime/session-20260405T231502Z/all.jsonl` has `38,768` rows
- `Logs/runtime/session-20260405T231502Z/tasks/` has `33` task files

This is exactly why task-scoped replay is the right next cut: the log volume is already high enough that “just open `all.jsonl`” is the wrong iteration loop.

---

## 2. Root cause of the remaining debugging pain

The remaining pain is not missing logs or missing live triage.

The root cause is a **split debugging model**:

- live state is visible in UI
- historical evidence is only really usable in the shell/editor

In other words:

- when the task is still active, `TaskPanel` and `DiagPanel` are useful
- when the backend hangs, restarts, or the session is over, the developer is pushed back to:
  - copy `task.log_path`
  - open `tasks/<task_id>.jsonl`
  - mentally reconstruct the task again from raw rows

This happens because:

1. `logging_system.replay()` is still memory-only  
   It replays the in-memory store, not persisted sessions on disk.

2. `DiagPanel` only displays live/in-memory trace entries  
   It shows `task.log_path` as text, but cannot load that file.

3. `sync_request` only restores the current process state  
   It does not rehydrate old task traces from persisted task files.

So the system already has the right artifacts, but not the last-mile UI/runtime hook.

---

## 3. Why not choose another slice first

### Not another triage refinement

The current triage block is already good enough to answer the live “what is happening now?” question.

Adding more fields such as:

- repeated failure signature
- last decisive transition
- summarized blocker history

would help, but it would still leave the core pain unresolved:

> after the process dies or the session is over, the UI still cannot replay the task from disk.

### Not a full session browser yet

A full historical session browser is worthwhile, but it is a larger feature:

- session listing
- session selection
- cross-session loading
- possibly diffs/search/filtering

That is not the smallest material iteration win.

### Not LLM-generated debug summaries

That is too expensive and too early.
The next slice should stay deterministic and reuse existing structured logs.

---

## 4. Smallest next slice

## Slice name

**Persisted task replay for current/latest session**

## Goal

Make the default debugging loop:

1. select a task in `DiagPanel`
2. see current triage if the task is still live
3. click/load persisted replay
4. inspect the normalized task timeline from disk
5. only open raw JSONL if the normalized replay is insufficient

## What it should do

For a selected task, the UI should be able to request:

- the persisted task log from disk
- for either:
  - the current live session, or
  - the `latest.txt` session if no live session exists

The backend should:

- read `tasks/<task_id>.jsonl`
- normalize records into the same trace-entry shape already used by `DiagPanel`
- return them as a replay payload

This keeps the mental model simple:

- same task selector
- same trace view
- same triage summary
- but now backed by durable task logs when live memory is gone

---

## 5. Exact recommended implementation shape

### 5.1 Backend transport

Add one inbound message:

```json
{
  "type": "task_replay_request",
  "task_id": "t_xxx",
  "session": "current_or_latest"
}
```

Add one outbound message:

```json
{
  "type": "task_replay",
  "data": {
    "task_id": "t_xxx",
    "session_name": "session-20260405T231502Z",
    "task_log_path": "Logs/runtime/.../tasks/t_xxx.jsonl",
    "entries": [...]
  }
}
```

No new protocol family is needed beyond this one request/reply pair.

### 5.2 Backend implementation targets

#### `logging_system/core.py`

Add deterministic file-backed helpers, for example:

- `latest_session_dir()`
- `load_session_metadata(session_dir)`
- `load_task_records(task_id, session_dir=None)`

These should reuse the existing JSONL format directly.

#### `main.py`

Add a handler such as:

- `RuntimeBridge.on_task_replay_request(...)`

Responsibilities:

- resolve the session directory:
  - live current session if present
  - else `Logs/runtime/latest.txt`
- load the task file
- normalize records into the trace-entry schema already used by `DiagPanel`
- send the payload back through `ws_server`

The normalization code should be shared with existing live trace formatting as much as possible.

#### `ws_server/server.py`

Extend the message protocol with:

- inbound: `task_replay_request`
- outbound: `task_replay`

No throttle changes are needed.

### 5.3 Frontend implementation targets

#### `web-console-v2/src/components/DiagPanel.vue`

Add a small control near the selected task:

- `加载持久化回放`

Desired behavior:

- if a task is selected, request replay for that task
- show the replay trace in the same trace stream
- keep the current triage summary above it
- display the source session name and task log path

Important:

- do not build a new page
- do not build a full session browser yet
- reuse the existing task selector and trace UI

### 5.4 Tests

Primary target:

- `tests/test_ws_and_review.py`

Add contract tests for:

- `task_replay_request`
- fallback to `latest.txt` session
- replay payload shape
- normalized entries arriving at the client

Optional backend helper tests:

- `tests/test_logging_system.py`

Only if the file-loading helpers are non-trivial.

---

## 6. Why this slice is the best next tradeoff

This slice is the best next tradeoff because it:

- reuses existing persisted task files
- reuses existing task selector UI
- reuses existing trace rendering
- requires no runtime semantics redesign
- immediately improves postmortem and next-day debugging

And most importantly, it changes the daily workflow from:

> open terminal → find log path → open raw JSONL → scroll

to:

> select task in Diagnostics → load persisted replay → inspect normalized timeline

That is a real, material iteration win.

---

## 7. What should remain deferred

These are all useful, but should come **after** persisted task replay:

- full historical session browser
- cross-session diff
- search/filter across persisted sessions
- richer triage fields such as repeated failure signatures
- LLM-generated debug summaries
- offline benchmark/report dashboards

Those are second-order improvements.

The first-order improvement is to stop making the developer leave the debugging UI just to inspect a finished or crashed task.

---

## 8. Final recommendation

The next minimal iteration-UX / replay / triage slice should be:

> **Persisted task replay in Diagnostics, backed by existing per-task JSONL logs for the current/latest session.**

This is the smallest change that materially reduces dependence on scrolling raw logs, while fitting cleanly on top of the triage work that has already landed.
