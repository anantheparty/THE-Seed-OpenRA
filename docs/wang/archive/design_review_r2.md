# Adversarial Re-Audit of `design.md` (Round 2)

Date: 2026-03-30
Author: yu

## Verdict

This revision is **much stronger** than Round 1.

The original 7 blockers are largely addressed:

- runtime model is now single-thread GameLoop
- action path is concretely defined
- `ResourceRequest` exists
- typed `SuccessCondition` / `FailureCondition` exist
- `Event` exists
- cancel/preempt semantics exist
- several schema holes were closed

So the answer is **not** "the design is still fundamentally blocked."

However, I do **not** think it is yet fully implementation-complete. I still see a small set of concrete gaps and mismatches that would force an implementer to invent behavior.

My current assessment:

- **Round 1 blockers:** mostly closed
- **Remaining implementation blockers:** 6
- **Scenario walkthrough:** Steps 1-3 are codeable; Steps 4-5 and edge cases are improved but still not fully closed

## What is now solid

These parts are now strong enough to code against:

1. **Runtime ownership**
   - single-thread `GameLoop` at 10 Hz
   - no expert-owned threads
   - expert-specific `tick_interval`
   - `docs/wang/design.md:36-87`

2. **Core data contracts**
   - `TaskSpec`, `ExecutionJob`, `Constraint`, `Outcome`, `Action`, `ActionResult`, `ResourceRequest`, `Event`, `NormalizedActor`
   - `docs/wang/design.md:158-371`

3. **WorldModel / ActionExecutor direction**
   - refresh cadence
   - event detection shape
   - centralized executor
   - `docs/wang/design.md:559-701`

4. **Scenario-level thinking**
   - mainline recon path
   - scout death
   - cancel
   - preempt
   - `docs/wang/design.md:764-864`

This is a real improvement. The design now feels like something an engineer can start from, instead of just infer from.

## Remaining blockers

### 1. `Outcome.result` and `JobStatus` are still mismatched

`ExecutionJob.status` uses:

- `pending`
- `binding`
- `running`
- `waiting`
- `succeeded`
- `failed`
- `aborted`
- `superseded`

`docs/wang/design.md:202-217`

But `Outcome.result` uses:

- `success`
- `partial`
- `failed`
- `aborted`
- `superseded`

`docs/wang/design.md:252-263`

And `Kernel.on_outcome()` does:

```python
job.status = JobStatus(outcome.result)
```

`docs/wang/design.md:404-410`

This will not work for:

- `success` vs `succeeded`
- `partial` which has no `JobStatus` equivalent at all

This is a direct code-level blocker, not a style issue.

### 2. The scenario still does not populate all required fields of the defined types

Wang explicitly asked: for each step, are all fields populated with concrete values?

The answer is still **no**.

Examples:

#### `ResourceRequest`

Defined fields:

- `request_id`
- `job_id`
- `kind`
- `count`
- `predicates`
- `mandatory`
- `allow_wait`
- `allow_substitute`
- `allow_preempt`

`docs/wang/design.md:301-312`

But the scenario only shows:

```python
ResourceRequest(kind="actor", count=1,
                predicates={"mobility":"fast"}, mandatory=True, allow_wait=False)
```

`docs/wang/design.md:797-800`

Missing:

- `request_id`
- `job_id`
- `allow_substitute`
- `allow_preempt`

#### `Action`

Defined fields:

- `action_id`
- `job_id`
- `actor_id`
- `command`
- `target_pos`
- `target_actor_id`
- `params`
- `priority`
- `expires_at`

`docs/wang/design.md:271-282`

But the scenario uses:

```python
Action(actor_id=57, command="move", target_pos=(1600,200))
```

`docs/wang/design.md:807-810`

and

```python
Action(actor_id=57, command="attack_move", target_pos=(1820,430))
```

`docs/wang/design.md:816-818`

Missing:

- `action_id`
- `job_id`
- `target_actor_id`
- `params`
- `priority`
- `expires_at`

#### `Outcome`

Defined fields include:

- `directive_id`
- `resources_released`
- `recoverable`
- `followup_suggestions`
- `timestamp`

