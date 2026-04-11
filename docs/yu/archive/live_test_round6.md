# Live Test Round 6

Date: 2026-04-01

## Context

- Wang requested a fresh live Round 6 covering opening chain, recon, and combat.
- `agent-chat check_inbox()` succeeded at the start of this round and returned Wang's detailed procedure plus a later status ping.
- The live game state was not a clean MCV-only baseline. On first inspection it already contained multiple buildings and units, including several barracks and a jammed `Building` queue.

## Phase A: Runtime Bring-Up

### Backend restart

- Old backend on `:8765` was stopped.
- New runtime booted successfully with `python3 main.py --log-level INFO`.
- `lsof -i :8765` confirmed the WS server listener.
- Direct `GameAPI.query_actor(TargetsQueryParam(faction='己方'))` succeeded.
- Direct WS `sync_request` returned `world_snapshot`.

Note:
- Wang asked for `nohup ... > /tmp/backend.log`, but that launch did not leave a usable backend process in this environment. Foreground startup worked reliably and was used for the actual live run.

## Root Causes Found and Fixed

### 1. Terminal task completion leaked resources

- Symptom: a finished job could keep `queue:Building` bound, so the next build task started in a broken resource state.
- Root cause: `Kernel.complete_task()` / `cancel_task()` only aborted non-terminal jobs and never released resources from jobs that had already reached a terminal state.
- Fix:
  - commit `dff8cb4` `fix: release terminal task job resources`

### 2. Auto-place reported success even when nothing changed

- Symptom: `place_building` could return success text while the ready building stayed in queue.
- Root cause:
  - game side accepted "attempted auto place" as success even when no position was found
  - Python side trusted the response text without checking queue effect
- Fixes:
  - OpenCodeAlert submodule commit `12b63c814a` `fix: fail when auto placing ready building has no effect`
  - top-level pointer commit `21b15e5` `fix: update opencodealert building placement semantics`
  - Python follow-up commit `65dde1c` `fix: detect stalled building placement in economy flow`

### 3. Prompt-only structure build mapping was not enough

- Symptom: live `建造矿场` / `建造兵营` could drift into unrelated expert choices.
- Root cause: even with prompt guidance, the TaskAgent still relied on LLM interpretation for simple one-shot structure builds.
- Fix:
  - commit `7af8a42` `fix: bootstrap common structure build commands`

### 4. Bootstrapped build tasks could drift after success

- Symptom: live `建造兵营` bootstrapped correctly into `EconomyExpert`, then the TaskAgent re-entered the LLM loop, aborted the economy job, and pivoted into recon.
- Root cause: bootstrap jobs had no deterministic close path; the TaskAgent gave the LLM another turn after the job had already succeeded.
- Fix:
  - commit `a4f08fc` `fix: close bootstrapped build tasks deterministically`

### 5. Building tasks completed before final placement

- Symptom: a building task could emit `succeeded` on `PRODUCTION_COMPLETE` while the resulting ready building was still sitting unplaced in the queue.
- Root cause: `EconomyJob` treated `PRODUCTION_COMPLETE` as task completion for `queue_type="Building"` instead of waiting for the final ready building to be placed.
- Fix:
  - commit `777e8f0` `fix: require building placement before economy completion`

### 6. Pre-existing ready buildings did not count toward task completion

- Symptom: if the queue already contained a matching ready building, the job could place it, clear the queue, increase the building count, and still keep the task running because no new `PRODUCTION_COMPLETE` belonged to that task.
- Root cause: `EconomyJob` only advanced `produced_count` from completion events, never from consuming pre-existing ready backlog.
- Fix:
  - commit `e7daa12` `fix: count ready buildings toward economy tasks`

### 7. Legitimate blocked reasons were silent to the player

- Symptom: after the queue issue was fixed, `建造兵营` still looked "stuck" because the task remained `running` while the real cause was low power.
- Root cause: `SignalKind.BLOCKED` was routed only to the TaskAgent; no `TaskMessage` / user-facing warning was registered.
- Fix:
  - commit `8b84985` `fix: surface blocked expert signals to players`

## Live Verification Highlights

### Live `建造兵营` before deterministic close fix

- Adjutant acknowledged the command and created the task.
- The bootstrap correctly started `EconomyExpert`.
- After production success, the TaskAgent incorrectly re-entered the LLM loop and drifted into recon.
- This was fixed by `a4f08fc`.

### Live `建造兵营` after deterministic close fix

- The task completed from the bootstrap job without any LLM calls.
- Runtime logs confirmed `llm_calls=0`.
- However, a ready barracks still remained in the queue afterward, exposing the next root cause in building-completion semantics.

### Live `建造兵营` after placement-completion fixes

- The stale ready barracks was placed.
- The live actor count increased from `7` to `8` barracks.
- `Building` queue became empty.
- This proved the old queue-jam behavior was fixed.

### Current live state

- Current base power state is low:
  - `Power=10`
  - `PowerDrained=190`
  - `PowerProvided=200`
- In this state, `建造兵营` is now blocked for a legitimate reason (`low_power`), not because of a hidden queue jam.
- After commit `8b84985`, the user-visible feedback path now emits:
  - `player_notification` / task warning content: `电力不足，生产暂停等待恢复`

## Current Conclusion

- The original "兵营卡住" bug was real, but it was a stack of multiple issues:
  - fake auto-place success
  - terminal resource leak
  - deterministic build-task drift back into LLM
  - building completion before placement
  - pre-existing ready buildings not counting toward task completion
- Those queue/build semantics are now fixed locally and committed.
- On the current live save, the remaining reason `建造兵营` cannot proceed is low power, and that reason is now surfaced to the player instead of failing silently.

## Follow-up Fix: `queue_unassigned` is not a player blocker

- User feedback identified another misleading warning:
  - `生产队列资源未分配，等待中`
- This should not be surfaced as a player-visible warning. It is only an internal scheduling state before Kernel has assigned the production queue resource.
- Root cause:
  - `EconomyJob._enter_waiting()` emitted `SignalKind.BLOCKED` for `reason="queue_unassigned"`.
  - Kernel correctly converts all `BLOCKED` signals into `TaskMessage(task_warning)`.
  - So a harmless queue-allocation wait looked like a real gameplay blocker.
- Fix:
  - `queue_unassigned` now emits `SignalKind.PROGRESS` instead of `SignalKind.BLOCKED`.
  - True blockers still remain on the warning path:
    - `low_power`
    - `no_funds`
    - `cannot_produce`
    - `queue_ready_item_pending`
    - `ready_item_not_placeable`
- Verification:
  - `python3 tests/test_economy_expert.py`
  - `python3 tests/test_kernel.py`
  - `python3 -m py_compile experts/economy.py tests/test_economy_expert.py`
- UX note:
  - If the browser still shows this exact string now, it is likely an old session-cached notification from before the fix rather than a newly emitted warning.

## Remaining Round 6 Work

- The requested full clean opening chain still is not complete because the live game is not a clean MCV-only baseline.
- Recon and combat steps are not yet re-run on a clean baseline in this round.
