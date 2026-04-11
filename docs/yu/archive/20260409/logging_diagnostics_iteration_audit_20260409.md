# Logging / Diagnostics / E2E Debugging UX Audit

Date: 2026-04-09

Scope:
- `logging_system/`
- `main.py`
- `ws_server/server.py`
- `web-console-v2/src/components/DiagPanel.vue`
- `web-console-v2/src/components/TaskPanel.vue`
- `tests/test_ws_and_review.py`
- `tests/test_logging_system.py`
- E2E pain-point reports under `docs/yu/` and `docs/wang/`

## Executive Summary

The current system has moved past “no observability” and now has real structured logs, per-task log files, replay of recent live history, and a diagnostics pane that can surface task traces. That is a meaningful step forward.

The remaining problem is not raw visibility, but **iteration ergonomics**:

- logs are available, but they are still too raw and too repetitive;
- the current replay path only reconstructs the **current live session**;
- the UI exposes events, but not enough **human-level task state**;
- triage still requires reading long traces and mentally reconstructing causality;
- there is no reliable offline/session browser workflow for “what happened during task X in session Y?”

So the current pipeline is good enough for live debugging and local forensics, but not yet good enough for **fast developer iteration** or **demo-grade explainability**.

---

## 1. What the current pipeline already does well

### 1.1 Structured logging exists end-to-end

`logging_system/core.py` stores structured `LogRecord`s in memory and, when a persistence session is started, appends every record to:

- `Logs/runtime/session-<timestamp>/all.jsonl`
- `Logs/runtime/session-<timestamp>/components/<component>.jsonl`
- `Logs/runtime/session-<timestamp>/tasks/<task_id>.jsonl`

Relevant code:

- `PersistentLogSession.append()` writes all/component/task JSONL immediately: `logging_system/core.py:46-75`
- `LogStore.add()` emits each record into the current persistence session: `logging_system/core.py:144-167`
- `start_persistence_session()` creates the session directory and `latest.txt`: `logging_system/core.py:230-255`

This is a solid foundation. The team is no longer missing a durable structured log substrate.

### 1.2 Current-session replay is already better than before

`RuntimeBridge.on_sync_request()` now sends the current world snapshot and task list directly to the reconnecting client, then replays recent in-memory history.

Relevant code:

- direct client sync path: `main.py:321-325`
- current dashboard broadcast path: `main.py:443-475`
- recent history replay: `main.py:518-545`

That means late-open / reconnect is no longer blind the way it used to be.

### 1.3 Diagnostics already exposes task traces

`DiagPanel.vue` can:

- select a task,
- show its `task_log_path`,
- render a task trace stream,
- show job-level details,
- filter benchmark summaries.

Relevant code:

- task trace selector and log path display: `web-console-v2/src/components/DiagPanel.vue:1-26`
- task trace ingestion from `task_list`, `task_update`, `query_response`, and `player_notification`: `web-console-v2/src/components/DiagPanel.vue:196-273`
- per-entry details rendering: `web-console-v2/src/components/DiagPanel.vue:16-24`

This is already much better than a plain live log console.

---

## 2. Why debugging still feels painful

### 2.1 The system emits too much raw information, but not enough summary

The underlying traces are rich, but the human debugging workflow is still “read the whole thing and infer what matters”.

The clearest evidence is the `TaskAgent` trace audit from `docs/yu/archive/20260402/task_agent_prompt_runtime_report.md`:

- the prompt is re-injected every wake,
- the same `context_snapshot` repeats many times,
- long traces can grow to dozens of LLM calls and tens of thousands of characters,
- `resource_lost` / `waiting` loops are visible, but not compressed into a concise state summary.

The report explicitly notes:

- repeated prompt injection,
- repeated history replay,
- weak success criteria,
- weak phase policy,
- confusing signal ordering.

That matches the lived debugging experience: the data exists, but the human still has to do the compression mentally.

### 2.2 Replay is only “current live memory”, not a real session browser

The runtime can replay the current session’s in-memory log store, but it cannot load a prior session from disk and reconstruct it in the UI.

Relevant code:

- `logging_system.replay()` is just a query over the in-memory store: `logging_system/core.py:349-356`
- `main.py` replay logic uses `log_records()` from memory: `main.py:522-528`
- `main.py` stores a `task_log_path`, but the frontend only displays it as text: `main.py:560-574`, `web-console-v2/src/components/DiagPanel.vue:12-14`

