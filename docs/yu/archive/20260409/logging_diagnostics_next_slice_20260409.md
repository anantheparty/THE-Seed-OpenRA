# Logging / Diagnostics / Replay / Triage UX

Date: 2026-04-09

Scope reviewed:
- `logging_system/core.py`
- `ws_server/server.py`
- `main.py`
- `web-console-v2/src/components/DiagPanel.vue`
- `web-console-v2/src/components/TaskPanel.vue`
- `web-console-v2/src/components/ChatView.vue`
- `web-console-v2/src/composables/useWebSocket.js`
- `tests/test_logging_system.py`
- `tests/test_ws_and_review.py`
- existing audit/docs under `docs/yu/`

## Short conclusion

The runtime no longer lacks logs. It now has:

- durable per-session JSONL logs,
- per-task log files,
- current-session replay on `sync_request`,
- task trace rows in Diagnostics.

The remaining pain is **not missing data**. It is that the current debugging flow is still **raw-event-first** instead of **triage-first**.

The smallest next slice that would make iterative E2E debugging dramatically easier is:

> add a structured **task triage snapshot** to `task_list` / `task_update`, and make TaskPanel + DiagPanel render that snapshot as the default debugging surface.

Do **not** make offline session replay the next slice. It is important, but it is a bigger feature. The faster win is to stop forcing the developer to mentally compress raw traces every time.

## What still makes debugging painful

### 1. The transport is fine; the payload semantics are weak

`ws_server/server.py` is not the main problem. It already:

- handles `sync_request`,
- broadcasts `log_entry` / `task_update` / `task_list` / `query_response`,
- supports targeted send-to-client replay.

The real issue is that the payloads sent through it are still too raw.

### 2. Current replay only gives rows, not an answer

`main.py::_replay_history()` replays:

- raw `log_entry`,
- raw `player_notification`,
- raw `query_response`.

That helps late-open debugging, but it still leaves the user asking:

- Is this task alive or effectively dead?
- Is it waiting on capability, resources, stale world, or a blocker?
- What was the last decisive transition?

Those answers are not first-class in the payload.

### 3. TaskPanel is still regexing summaries

`web-console-v2/src/components/TaskPanel.vue` currently uses `getTaskWaitingHint()` to infer waiting state from:

- `job.status`,
- `job.summary`,
- regex matches on text like `resource_lost`, `waiting for replacement`, `capability`.

That is useful as a stopgap, but it is still heuristic and brittle.

### 4. DiagPanel is a raw timeline, not a triage view

`web-console-v2/src/components/DiagPanel.vue` already shows:

- selected task trace,
- per-entry details,
- task log path,
- log filters.

But it still mostly asks the human to reconstruct:

- current phase,
- last blocker,
- active request/reservation,
- repeated failure pattern.

### 5. Chat/notification surfaces are still split from debugging state

`ChatView.vue` correctly shows:

- `query_response`,
- `player_notification`,
- `task_message`.

But these remain separate event streams. There is still no compact “task truth” object that says:

- what the task is currently doing,
- why it is waiting,
- what changed last.

### 6. Session persistence exists, but session browsing does not

`logging_system/core.py` persists:

- `all.jsonl`,
- `components/*.jsonl`,
- `tasks/*.jsonl`,
- `session.json`.

This is good and should stay. But `logging_system.replay()` is still memory-only, and the frontend cannot open a previous session from disk. That matters, but it is a **second slice**, not the next one.

## Smallest next slice

### Slice name

**Structured task triage snapshot**

### Goal

Make the default debugging path:

1. open task card,
2. see a structured current-state summary,
3. open Diagnostics,
4. see the same summary and the supporting trace below it,
5. only then open raw logs if needed.

### Proposed payload

Extend each task payload with a small structured block, for example:

```json
"triage": {
  "state": "waiting_capability",
  "phase": "request_units",
  "status_line": "等待能力模块补齐步兵（已请求 e1 ×3）",
  "last_transition": "unit_request_submitted",
  "last_transition_at": 1775.123,
  "waiting_reason": "capability_unit_request",
  "blocking_reason": null,
  "active_job_id": "j_xxx",
  "active_expert": "ReconExpert",
  "reservation_ids": ["r_xxx"],
  "world_stale": false
}
```

This should be deterministic and derived from existing runtime/task/job/message state. No LLM summarization is required for the first slice.

## Why this slice first

Because it reuses what already exists:

- task objects,
- job status,
- unit reservations/runtime_state,
- task messages,
- world stale health,
- log path / trace rows.

It avoids a bigger project such as:

- disk-backed session browser,
- offline replay loader,
- cross-session diff,
- LLM-generated debug summaries.

Those are still worth doing later, but they are not the fastest way to make tomorrow’s debugging loop better.

## Exact file / function targets

### 1. `main.py`

Primary target:
- `RuntimeBridge._task_to_dict()`

Add a helper:
- `RuntimeBridge._build_task_triage(task, jobs)`

That helper should derive the triage block from:

- `task.status`,
- `jobs`,
- `kernel.list_task_messages()` for recent task-specific warnings/info,
- `world_model.refresh_health()`,
- `world_model.runtime_state()` for reservation/capability hints.

Keep the logic here first. This is the narrowest place to enrich task payloads without redesigning logging or kernel semantics.

Secondary target:
- `RuntimeBridge._publish_task_updates()`

No protocol redesign needed; just ensure the enriched task payload flows through the existing `task_update`.

### 2. `web-console-v2/src/components/TaskPanel.vue`

Primary target:
- replace `getTaskWaitingHint()` heuristic-first rendering with structured `task.triage`.

Desired behavior:
- show `task.triage.status_line` as the main hint,
- fall back to the old heuristic only if `triage` is absent.

This immediately turns the task list into a real triage surface.

### 3. `web-console-v2/src/components/DiagPanel.vue`

Primary target:
- add a summary block for the selected task above the raw trace stream.

Show:
- `state`
- `phase`
- `waiting_reason` / `blocking_reason`
- `active expert/job`
- `reservation ids`
- `world stale`

This keeps raw trace useful, but stops it from being the first thing the developer has to parse.

### 4. `tests/test_ws_and_review.py`

Add contract tests asserting that:

- `task_list` / `task_update` include the `triage` object,
- `sync_request` still replays current state with the enriched task payload,
- structured waiting/capability state survives reconnect.

### 5. Optional follow-up test target

`tests/test_logging_system.py`

Only if needed, add a small regression around the report logic source data. This is not the main place to test the slice.

## Recommended implementation order

1. **Backend payload first**
   - implement `RuntimeBridge._build_task_triage()`
   - inject it into `_task_to_dict()`

2. **TaskPanel consumption**
   - render the triage summary as the default task hint

3. **DiagPanel consumption**
   - add selected-task summary header

4. **WS/reconnect contract tests**
   - ensure `sync_request` and task payloads keep working

5. **Only after this slice**
   - disk-backed session loader / session picker
   - task/session export bundle
   - cross-session compare

## What to defer

Not next slice:

- offline session replay UI,
- session catalog / session picker,
- compare-two-sessions tooling,
- log search over persisted history,
- LLM-generated diagnostic summaries.

These are good Phase 2+ debugging features. They are not the smallest step.

## Final recommendation

The next debugging UX slice should be:

> **structured task triage snapshot in `task_list` / `task_update`, rendered first-class in TaskPanel and DiagPanel**

That is the fastest path from:

- “the data exists, but debugging is still painful”

to:

- “I can look at one task and immediately know whether it is executing, waiting on capability, blocked by resources, or poisoned by stale world.”
