# Adversarial Re-Audit of `design.md` (Round 3)

Date: 2026-03-30
Author: yu

## Verdict

Round 3 is again better than Round 2. The 6 gaps I called out in R2 are mostly addressed in direction:

- `Outcome.result` and `JobStatus` are now aligned in naming
- scenario examples now populate far more fields
- mid-task resource requests now have `resource_callback`
- cancel now has `CancelSelector`
- dashboard ingress exists
- `Action` / `ActionResult` now use `resource_key`

So the design is clearly converging.

However, I still do **not** think the spec is fully zero-blocker / fully code-complete yet. I found **4 remaining implementation blockers**.

These are not architecture-level failures like Round 1. They are now narrower and more mechanical. But they are still real places where an implementer would have to invent behavior or fix inconsistencies during coding.

## What is now solid

These parts are now strong enough to implement from the spec:

1. **Runtime model**
   - single-thread `GameLoop`
   - expert-specific `tick_interval`
   - no expert-owned threads
   - startup order is much clearer
   - `docs/wang/design.md:36-121`

2. **Core contracts**
   - `Directive`, `ResolvedTarget`, `TaskSpec`, `ExecutionJob`, `Constraint`, `Outcome`, `CancelSelector`, `Action`, `ActionResult`, `ResourceRequest`, `Event`, `NormalizedActor`
   - `docs/wang/design.md:127-399`

3. **Action execution**
   - `resource_key` cleans up actor vs queue actions
   - `ActionExecutor` now groups by `resource_key`
   - `docs/wang/design.md:697-752`

4. **Mainline scenario**
   - the normal recon path is now much more fully typed
   - `docs/wang/design.md:824-915`

If I only looked at the happy path, I could start implementing.

## Remaining blockers

### 1. Cancel matching logic is still broken at the schema/code level

`CancelSelector` is a good addition. `docs/wang/design.md:270-282`

But `Kernel._match_jobs()` does this:

```python
elif selector.intent_match and selector.intent_match in job.intent:
    results.append(job)
```

`docs/wang/design.md:465-476`

There are two concrete problems:

#### A. `ExecutionJob` has no `intent` field

`ExecutionJob` includes:

- `job_id`
- `task_id`
- `directive_id`
- `status`
- `owner_expert_id`
- `expert_type`
- `resources`
- `pending_requests`
- `priority`
- `task_kind`
- `cancel_requested`
- `failure_reason`
- timestamps

`docs/wang/design.md:215-230`

There is **no** `intent` field on the job, yet `_match_jobs()` reads `job.intent`.

That is a direct implementation bug in the spec.

#### B. The scenario selector would not match with the shown logic

The cancel scenario says:

```python
CancelSelector(intent_match="recon|explore")
```

`docs/wang/design.md:281-282`, `docs/wang/design.md:937-939`

But `_match_jobs()` uses plain substring membership:

```python
selector.intent_match in job.intent
```

If `job.intent == "recon_find"`, then `"recon|explore" in "recon_find"` is false.

So the design text says the cancel flow works, but the actual matching code shown would not work.

This is the clearest remaining blocker.

### 2. Cancel / preempt path still drops `Outcome` information on the floor

The spec now makes `abort()` return a full `Outcome`. `docs/wang/design.md:500-503`, `docs/wang/design.md:941-944`

But `Kernel.cancel()` does:

```python
expert.abort("user_cancel")
job.status = JobStatus.ABORTED
self._release_resources(job)
```

`docs/wang/design.md:456-464`

That means the returned `Outcome` is ignored.

Practical consequences:

- `reason`
- `data`
- `resources_released`
- `recoverable`
- `followup_suggestions`
- `timestamp`

are all lost unless the implementer invents a second path.

It also means cancel/preempt path and normal `on_outcome()` path are inconsistent:

- normal completion goes through `on_outcome()`
- abort path mutates job directly and bypasses `on_outcome()`

The design text says "Kernel: 更新看板", but the shown code does not call:

- `_notify_dashboard(outcome)`
- `_check_blocked_tasks()`

on cancel/preempt.

So the spec still has an implementation gap in how abort outcomes are propagated.

### 3. Wait-timeout path is still not fully specified

`ResourceRequest` now has:

```python
wait_timeout_s: float
```

`docs/wang/design.md:332-337`

and the scout-death scenario says:

- request enters waiting queue
- 30s later timeout expires
- Kernel notifies Expert request failed

`docs/wang/design.md:924-932`