So a developer can inspect the current run, but cannot yet do:

- “open the previous session that failed last night”,
- “compare this session against the last good session”,
- “replay task #004 from disk after restarting the backend”.

That missing offline replay path is a major reason the system still feels cumbersome.

### 2.3 Cause and effect are still too easy to misread

Several reports already showed this pattern:

- `resource_lost` can appear before `job_started` from the TaskAgent’s perspective;
- `Movement` or `Deploy` success can be over-interpreted by narrative state;
- `task_update`, `player_notification`, and raw logs are visible, but not grouped into a causal “why it happened” story.

The current UI shows:

- event rows,
- job rows,
- waiting badges,
- trace details.

What it does **not** yet show is:

- current phase,
- last decisive transition,
- last blocking reason,
- repeated failure signature,
- whether the task is actually waiting on a capability versus just continuing in a slow path.

That is why developers still have to read a lot of raw output to answer a simple question like:

> “Is this task alive, blocked, waiting on a capability, or just slow?”

### 2.4 The frontend still lacks a “triage-first” default view

`TaskPanel` now shows a waiting hint when job summaries imply capability/resource waiting, but it is still fundamentally a raw task list + raw job list.

Relevant code:

- current job rendering: `web-console-v2/src/components/TaskPanel.vue:19-35`
- waiting hint heuristic: `web-console-v2/src/components/TaskPanel.vue:121-139`

This is useful, but not enough. It still asks the developer to infer the important question from low-level status strings.

---

## 3. Concrete improvements to iteration UX

### 3.1 Add a task-level “what’s happening now” summary

Each running task should show a concise, human-readable status line synthesized from the trace, not just the jobs.

The line should answer:

- What phase is the task in?
- Is it executing, waiting, blocked, or partially done?
- What is the last blocking reason?
- What is the next likely action?

For example:

- `等待能力模块补齐单位`
- `等待资源恢复，仍在运行中`
- `侦察已启动，正在寻找目标`
- `正在部署基地车`

This is higher value than raw job status badges.

### 3.2 Make “waiting on capability” first-class, not just a heuristic

Right now `TaskPanel` infers waiting by regexing `task.jobs[].summary` and `task.jobs[].status`.

That works as a stopgap, but the UX should eventually consume a structured task state, for example:

- `waiting_reason`
- `phase`
- `capability_name`
- `retry_count`
- `last_transition`

That would let the UI show a reliable “waiting on capability” badge instead of guessing from text.

### 3.3 Add a task-centric “progress timeline”

The current task trace is already close, but it should be organized as a timeline with named milestones:

- task created
- NLU/routing decision
- capability/job started
- job waiting / progress / blocked
- task warning / notification
- task completion

The key improvement is not more rows. It is grouping and labeling the rows into a story.

### 3.4 Add session-level health markers to the dashboard

The dashboard should answer, at a glance:

- is the world stale?
- is the backend using current state or a fallback snapshot?
- is the current session healthy?
- is a task waiting because of capability, resources, or world sync?

The backend already has enough data to surface this:

- `WorldModel.refresh_health()`
- `world_summary()`
- `runtime_state()`
- task/job metadata
- player notifications

The missing step is a concise frontend status layer.

### 3.5 Prefer “one screen” triage over “scroll the log”

For demo and developer iteration, the default path should be:

1. look at the task card summary,
2. open the task trace,
3. see last block reason,
4. if needed, open raw session logs.

Not:

1. scroll generic logs,
2. search for task_id,
3. manually reconstruct the trace,
4. guess at causality.

---

## 4. Missing tooling for triage, replay, and root-cause analysis

### 4.1 Offline session replay

The biggest missing tool is a way to load a saved session directory and replay it after restart.

Current situation:

- logs are written to disk,
- but there is no API to load them back into `logging_system`,
- and the frontend cannot request “open session X”.

Missing features:

- session picker,
- load-by-session-path,
- replay current task from disk,
- compare two sessions.

### 4.2 Task-centric replay bundle

For a failed task, developers need a single export that bundles:

- task summary,
- task trace,
- related logs,
- benchmark timing,
- relevant world snapshots,
- player notifications,
- raw LLM input for each wake.

Today these exist in fragments, but not as a one-click bundle.

