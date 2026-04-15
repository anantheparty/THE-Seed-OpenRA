# E2E Log Triage

Updated: 2026-04-16 00:11

## Purpose

Use this flow after each live E2E run so log review stays short, root-cause-first, and directly convertible into engineering slices.

## Standard Flow

1. Identify the exact live session directory.
   - Prefer `Logs/runtime/session-*/session.json`.
   - Confirm `started_at`, `world_health`, and `task_rollup` before reading task logs.

2. Pull the active task ids for the run you care about.
   - Use `session.json`, `components/kernel.jsonl`, and `tasks/*.jsonl`.
   - For commandless startup cases, check whether a capability task was auto-created and whether it actually executed tools.

3. Reconstruct behavior from per-task logs, not from the UI.
   - Read `tasks/<task_id>.jsonl` first.
   - Look for this chain:
     - `llm_succeeded`
     - tool call name/arguments
     - `job_started`
     - `expert_signal`
   - This is the authoritative answer to “did the system really act?”

4. Separate truth-surface bugs from action bugs.
   - Action bug: backend actually started jobs or called tools.
   - Truth-surface bug: UI/rendering/cache makes state flicker, disappear, or lie.

5. Reduce findings to minimal fix slices.
   - One slice per root cause.
   - Each slice must name:
     - symptom
     - root cause
     - narrow files to touch
     - focused regression to pin

6. Verify the slice locally before broader E2E.
   - Backend behavior: targeted `pytest` and `py_compile`
   - Frontend behavior: targeted component spec
   - Only after that run a broader smoke or next live round

## 2026-04-15 Case Study

Session:
- `Logs/runtime/session-20260415T154819Z`

### Finding 1: Capability acted without user command

Evidence:
- `tasks/t_4cf48613.jsonl:9` capability LLM reasoned it should query MCV on clean startup
- `tasks/t_4cf48613.jsonl:12` capability called `deploy_mcv`
- `tasks/t_4cf48613.jsonl:14` backend started `DeployExpert`
- `tasks/t_4cf48613.jsonl:30` capability called `produce_units(powr)`
- `tasks/t_4cf48613.jsonl:32` backend started `EconomyExpert` for `powr`
- `tasks/t_4cf48613.jsonl:89` capability called `produce_units(weap)`
- `tasks/t_4cf48613.jsonl:91` backend started `EconomyExpert` for `weap`

Root cause:
- Capability startup path still let `base_progression` / `buildable_now` become implicit action authorization.
- Prompt wording still allowed `[阶段]` to override the passive-assistant contract.
- `LOW_POWER` was also being surfaced inside `[玩家追加指令]`, which is another systemic path for accidental autonomous behavior.

Engineering slice:
- Add/verify a fail-closed idle gate in `task_agent/agent.py` so capability does not call LLM without explicit player demand or pending request state.
- Remove `LOW_POWER` from `_build_player_messages()` in `task_agent/context.py`.
- Tighten `build_capability_system_prompt()` in `task_agent/policy.py` so `[阶段]` is advisory only unless demand already exists.

Pinned by:
- `tests/test_task_agent.py`
- `tests/test_capability_task.py`

### Finding 2: Persisted Replay Summary flickered in diagnostics

Symptom:
- Diagnostics panel showed `Persisted Replay Summary` appearing/disappearing during live updates.

Root cause:
- `web-console-v2/src/components/DiagPanel.vue` deleted replay cache entries on every `task_update`, then requested a fresh replay asynchronously.
- During that gap, `selectedTaskReplayBundle` became `null`, so the panel dropped the replay block until the next replay response arrived.

Engineering slice:
- Keep the existing replay cache during `task_update`; only invalidate request level and asynchronously refresh.

Pinned by:
- `web-console-v2/src/components/__tests__/DiagPanel.spec.js`

## Current Minimal Verification Set

Backend:
```bash
python3 -m py_compile task_agent/agent.py task_agent/context.py task_agent/policy.py tests/test_capability_task.py tests/test_task_agent.py
pytest -q tests/test_capability_task.py tests/test_task_agent.py -k 'capability and (idle or player_message or system_prompt or low_power)'
```

Frontend:
```bash
cd web-console-v2
npm test -- --run src/components/__tests__/DiagPanel.spec.js -t 'keeps replay summary visible while live task_update triggers a refresh|renders current runtime triage when replay_triage is absent|falls back to replay_triage when current runtime exists but triage is null'
```
