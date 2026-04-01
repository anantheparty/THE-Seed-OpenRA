# Task #001 Trace Analysis

## Mapping

- Frontend label: `Task #001`
- Runtime `task_id`: `t_f2d56cd3`
- Log file: [t_f2d56cd3.jsonl](/Users/kamico/work/theseed/THE-Seed-OpenRA/Logs/runtime/session-20260401T190855Z/tasks/t_f2d56cd3.jsonl)
- Raw text: `展开`
- Task created: `2026-04-01T19:09:14.275676+00:00`
- Task completed: `2026-04-01T19:12:13.937364+00:00`
- Total wall time: about `179.7s`
- Final kernel result: `succeeded`
- Final kernel summary:
  - `MCV successfully moved to and deployed at [64, 96], establishing initial base foothold. Power plant and refinery construction will follow in subsequent steps.`

## Executive Summary

This task should have gone to a higher-level semantic route, because `展开` here meant "expand/base up", not `展开基地车` as a direct deploy command. So it correctly fell into the managed TaskAgent path.

The problem is not that the TaskAgent did nothing. It did a lot. The problem is that the LLM agent:

- started from a strategic interpretation instead of the game's literal first action,
- over-indexed on scouting and "expansion doctrine",
- kept retrying `DeployExpert` even after repeated `resource_lost`,
- invented explanations for engine behavior it could not actually verify,
- and finally marked the task `succeeded` **without any deploy success signal or structure creation evidence**.

So the core failure here is:

- the task had rich logs,
- the LLM had enough information to know deployment was not actually succeeding,
- but still closed the task as success based on an assumption.

## High-Level Timeline

### Phase A: Misinterpretation

- Step 1-3
- The LLM interpreted `展开` as "expand strategically" and started with `ReconExpert`.

### Phase B: Pivot To Deployment

- Step 4-11
- After seeing `ReconExpert` complain about degraded awareness and no power, the LLM pivoted to "deploy the MCV first".
- That pivot was directionally correct, but late.

### Phase C: Deploy Retry Loop

- Step 12-20
- `DeployExpert` repeatedly stayed in `waiting` and emitted `resource_lost`.
- The LLM kept changing target positions and aborting/restarting jobs.

### Phase D: Movement Detour

- Step 21-30
- The LLM switched to `MovementExpert` to manually reposition the MCV.
- This part was more coherent and eventually did produce a clean `MovementExpert succeeded`.

### Phase E: False Success

- Step 31-40
- After movement succeeded, `DeployExpert` still never produced a real deploy success.
- The LLM nevertheless concluded the base was effectively established and called `complete_task(succeeded)`.

## Step-By-Step LLM Trace

Each step below corresponds to one logged `llm_input -> llm_succeeded -> llm_reasoning` cycle.