### 4.3 Causal stitching across layers

Right now the data is distributed across:

- `log_entry`
- `task_update`
- `query_response`
- `player_notification`
- benchmark events
- per-task JSONL

The system needs a causal stitcher that can answer:

- which LLM wake produced which job?
- which job emitted which signal?
- what immediately preceded a failure?
- what repeated over and over?

Without this, root-cause analysis is still manual.

### 4.4 Task/session search and comparison

There is no easy way to ask:

- “show all tasks that ended in `partial`”,
- “show all sessions where `resource_lost` repeated > 5 times”,
- “compare the current session against the last successful one”.

This is the kind of tooling that makes iteration fast.

### 4.5 Better file-backed debug entry points

`DiagPanel` currently shows `task_log_path` as text, but it cannot open or browse that file directly.

The UI would benefit from:

- “open task log”,
- “export this task”,
- “copy task trace”,
- “download current session bundle”.

That turns the existing persistence into an actual workflow tool.

---

## 5. What tests already cover, and what they still miss

### 5.1 Existing coverage is good for plumbing

Relevant tests:

- `tests/test_logging_system.py`
- `tests/test_ws_and_review.py`
- `tests/test_task_agent.py`

These cover:

- structured logging basics,
- session persistence write paths,
- websocket sync/reconnect,
- current-state push on `sync_request`,
- task-agent wake/review behavior.

### 5.2 Missing tests are mainly “developer workflow” tests

The current suite does **not** cover:

- loading a persisted session back into a replay view,
- task timeline reconstruction from disk,
- session compare / triage export,
- UI-driven detection of “waiting on capability” vs “dead task”,
- late-open debug panes reading historical task traces from disk,
- a task summary generated from the trace rather than raw rows.

That is why the backend can be correct while the developer experience still feels rough.

---

## 6. Implementation order

### Phase 0 — Low-risk demo UX fixes

Goal: make the current live demo easier to interpret without changing runtime behavior.

1. Improve task card summaries.
   - Show task-level phase / waiting / blocked summary.
   - Keep the existing raw job rows underneath.
2. Make `DiagPanel` default to the currently selected task’s trace and last meaningful event.
3. Surface world health clearly in the diagnostics header.
4. Add a clear “stale / sync degraded / waiting on capability” badge.

Why first:
- low risk,
- no runtime semantics change,
- immediate demo value.

### Phase 1 — Session-aware replay and inspection

Goal: let developers reopen a run and inspect it after the fact.

1. Add a session catalog / session picker.
2. Add a backend loader for persisted log sessions.
3. Allow `DiagPanel` to browse loaded sessions.
4. Allow task trace rehydration from `tasks/<task_id>.jsonl`.

Why second:
- this is the biggest missing triage tool,
- it directly reduces “stare at huge logs” pain.

### Phase 2 — Task-centric summarization

Goal: compress noisy traces into short, actionable summaries.

1. Generate a per-task summary from trace events.
2. Highlight repeated failure signatures.
3. Extract last blocking reason and last successful transition.
4. Surface a “what changed since the last wake?” diff.

Why third:
- once session replay exists, summarization becomes much more useful.

### Phase 3 — Root-cause workflow tools

Goal: make debugging faster than manual log reading.

1. Add a one-click task/session export bundle.
2. Add search by task status / error signature / expert type.
3. Add compare-session tooling.
4. Add a trace-to-failure clustering view.

Why fourth:
- these are the tools that turn logging into actual iteration speed.

### Phase 4 — Cross-session analysis and regression tracking

Goal: support longer-term iteration and future pre/demo cycles.

1. Persist a session index across runs.
2. Track recurring failure signatures.
3. Surface regressions in the dashboard.
4. Export a compact “what improved / what regressed” summary.

Why last:
- useful, but only after the basic triage/replay flow exists.

---

## 7. Bottom line

The current observability stack is **real** and already better than a typical prototype:

- logs are structured,
- sessions are persisted,
- task traces exist,
- reconnects get current state,
- diagnostics can inspect live traces.

But developer iteration still feels painful because the system does not yet provide:

- a compact task story,
- disk-backed replay,
- session browsing,
- causal stitching,
- root-cause summaries,
- one-click triage artifacts.

So the right next move is not “more logs”.
It is to turn the existing logs into a **workflow**.