`docs/wang/design.md:252-263`

But the scenario success outcome only gives:

- `job_id`
- `result`
- `reason`
- `data`

`docs/wang/design.md:820-824`

So the scenario is still not fully executable as a typed walkthrough.

### 3. Mid-task resource requests still have no clean interface path

The design says mid-task resource replenishment is supported, and gives this example:

```python
new_actors = self.kernel.on_resource_request(request)
```

`docs/wang/design.md:469-483`

But the `Expert` interface only exposes:

- `bind(task, world)`
- `start(task, world, assigned)`
- `tick(world)`
- `on_resource_lost(actor_id, world)`
- `abort(reason)`

`docs/wang/design.md:492-525`

There is no `kernel` handle in the interface, and `tick()` only receives `world`.

So I still cannot write this path cleanly without inventing one of:

- `Expert.kernel`
- `Expert.request_resources(...)`
- `world.request_resources(...)`
- callback injection during `start()`

This matters directly for the scout-death edge case.

### 4. Cancel API is still mismatched with the scenario

The public Kernel API is:

```python
def cancel_by_directive(self, directive_id: str):
    """取消某个指令产生的所有 Job。"""
```

`docs/wang/design.md:431-438`

But the scenario for `"取消探索"` is:

```text
Directive(kind="cancel", target="探索")
  → Kernel.cancel_by_directive(): 匹配 intent 含 "recon"/"explore" 的 Job
```

`docs/wang/design.md:443-450`, `docs/wang/design.md:846-852`

These are not the same operation.

One is:

- "cancel all jobs produced by original directive id X"

The other is:

- "cancel jobs matching the semantic selector '探索'"

That means the implementation still lacks a concrete cancel selector model. It needs one of:

- `cancel_by_directive_id(original_directive_id)`
- `cancel_by_intent(selector)`
- `cancel_by_target(resolved_target)`
- a dedicated `CancelSpec`

Right now the method name/signature and the example behavior disagree.

### 5. Startup sequence and dashboard command ingress are still incomplete

The startup sequence is much better, but still incomplete at the integration boundary.

Current startup:

- creates `resolver`, `decomposer`, `interpreter`
- creates `dashboard`
- runs `GameLoop`

`docs/wang/design.md:89-120`

But two critical things are still missing:

#### A. How user commands enter the system

The dashboard section defines outbound WebSocket events:

- `world_snapshot`
- `task_update`
- `task_list`
- `log_entry`
- `action_executed`

`docs/wang/design.md:713-721`

But there is no inbound command contract for:

- player text command submission
- cancel command submission
- mode switching
- clarification responses

The user panel explicitly includes "命令输入". `docs/wang/design.md:709-711`

So the dashboard is not yet fully implementable end-to-end.

#### B. Kernel wiring is still implicit

`Kernel.submit_directive()` uses:

- `self.resolver`
- `self.decomposer`

`docs/wang/design.md:382-390`

But the startup sequence shows:

- `resolver = Resolver(world)`
- `decomposer = Decomposer()`
- `interpreter = Interpreter(resolver, decomposer, kernel)`

`docs/wang/design.md:107-110`

The document never states how `Kernel` receives `resolver` and `decomposer`.

This is not a big conceptual blocker, but it is still missing wiring in the composition root.

### 6. `Action` / `ActionExecutor` are still too actor-centric for queue-based actions

`Action` requires:

```python
actor_id: int
```

`docs/wang/design.md:271-282`

And `ActionExecutor.execute_batch()` groups actions by `actor_id`:

```python
by_actor = group_by(actions, key=lambda a: a.actor_id)
```

`docs/wang/design.md:659-674`

But the design also includes queue-based work:

- `ResourceRequest.kind = "production_queue"`
- `Action.command = "produce"`

`docs/wang/design.md:303-312`, `docs/wang/design.md:276-280`, `docs/wang/design.md:685-686`

That creates a concrete modeling problem:

- what `actor_id` does a `produce` action use?
- how are multiple queue actions deduplicated?
- should grouping key be actor, queue, or generic resource key?

This can be fixed, but right now the action layer is still biased toward unit actions and does not cleanly model production-queue actions.