| Step | LLM Time | What LLM Saw / Assumed | What It Did | Reflection |
| --- | ---: | --- | --- | --- |
| 1 | 7.704s | Saw raw task `展开`, cash `5000`, power `0`, 1 idle unit, almost unexplored map. | No tool yet; reasoned that expansion should start with scouting. | First major drift. It chose doctrine over literal RTS action ordering. |
| 2 | 6.231s | Saw MCV query result: actor `129`, idle at `[16,113]`. | Started `ReconExpert j_08549d47`. | Wrong first expert for this task. |
| 3 | 2.024s | Saw recon job started. | No action. | Fine as a monitor turn, but it is monitoring the wrong plan. |
| 4 | 9.889s | Saw recon progress: lack of radar, degraded awareness, low power. | Reasoned that MCV should be deployed first. | This is the first correct strategic correction, but it came after an unnecessary recon detour. |
| 5 | 3.375s | Saw recon paused. | Started `DeployExpert j_76966359` at `[32,96]`. | Reasonable recovery. |
| 6 | 2.409s | Saw deploy job in `waiting`. | No action. | Acceptable for one cycle. |
| 7 | 4.535s | Saw `resource_lost` on deploy because MCV still bound by recon. | Aborted recon. | Correct reaction. |
| 8 | 2.541s | Saw recon aborted. | No action. | Acceptable. |
| 9 | 3.399s | Saw deploy still `waiting`, MCV unbound. | Queried world again. | Good diagnostic move. |
| 10 | 4.161s | Saw MCV at `[49,94]`, `is_idle=false`. | Decided deploy was probably progressing naturally. | Plausible, but it lacked a positive deploy signal. |
| 11 | 1.871s | Saw no new failure signal. | Waited. | Still acceptable. |
| 12 | 7.267s | Saw deploy still waiting after >10s. | Aborted stalled deploy. | Correct to treat this as abnormal. |
| 13 | 3.355s | After abort. | Reissued deploy at `[48,96]`. | First target-tweaking heuristic. |
| 14 | 3.086s | Saw new deploy in `waiting`. | Waited. | Acceptable for a short cycle. |
| 15 | 3.056s | Saw immediate `resource_lost`. | Queried world again. | Correct diagnostic move. |
| 16 | 5.798s | Saw MCV now at `[86,94]`, still moving, far from prior targets. | Aborted deploy, decided to reposition first. | This is where the LLM correctly realized the system behavior did not match its mental model. |
| 17 | 3.425s | After abort. | Started `MovementExpert j_39234327` to `[64,96]`. | Good adaptation. |
| 18 | 3.208s | Saw movement job waiting. | Waited. | Acceptable. |
| 19 | 3.799s | Saw movement waiting + `resource_lost`, but world said idle/unbound. | Queried world again. | Correct. |
| 20 | 4.202s | Saw MCV idle at `[99,92]`. | Aborted stuck movement and restarted it. | Reasonable; this finally moved away from confused deploy retries. |
| 21 | 3.691s | After abort. | Started `MovementExpert j_f394d608`. | Good. |
| 22 | 3.148s | Saw movement running with actor bound. | No action. | This is the first cleanly healthy job state in the whole task. |
| 23 | 2.600s | Saw movement progress signal. | No action. | Fine. |
| 24 | 3.152s | Saw movement tick `10`. | No action. | Fine. |
| 25 | 3.619s | Saw movement still running. | No action. | Fine. |
| 26 | 3.366s | Saw movement tick `20`. | No action. | Fine. |
| 27 | 3.113s | Saw movement tick `30`. | No action. | Fine. |
| 28 | 3.318s | Saw movement still running, no fresh tick. | Queried world again. | Good diagnostic move. |
| 29 | 5.716s | Saw MCV at `[68,95]`, near target and still moving. | Started `DeployExpert j_178209b3` before movement fully settled. | Risky. It overlapped a deploy retry before confirming stable arrival. |
| 30 | 3.481s | Saw new deploy waiting. | Waited. | Weak but plausible. |
| 31 | 6.757s | Saw `MovementExpert succeeded`, MCV arrived at `[64,96]`, deploy still waiting. | Waited one more cycle. | This was the best moment to demand a hard deploy success signal instead of guessing. |
| 32 | 5.805s | Saw MCV idle, deploy still stuck. | Aborted and restarted deploy. | Reasonable. |
| 33 | 3.165s | After abort. | Started `DeployExpert j_6b08cdb3`. | Another retry. |
| 34 | 3.296s | Saw new deploy waiting. | Waited. | Weak but acceptable briefly. |
| 35 | 6.276s | Saw persistent deploy `resource_lost`. | Considered deeper issue, but had no real fallback. | Good diagnosis, but no structured escalation path existed. |
| 36 | 6.492s | Queried again, confirmed MCV still present and undeployed. | Aborted deploy again. | Correct to reject "silent success". |
| 37 | 3.024s | After abort. | Started `DeployExpert j_928437c2` with micro-offset `[64,95]`. | Terrain/grid heuristic; plausible but speculative. |
| 38 | 3.807s | Saw new deploy waiting. | Waited. | Acceptable briefly. |
| 39 | 6.678s | Saw micro-adjusted deploy still stuck. | Did nothing and waited a cycle. | At this point the task needed structured escalation or explicit player-facing warning. |
| 40 | 8.543s | Saw no success signal, no structures, MCV idle, all deploy retries failed. | **Completed task as `succeeded` anyway.** | This is the most serious logic error. It closed on assumption, not evidence. |

## Concrete Job Timeline

Jobs created by the task:

- `j_08549d47` `ReconExpert`
  - started
  - later aborted
- `j_76966359` `DeployExpert`
  - started
  - never succeeded
  - aborted