But the actual runtime mechanism is still missing. The spec does not define:

- where waiting requests are stored
- how timeout timestamps are tracked
- which component checks expiry
- when `job.status` moves from `WAITING` back to `FAILED`
- how Kernel "notifies Expert request failed"

There is no method like:

```python
expert.on_resource_request_failed(request_id, reason)
```

and no timeout sweep is specified in `GameLoop` or `Kernel`.

So the data field exists, but the actual timeout execution path is still not code-complete.

### 4. Dashboard outbounds are still not sufficiently specified for full implementation

Round 3 fixed inbound events well. `docs/wang/design.md:764-771`

But outbound events are still partly prose-only:

- `world_snapshot`
- `task_update`
- `task_list`
- `action_executed`

`docs/wang/design.md:773-781`

Only these have concrete payload schemas:

- Task card schema
- structured log schema

`docs/wang/design.md:783-822`

What is still missing:

#### `world_snapshot`

Current description:

- "经济、兵力、地图探索率"

No field-level schema is given.

#### `task_update`

Probably close to the task card schema, but the spec never explicitly says whether it is:

- full job object
- task card object
- delta patch

#### `task_list`

Again likely an array of task cards, but not stated formally.

#### `action_executed`

The text says:

- `Action + ActionResult`

but there is no concrete payload schema for the combined event.

Wang explicitly asked whether the dashboard can be implemented from the WebSocket event specs. My answer is:

- **ingress:** mostly yes
- **egress:** not fully yet

This is now a UI-integration blocker, not a core architecture blocker.

## Scenario walkthrough

## Step 1: Interpreter

**Can I write code for this step from the spec alone?** Yes.

The `Directive` contract is clear, and the example is fully concrete.

## Step 2: Resolver

**Can I write this exact scenario from the spec alone?** Yes.

For `"敌人基地"` the keyword path is straightforward and fully typed.

## Step 3: Decomposer

**Can I write this step from the spec alone?** Yes.

`TaskSpec`, `SuccessCondition`, and `FailureCondition` are all concrete enough for this path.

## Step 4: Kernel

**Can I write the happy-path resource bind from the spec alone?** Yes.

This is now much better:

- `ResourceRequest` is fully populated in the scenario
- `ExecutionJob` is fully populated in the scenario
- `expert.start(..., resource_callback)` is concrete

This step is now codeable.

## Step 5: Execution timeline

**Can I write the mainline tick/action/outcome flow from the spec alone?** Yes.

This was not true in earlier rounds. The happy path is now sufficiently typed.

## Edge case: scout dies

**Mostly codeable, but still not fully closed because of the timeout path.**

The callback model fixed the major missing interface from Round 2.

What remains missing is the timeout execution mechanism after the request has entered the wait queue.

## Edge case: cancel

**Conceptually fixed, but still blocked by the actual matching logic and abort-propagation path.**

The scenario is good. The shown code is not yet consistent with it.

## Edge case: preempt

**Mostly codeable.**

The main remaining issue is the same as cancel:

- what happens to the returned abort `Outcome`

## Contradictions check

The big cross-section contradictions from Round 1 and Round 2 are mostly gone.

I do **not** see another large-scale architecture contradiction.

The remaining contradictions are now local:

1. `Kernel._match_jobs()` expects `job.intent`, but `ExecutionJob` does not have it
2. `CancelSelector(intent_match="recon|explore")` is shown as working, but the matching logic shown would not match it
3. `abort()` returns `Outcome`, but the cancel code path ignores it

## Missing fields I would still add

If the goal is truly "I can code this without adding anything", I would still add:

1. `intent: str` on `ExecutionJob`
   - or explicitly route matching through `TaskSpec`

2. A typed waiting-request runtime record
   - e.g. `PendingResourceRequest(request, enqueued_at, expires_at)`

3. Explicit outbound schemas for:
   - `world_snapshot`
   - `task_update`
   - `task_list`
   - `action_executed`

## Bottom line

Round 3 is close.

The mainline scenario is now implementable. The original large blocker class is gone.

But I would still **not** declare "zero blockers" yet, because these 4 remaining issues still force implementation-time invention or correction:

1. broken cancel matching
2. abort outcome not propagated
3. wait-timeout mechanism unspecified
4. outbound dashboard event schemas incomplete

So my final assessment is:

- **Core architecture:** ready enough to implement
- **Spec completeness:** not yet perfect
- **Remaining blockers:** 4, all narrower than previous rounds
