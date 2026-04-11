# E2E Task-Centric Iteration UX Audit

Date: 2026-04-09  
Author: yu

Scope reviewed:
- `logging_system/core.py`
- `main.py`
- `ws_server/server.py`
- `web-console-v2/src/components/DiagPanel.vue`
- `web-console-v2/src/components/TaskPanel.vue`
- `web-console-v2/src/App.vue`
- prior audits:
  - `docs/yu/logging_diagnostics_next_slice_20260409.md`
  - `docs/yu/replay_triage_next_slice_20260409.md`

Constraint:
- No code changes in this audit.
- Goal: make E2E debugging **task-centric** instead of **raw-log-centric**.
- Focus on replay, summaries, blockers, triage bundles, and diagnostics UX.

## Short conclusion

The next iteration-UX bottleneck is no longer missing logs, missing persisted sessions, or missing live triage.

Those pieces already exist:
- per-session and per-task JSONL logs
- `sync_request` replay of current live state/history
- task triage on `task_list` / `task_update`
- disk-backed `task_replay_request`
- `DiagPanel` task trace view

The remaining problem is:

> the replay/debug path is still **row-centric**.  
> The developer still has to read a long event stream and mentally answer:
> - what happened,
> - what is the blocker,
> - what changed last,
> - whether the task is stuck, progressing, or already doomed.

So the smallest next slice should **not** be more logs and should **not** be a full session browser.

The smallest next slice should be:

> **upgrade `task_replay_request` into a task debug bundle**  
> that returns a deterministic summary + blocker view + key transitions + supporting raw entries.

This keeps the current transport and persisted logs, but changes the default debugging loop from “read rows” to “inspect one task bundle”.

## 1. What is already good enough

## 1.1 Durable logging exists

`logging_system/core.py` already persists:
- `all.jsonl`
- `components/*.jsonl`
- `tasks/<task_id>.jsonl`
- `session.json`

There is already a task-scoped disk reader:
- `read_task_replay_records(task_id, session_dir=None, limit=None)`

So raw evidence is not the bottleneck.

## 1.2 Live triage exists

`main.py::_build_task_triage()` already derives:
- `state`
- `phase`
- `status_line`
- `waiting_reason`
- `blocking_reason`
- `active_expert`
- `active_job_id`
- `reservation_ids`
- `world_stale`
- `active_group_size`

And the frontend already uses it:
- `TaskPanel.vue` prefers `task.triage.status_line`
- `DiagPanel.vue` shows a triage summary block for the selected task

So “what is this task doing **right now**?” is already much better than before.

## 1.3 Disk-backed task replay exists

`main.py::RuntimeBridge.on_task_replay_request()` already:
- reads persisted task records from disk
- resolves the current session log path
- returns them to the frontend

`ws_server/server.py` already supports:
- inbound `task_replay_request`
- outbound `task_replay`

`DiagPanel.vue` already:
- requests replay when a task is selected
- merges replay rows with live trace rows

So the problem is not “there is no replay”.

## 2. Root cause of the remaining debugging pain

The current UX is still **raw-log-centric** because replay returns rows, not answers.

### 2.1 `task_replay_request` returns data, but not interpretation

Current payload from `main.py::on_task_replay_request()` is basically:
- `task_id`
- `log_path`
- `entry_count`
- `entries`

This is useful transport-wise, but it still leaves all of the following to the human:
- what were the decisive transitions?
- what blocker dominated this task?
- was it waiting on capability, resources, player, stale world, or pure retry?
- when did it last make real progress?
- did it spin on the same failure signature?

### 2.2 `DiagPanel` is still a timeline-first UI

`DiagPanel.vue` currently shows:
- a small current triage block
- then a large trace stream
- then raw log stream

That means the primary debugging gesture is still:
- pick task
- scroll trace
- expand JSON
- reconstruct meaning manually

This is better than `cat tasks/<task_id>.jsonl`, but not enough.

### 2.3 Triaging and replay are still split

There are currently two separate mental models:

1. **Live snapshot**
- driven by `task.triage`
- answers “what is true now”

2. **Replay rows**
- driven by persisted JSONL
- answers “what events happened”

What is missing is the bridge:

> “Given this task’s replay, what is the compact debugging story?”

### 2.4 There is no task bundle

The current system lacks a single deterministic object that says:
- header
- current/latest triage
- last meaningful transitions
- blocker summary
- reservation summary
- related player-facing messages
- raw trace

Without that, debugging is still event archaeology.

## 3. Smallest next slice

## Slice name

**Task Debug Bundle over existing replay transport**

## Goal

Keep the existing `task_replay_request` flow, but upgrade its response from:

> “here are the rows”

to:

> “here is the task-level debugging bundle, and here are the supporting rows”

This is smaller than:
- a full historical session browser
- offline session search
- LLM-generated summaries
- global causal graph stitching

And it directly attacks the current pain.

## 4. Proposed bundle shape

The existing `task_replay` response should be extended, not replaced.

Suggested payload:

```json
{
  "task_id": "t_xxx",
  "session_name": "session-20260405T231502Z",
  "task_log_path": "Logs/runtime/.../tasks/t_xxx.jsonl",
  "entry_count": 842,
  "bundle": {
    "summary": {
      "task_status": "partial",
      "current_or_final_state": "blocked",
      "status_line": "等待能力模块交付单位：e1 × 3",
      "active_expert": "ReconExpert",
      "active_job_id": "j_xxx",
      "world_stale": false
    },
    "last_transition": {
      "event": "unit_request_submitted",
      "timestamp": 1775.123,
      "message": "请求步兵 × 3"
    },
    "blockers": [
      {
        "kind": "unit_reservation",
        "count": 4,
        "latest_message": "等待能力模块交付单位：e1 × 3"
      }
    ],
    "highlights": [
      {
        "timestamp": 1775.101,
        "kind": "job_started",
        "message": "ReconExpert started"
      },
      {
        "timestamp": 1775.122,
        "kind": "task_warning",
        "message": "电力不足"
      }
    ],
    "player_visible": [
      ...
    ]
  },
  "entries": [...]
}
```

Important property:
- `entries` remain available
- but the human no longer starts from them

## 5. Deterministic derivation rules

This bundle should be deterministic and derived from existing runtime/log data only.

No LLM summarization is needed in the first slice.

### 5.1 Summary

Source:
- live task payload from `_task_to_dict()` when available
- fallback derived from persisted rows if task no longer exists in memory

Fields:
- task status
- most recent triage-like state
- active expert/job when inferable
- world stale flag if present

### 5.2 Last transition

Derived from the latest decisive replay row among:
- `task_update`
- `task_complete`
- `job_started`
- `tool_execute`
- `task_message`
- `player_notification`

Not from arbitrary debug rows.

### 5.3 Blockers

Derived by scanning the replay for repeated deterministic signatures:
- `task_warning`
- `waiting_reason`
- `blocking_reason`
- reservation wait
- world stale
- repeated failed/aborted job patterns

This should answer:
- what was the dominant blocker category?
- how many times did it recur?
- what was the last concrete message?

### 5.4 Highlights

A compact ordered subset of the replay:
- job start
- job completion/failure
- warning
- question
- query_response tied to task
- reservation created/fulfilled

This is the “story spine”.

### 5.5 Player-visible thread

Collect the subset of rows that actually reached the player:
- `query_response`
- `player_notification`
- `task_message`

This answers:
- what did the user actually see?

That is especially important in E2E debugging because a task can be internally active while externally silent.

## 6. Exact current code paths to extend

## 6.1 `logging_system/core.py`

Current relevant helper:
- `read_task_replay_records(...)`

Smallest extension:
- keep `read_task_replay_records`
- add a new deterministic helper such as:
  - `build_task_debug_bundle(task_id, records, live_task=None)`

This function should stay pure:
- input: raw rows + optional live task dict
- output: compact bundle dict

Why here:
- it already owns persisted record reading
- avoids burying replay parsing logic inside the websocket layer

## 6.2 `main.py`

Current path:
- `RuntimeBridge.on_task_replay_request()`

This is the exact place to extend.

Current behavior:
- read records
- send raw payload

Recommended change:
- also resolve the current live task payload if it still exists
- build the deterministic debug bundle
- send:
  - `session_name`
  - `task_log_path`
  - `bundle`
  - `entries`

No new transport family is required.

### Also relevant in `main.py`

- `_task_to_dict()`
- `_build_task_triage()`

These should become the source of truth for live summary fields when the task is still in memory.

## 6.3 `ws_server/server.py`

No protocol redesign needed.

Current support already exists:
- inbound `task_replay_request`
- outbound `task_replay`

Smallest change:
- none beyond allowing the richer payload through

## 6.4 `web-console-v2/src/components/DiagPanel.vue`

Current behavior:
- stores replay rows in `replayCache`
- merges them into the trace stream

This is where the UX must change.

Recommended additions:
- `replaySummaryCache[taskId]`
- a new summary block above the trace stream for:
  - current/final state
  - last transition
  - top blockers
  - player-visible thread summary

Important rule:
- raw entries remain below
- the summary becomes the default reading path

This is the smallest change that actually shifts the debugging posture from row-first to task-first.

## 6.5 `web-console-v2/src/components/TaskPanel.vue`

This slice does **not** need to touch `TaskPanel` first.

Reason:
- `TaskPanel` already has triage/status-line
- the immediate pain is E2E diagnosis after something goes wrong, which lives in Diagnostics

TaskPanel can consume richer blocker summaries later if useful.

## 7. Why this slice is better than the other obvious options

### Better than “more triage fields”

Triage is live-state only.
It does not solve postmortem or session-replay comprehension by itself.

### Better than “session browser first”

A full browser is larger:
- session listing
- cross-session selection
- filters/search

The bundle slice helps immediately even if only the current/latest session is supported.

### Better than “LLM summarization of logs”

Too expensive and too fragile for the next step.
Current logs are already structured enough for a deterministic summary layer.

### Better than “just open JSONL in UI”

That would still be raw-log-centric, just in a browser pane instead of a terminal.

## 8. Recommended implementation order

### Step 1
Extend `task_replay_request` backend response with:
- `session_name`
- `task_log_path`
- deterministic `bundle`
- unchanged `entries`

### Step 2
Teach `DiagPanel` to render the bundle before the raw trace.

### Step 3
Only after the bundle works, consider:
- offline session selection
- bundle export
- cross-task/session triage views

## 9. Safest first slice

If only one very small implementation slice is chosen, it should be:

> **reuse the existing `task_replay_request` path, but add a deterministic task debug bundle and render it in `DiagPanel` above the raw trace.**

That is the minimum change that makes E2E debugging meaningfully task-centric instead of raw-log-centric.