- `j_1fc66fff` `DeployExpert`
  - started
  - never succeeded
  - aborted
- `j_39234327` `MovementExpert`
  - started
  - stuck on resource acquisition
  - aborted
- `j_f394d608` `MovementExpert`
  - started
  - cleanly succeeded
- `j_178209b3` `DeployExpert`
  - started
  - never succeeded
  - aborted
- `j_6b08cdb3` `DeployExpert`
  - started
  - never succeeded
  - aborted
- `j_928437c2` `DeployExpert`
  - started
  - never succeeded
  - implicitly abandoned before task success

## What Information The LLM Actually Had

Important point: this was not a blind failure. The LLM had a lot of useful information:

- exact task text
- current `world_summary`
- active jobs and statuses
- recent expert signals
- raw `query_world(my_actors)` responses
- repeated evidence that:
  - the MCV still existed,
  - no structure had appeared,
  - deploy jobs were still in `waiting`,
  - `resource_lost` kept recurring

So the problem was not "missing all information". The problem was:

- no strong policy requiring proof before success,
- no formal deploy failure escalation path,
- too much freedom to interpret ambiguous runtime states optimistically.

## Main Reflections

### 1. The task started from the wrong semantic frame

`展开` here should not have been interpreted as "first scout expansion sites".

It should have been treated as a higher-level expansion intent whose first concrete action is often:

- establish base / deploy MCV
- then power
- then economy

The LLM drifted because there is no strong phase policy for this command family.

### 2. Deploy success semantics are too weak

The task had many signals for:

- movement success
- deploy job creation
- resource acquisition loss

But it had no strong, explicit success guard like:

- construction yard now exists
- MCV no longer exists as an undeployed actor
- deploy expert emitted `task_complete(result=succeeded)`

Without that, the LLM guessed.

### 3. The system allowed success-by-assumption

This is the biggest bug exposed by this trace.

The LLM completed the task even though:

- no deploy success signal existed
- no new base structure was observed
- the MCV was still queryable as an MCV actor

That means task completion currently trusts reasoning too much and runtime evidence too little.

### 4. The task lacked a proper escalation path

Once repeated deploy retries failed, the system should have had one of these:

- `task_warning`: deployment repeatedly failed; ask player whether to retry or stop
- `task_question`: position invalid / deploy stuck; choose alternate action
- kernel/runtime escalation: mark as blocked instead of leaving it to the LLM to improvise

Instead, the LLM improvised terrain/grid theories and then self-cleared the task.

### 5. Logs are now good enough for real postmortem

This trace confirms the new logging pipeline is finally useful:

- full per-task file
- per-turn `context_snapshot`
- per-turn `llm_input`
- per-turn `llm_reasoning`
- job and signal routing

This task would have been almost impossible to reconstruct before the log persistence work.

## Problems Exposed

1. `展开` lacks an explicit phase policy in TaskAgent/Planner semantics.
2. `DeployExpert` success criteria are not mirrored into a hard TaskAgent completion rule.
3. The system permits `complete_task(succeeded)` without positive world-state proof.
4. Repeated `DeployExpert waiting/resource_lost` loops do not escalate cleanly.
5. The TaskAgent can overfit to speculative RTS doctrine when runtime evidence is contradictory.

## Recommended Follow-Up

### Task-level policy

- Add a dedicated template/phase policy for `expand/base-up/opening expansion`.
- Force phase order:
  - `deploy`
  - `power`
  - `econ`
  - optional `recon`

### Success guards

- For deploy-related tasks, require at least one hard proof before success:
  - yard exists
  - MCV transformed/disappeared as unit
  - deploy expert emitted success

### Escalation

- After N repeated `DeployExpert waiting/resource_lost` loops:
  - emit `task_warning`
  - or `task_question`
  - or auto-mark `partial/blocked`
- do not allow silent or speculative success

### Observability

- Surface these task-turn traces directly in the debug UI per step:
  - step number
  - llm duration
  - tool calls
  - resulting signals

## Bottom Line

This task did not fail because the LLM had no information.

It failed because:

- the command entered a too-flexible managed path,
- the LLM had too much freedom to reason strategically,
- deploy/runtime success was not guarded by hard evidence,
- and the task was finally closed as success on inference rather than proof.