## Scenario walkthrough

## Step 1: Interpreter

**Can I code it from the spec alone?** Yes, mostly.

Reason:

- `Directive` is defined
- example is concrete
- ambiguity threshold exists

Remaining minor gap:

- clarification response path is not yet specified in dashboard/input transport

## Step 2: Resolver

**Can I code this exact scenario from the spec alone?** Yes.

Reason:

- `"敌人基地"` is simple keyword resolution
- `ResolvedTarget` has enough fields

Caveat:

- I would still need the actual resolver rule table and scoring implementation for harder phrases, but this particular scenario is implementable.

## Step 3: Decomposer

**Can I code this step from the spec alone?** Yes.

Reason:

- the "explore to find X" pattern is clear
- `TaskSpec`, `SuccessCondition`, and `FailureCondition` exist

Caveat:

- the scenario still leaves some evaluator semantics implicit, especially for `expert_report`

## Step 4: Kernel

**Can I code it fully from the spec alone?** Not quite.

What is solid:

- expert selection
- instance-per-job model
- typed resource request idea

What is still missing:

- full `ResourceRequest` field population in the scenario
- mandatory initial bind failure behavior when no actor is available
- explicit wiring of resolver/decomposer into kernel startup

## Step 5: Execution timeline

**Can I code the control flow?** Yes.

**Can I code it without adding missing values?** No.

The missing values are exactly the omitted `Action` and `Outcome` fields described above.

## Edge cases

### Scout dies

**Much better than Round 1, but still not fully closed.**

Good:

- `UNIT_DIED`
- `on_resource_lost`
- waiting state

Still missing:

- how Expert actually sends the new `ResourceRequest` without a kernel/resource callback
- where the hardcoded "30s no scout" timeout comes from

There is a `timeout_s` on `TaskSpec`, but the edge case invents a separate 30s resource-wait timeout. `docs/wang/design.md:171-172`, `docs/wang/design.md:840-843`

That timeout needs its own explicit field or policy.

### Cancel

**Improved, but API/signature mismatch remains.**

The behavioral intent is there. The selector model is not.

### Preempt

**Mostly codeable.**

This path is much clearer now than before.

Main remaining issue:

- resource/action lease details are still implicit rather than formalized

## Dashboard readiness

**Readable output path:** mostly yes.

The frontend can likely render:

- task cards
- logs
- snapshot summaries

from the current schemas.

**Full dashboard implementation:** not yet.

Still missing:

- inbound command submission protocol
- schema for `world_snapshot`
- schema for `task_update`
- schema for `task_list`
- schema for `action_executed`
- mode subscription / filtering behavior for user mode vs debug mode

Only `Task` card and log schemas are concrete right now. `docs/wang/design.md:723-760`

## Contradictions check

The big runtime contradiction from Round 1 is gone.

I do **not** see a remaining architecture-level contradiction on the same scale.

The remaining contradictions are smaller but real:

1. `Outcome.result` vs `JobStatus`
2. `cancel_by_directive(directive_id)` vs `"取消探索"` semantic cancel flow
3. `Expert.tick(world)` interface vs `self.kernel.on_resource_request(...)` example

## Missing fields / values I would still add during implementation

If the document is meant to be fully code-ready, I would still add:

1. A `resource_key` or `target_resource` field to `Action`
   - to unify actor actions and production-queue actions

2. A dedicated resource-wait timeout field
   - either on `ExecutionJob` or `ResourceRequest`

3. A concrete cancel selector structure
   - not just `directive_id`

4. Full example values for all fields in the scenario
   - especially `Action`, `ResourceRequest`, `Outcome`

## Bottom line

This design revision **does close the original 7 blockers in substance**.

But I would **not** say "zero blockers" yet.

The remaining issues are smaller and more mechanical than Round 1, but they are still real implementation blockers because they would force the engineer to invent:

- status mapping behavior
- cancel selector semantics
- mid-task resource request plumbing
- queue-action modeling
- dashboard command ingress
- missing example field values

So my final assessment is:

- **Round 1 blocker set:** addressed
- **Round 2 result:** close, but not yet fully code-complete
