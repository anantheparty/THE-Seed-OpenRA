# Adversarial Implementation Review of `design.md`

Date: 2026-03-29
Author: yu

## Verdict

**The design is not implementation-ready yet.**

If I started coding from `docs/wang/design.md` right now, I would have to invent behavior in multiple critical places. The document is strong on architectural intent, but still weak on execution semantics, data contracts, and runtime ownership.

The main problem is not "missing polish". The main problem is that several core components are still specified at the slogan level rather than the code-writing level.

## Highest-severity blockers

### 1. The runtime execution model is contradictory

There are two incompatible models in the spec:

- Section 4 says each Expert owns its own loop, and `tick()` is driven by the Expert's own thread/coroutine/timer. `docs/wang/design.md:217-240`
- Section 10 says a single-thread `GameLoop` iterates active Experts and calls `expert.tick(world)` itself. `docs/wang/design.md:451-499`

This is the single biggest blocker. Until this is resolved, I cannot correctly design:

- locking
- WorldModel freshness
- event delivery
- action execution
- cancellation/preemption
- dashboard update timing

### 2. The action path is still abstract, not implementable

The document says:

- Expert outputs `ActionProposal / Outcome` in the pipeline table. `docs/wang/design.md:49-55`
- The interface actually returns `list[Action] | Outcome`. `docs/wang/design.md:196-204`
- The game loop calls `executor.execute_batch(self.action_queue)`. `docs/wang/design.md:475-477`

But none of these are concretely defined:

- `ActionProposal`
- `Action`
- `ActionExecutor`
- `ActionResult`
- command ack / failure / timeout / retry behavior

So I still do not know who *actually* translates expert intent into `GameAPI` calls and how success/failure is observed.

### 3. Resource allocation cannot be implemented from the current contracts

`Expert.bind()` returns `list[str]`. `docs/wang/design.md:196-204`

That is far too weak for real allocation. It does not tell Kernel:

- what capability is needed
- how many units are needed
- whether the request is mandatory or optional
- whether the request can wait
- what fallback is allowed
- whether replacement is allowed mid-task

Example: "I need a fast unit" cannot be satisfied from `list[str]`. The request needs a typed structure like:

```python
ResourceRequest(
    kind="actor",
    count=1,
    predicates={"mobility": "fast", "armed": False},
    allow_wait=True,
    allow_substitute=True
)
```

Without this, Kernel cannot be written.

### 4. Resolver rules are too vague for actual implementation

The design says Resolver handles things like:

- `"左边那群坦克"`
- `"基地"`
- `"那个基地"`
- relative direction
- ambiguity fallback

But the actual matching algorithm is missing. `docs/wang/design.md:83-103`

I do not know:

- what candidate set is generated
- how groups are clustered
- how "left" is defined
- whether "left" is from camera, from my base, or from map axes
- how "that base" uses conversational context
- what happens when there are multiple equally plausible matches

So Resolver is conceptually present, but algorithmically absent.

### 5. Success/failure conditions are strings, not executable logic

`TaskSpec.success_condition` and `failure_condition` are plain strings like:

- `"enemy_base_located"`
- `"all_scouts_dead"`
- `"timeout_300s"`

`docs/wang/design.md:107-118`

This is not enough to implement evaluation. I still need:

- condition type
- parameters
- evaluation owner
- evaluation timing
- event-driven vs polling semantics

Right now these are names, not runnable contracts.

## Detailed gaps by stress-test area

## 1. Game loop / real-time integration

### 1.1 Poll frequency is still not concretely specified

Section 10 proposes a single-thread loop with `sleep(self.tick_interval)` at roughly `0.5-1s`. `docs/wang/design.md:482-490`

That is not enough detail to code:

- exact default tick interval
- whether interval is fixed or adaptive
- whether `world.refresh()` and `execute_batch()` share the same cadence
- how slow `refresh()` is allowed to be before skipping a tick

For `ReconExpert`, maybe 1 Hz is acceptable. For `CombatExpert` micro, it is not obviously acceptable.

### 1.2 The document still does not define who refreshes what, in what order, and with what granularity

`self.world.refresh()` exists only as a call site. `docs/wang/design.md:459-460`

Missing:

- full snapshot vs partial incremental refresh
- whether map/fog/economy/unit state have different refresh cadences
- whether refresh pulls one giant `GameAPI` snapshot or many smaller queries
- whether `detect_events()` runs on raw snapshots or already-normalized model state

The call sequence is shown; the refresh contract is not.

### 1.3 Base-under-attack reaction path is only a sketch

The design says:

- `BASE_UNDER_ATTACK` can be emitted by `WorldModel.detect_events()`. `docs/wang/design.md:500-509`
- Kernel may trigger a defense constraint or high-priority task. `docs/wang/design.md:511-513`

But the real propagation path is unspecified:

1. What exact event shape is emitted?
2. What threshold causes `BASE_UNDER_ATTACK`?
3. Does Kernel create a new `TaskSpec`, a new `ExecutionJob`, or only a `Constraint`?
4. Which existing tasks are paused, preempted, or left alone?
5. Who chooses whether to dispatch `CombatExpert`, `EconomyExpert`, or both?

This means "react to enemy attack" cannot be implemented deterministically from the spec.

### 1.4 The single-thread loop does not reconcile with expert-specific cadence needs

If the runtime is truly single-threaded, then every expert shares one tick budget.

Missing:

- per-expert desired cadence
- max budget per tick
- whether slow experts can skip cycles
- whether `CombatExpert` can run at 5-10 Hz while `EconomyExpert` runs at 0.5 Hz

If all experts run every `0.5-1s`, combat/micro is underspecified. If experts still own loops, Section 10 is wrong.

### 1.5 `GameLoop` is named, but startup/runtime ownership is still incomplete

Section 10 identifies the missing driver, which is good, but it still does not fully define:

- who constructs `WorldModel`
- who owns the `GameAPI` connection
- who constructs `Kernel`
- where experts are registered
- who starts/stops dashboard transport
- what shutdown order is safe

So the design has a loop, but not yet a complete composition root.

## 2. Resolver concreteness

### 2.1 `ResolvedTarget` is missing the data needed for ambiguous resolution

Current fields are:

- `owner`
- `entity_type`
- `actor_ids`
- `position`
- `known`
- `raw_text`

`docs/wang/design.md:87-96`

Missing fields I would need:

- candidate list
- match confidence
- ambiguity reason
- spatial qualifier
- referent source (`conversation_memory`, `world_match`, `default_rule`)

Without those, there is no principled way to log or explain why a target was resolved a certain way.

### 2.2 `"左边那群坦克"` is not reducible to the listed rules

The current rule list says:

- `"坦克"` / `"那群坦克"` → entity_type=unit
- `"左边"` / `"上方"` → relative direction, combine with `base_pos`

`docs/wang/design.md:98-103`

That is still insufficient. I need the exact algorithm:

- cluster enemy tanks first, or filter then cluster?
- what distance threshold defines "那群"?
- what is the frame of reference for "左边"?
- if two clusters are both left of the base, how is the winner selected?
- is "left" based on map coordinates, screen coordinates, or my main base orientation?

### 2.3 `"那个基地"` cannot be implemented because there is no referent memory model

The spec says "匹配失败 → ambiguity，反问", but demonstratives are not necessarily ambiguity failures. They are often references to prior context.

Missing:

- dialogue referent memory
- recent-target stack
- last-mentioned entity cache
- scope rules for "that", "there", "those units"

Without that, `"那个基地"` either fails too often or silently guesses.

### 2.4 Multiple enemy bases are not handled

For `entity_type="base"`, current `ResolvedTarget` can hold many `actor_ids`, but the design does not state:

- whether multiple enemy bases are valid output
- whether Resolver should return one base or a set
- how a later `Decomposer` or `Expert` interprets a multi-base target
- whether `"敌人基地"` means main base only or any known enemy base

This matters immediately for attack/recon tasks.

## 3. Resource allocation mechanics

### 3.1 Kernel does not have enough typed data to satisfy capability-based requests

The design example says:

- `ReconExpert.bind()` requests 1 fast unit. `docs/wang/design.md:330-331`

But nowhere does the document define:

- actor mobility classes
- actor health categories
- actor current assignment state
- actor role suitability
- actor ETA / reachability

So even after adding a better request object, Kernel still needs a concrete actor capability index.

### 3.2 The source of actor attributes is missing

Wang explicitly asked about speed, type, health.

The spec never says where those live:

- raw `GameAPI` actor objects?
- normalized `WorldModel` actor wrappers?
- static unit data + dynamic actor state?

Without a defined normalized actor schema, resource matching cannot be written cleanly.

### 3.3 "No matching actor available" behavior is not actually specified

Section 3 says Kernel can "分配或排队". `docs/wang/design.md:176-181`

But queueing is undefined:

- what queue object stores the wait?
- what wakes the queue up?
- does the task enter `pending` or `binding`?
- does it timeout?
- can lower-quality substitutes be assigned later?

This is a major implementation hole.

### 3.4 Mid-execution reallocation is not part of the interface

The scenario explicitly needs it:

- scout dies
- scout gets reassigned
- combat expert needs reinforcements

But the only resource hook is `bind()` at startup. `docs/wang/design.md:196-204`

Missing:

- `request_more_resources(...)`
- `on_resource_lost(...)`
- `on_resource_replaced(...)`
- resource lease model

### 3.5 Preemption semantics are incomplete

The design says higher-priority tasks may preempt lower-priority resources. `docs/wang/design.md:178-179`

But I still do not know:

- whether preemption is immediate or graceful
- whether `release()` is called before `abort()`
- whether actions already queued for the old owner are discarded
- how the old expert learns exactly which resource was revoked

Without lease/revocation semantics, preemption is dangerous to implement.

## 4. Action execution path

### 4.1 The design does not define an executable `Action` contract

`tick()` returns `list[Action]`, but `Action` is never specified.

I need at least:

- action type enum
- target reference form
- actor binding
- idempotency key
- deadline / expiration
- preconditions
- source task/job id

Otherwise `execute_batch()` is just a placeholder.

### 4.2 There is no concrete ActionExecutor spec

The game loop calls:

```python
self.executor.execute_batch(self.action_queue)
```

`docs/wang/design.md:475-477`

But not defined:

- ordering guarantees
- duplicate suppression
- same-actor conflict resolution
- per-tick action cap
- partial failure reporting
- sync vs async result collection

### 4.3 Success/failure observation is not defined

Wang asked: how do we know if a move command succeeded or failed?

The document does not say:

- whether GameAPI returns an ack
- whether success is inferred from later WorldModel position
- how long we wait before declaring timeout
- whether "blocked by pathing / target vanished" is a hard fail or soft retry

This blocks any robust implementation of managed tasks.

### 4.4 There is still a direct-call inconsistency

WorldModel invariant says Experts do not call `GameAPI` directly. `docs/wang/design.md:269-271`

But the design also says simple experts like `DeployExpert` may "直接调 GameAPI". `docs/wang/design.md:210-215`

This must be resolved. Either:

- experts only propose actions, and executor is the sole GameAPI caller
- or some experts are allowed to bypass the executor

Right now the design claims both.

### 4.5 Latency budget is not defined

Current proposed path is:

1. refresh world
2. detect events
3. tick experts
4. batch actions
5. execute batch
6. sleep 0.5-1s

`docs/wang/design.md:457-483`

If actions only leave the process once per loop, latency may already be `0.5-1s + refresh cost + execution cost`, which is acceptable for recon/economy but may be poor for combat reactions.

The design needs explicit latency targets per expert class.

## 5. State synchronization

### 5.1 WorldModel freshness is undefined

The API surface shows getters, but no freshness metadata. `docs/wang/design.md:248-267`

I need:

- snapshot timestamp
- last refresh duration
- last successful refresh tick
- stale/invalid flags

Otherwise experts cannot reason about whether their inputs are fresh enough.

### 5.2 Decision/action race is unhandled

The design assumes:

- expert reads world
- returns action
- executor sends action

But by then:

- target may be dead
- actor may be damaged
- resource may be preempted
- threat may have changed

The spec never defines precondition checks at execution time.

I would expect something like:

```python
ActionProposal(
    actor_id=57,
    action="move",
    target_pos=(1800, 450),
    world_version=1234,
    preconditions=["actor:57 alive", "resource lease valid"]
)
```

Right now that layer does not exist.

### 5.3 Locking/concurrency is impossible to reason about until execution model is fixed

If Section 10 wins (single-thread loop), locking is minimal.

If Section 4.3 wins (experts own threads/coroutines), then I need:

- world snapshot immutability rules
- kernel lock boundaries
- action queue thread safety
- dashboard read isolation

The design does not pick one model conclusively.

### 5.4 WorldModel ownership is underspecified

The document says:

- game state comes from GameAPI refresh
- runtime task/resource state is written by Kernel

`docs/wang/design.md:248-271`

But it does not define:

- whether WorldModel is mutable shared state or immutable snapshot replacement
- who owns writes to each subdomain
- whether `detect_events()` compares raw snapshots, normalized snapshots, or both

This matters for correctness and for debugging.

## 6. Edge cases in the scenario

### 6.1 Scout dies at t=20s: no concrete recovery policy

The task has `failure_condition="all_scouts_dead"`, but that still leaves multiple implementation choices:

- fail immediately
- ask Kernel for another scout
- downgrade to slower scout
- suspend until unit becomes available

The current design does not choose one.

### 6.2 New player command while recon is running is not specified

Critical missing policy:

- does new command append, supersede, or interrupt?
- who decides whether two directives compose?
- what if new command is "回家" or "取消探索"?

Without a command supersession policy, the command system is not implementable.

### 6.3 Competing commands for the same scout are not fully defined

The document mentions priority-based preemption, but not:

- whether user-originated commands always outrank background jobs
- whether recon keeps the unit unless a hard override occurs
- whether player-issued override creates a new job or mutates the existing one

### 6.4 `"取消探索"` is not representable enough yet

To implement cancellation, I need:

- how a cancel directive maps to an existing task/job
- whether cancel targets `task_id`, intent class, or currently selected unit
- what happens to partial outcomes
- whether resources are released immediately or after graceful release

None of that is defined.

### 6.5 Constraint interaction is still abstract

`Constraint` exists as a type, but the enforcement path is missing. `docs/wang/design.md:145-157`

I do not know:

- whether constraints are consulted by Kernel only
- whether experts also read them every tick
- how they modify scoring, pathing, or engagement rules
- whether they can be violated under emergency

So "别追太远" is still not implementable beyond a slogan.

## 7. Missing components

### 7.1 `main()` / composition root is still missing

Section 10 provides a loop sketch, but not the full startup order.

I still need a concrete initialization sequence like:

1. create `GameAPI`
2. create static unit data registry
3. create `WorldModel`
4. create `Kernel`
5. register expert factories
6. create `ActionExecutor`
7. create dashboard server
8. create interpreter/resolver/decomposer pipeline
9. start `GameLoop`

Without this, the design is not yet a bootable system.

### 7.2 Expert registry/factory is missing

Kernel is supposed to choose an expert from task intent. `docs/wang/design.md:176-181`, `docs/wang/design.md:206-213`

Missing:

- how experts are registered
- one instance per expert type vs one per job
- whether experts are reusable or stateful per job
- lifecycle ownership of expert instances

### 7.3 Dashboard payloads are underspecified

The design says:

- Vue 3
- dual mode
- three zones
- structured logs

`docs/wang/design.md:401-425`

But I still cannot implement backend/frontend integration because missing:

- websocket event names
- task board card schema
- world overview schema
- debug mode payloads
- log stream payloads beyond one generic dict
- historical replay API

The UI architecture is directionally right, but not contract-ready.

### 7.4 Structured logging schema is too small

Current log record:

```python
{timestamp, layer, task_id, expert, message, data}
```

`docs/wang/design.md:415-425`

Still missing:

- log level
- event type
- job_id
- actor_ids/resources involved
- world tick / world version
- correlation id for a player directive

Without those, debugging race conditions and task lineage will be painful.

## Spec mismatches and omissions

### A. `TaskSpec` example references `blocked_by`, but the dataclass does not contain it

The decomposer template says:

- `"做 X 然后做 Y" → 两个 TaskSpec，Y.blocked_by = X`

`docs/wang/design.md:120-127`

But `TaskSpec` has no `blocked_by` field. `docs/wang/design.md:107-118`

This is a direct schema inconsistency.

### B. `ExecutionJob` is too thin for runtime control

Missing fields that implementation will likely need:

- `priority`
- `task_kind`
- `expert_instance_id`
- `pending_resource_requests`
- `last_result`
- `failure_reason`
- `cancel_requested`

### C. `Outcome` is too thin for operational recovery

Missing:

- `job_id`
- `reason`
- `recoverable`
- `resource_changes`
- `followup_task_specs`

### D. `Constraint` is too thin to support real enforcement

Missing:

- precedence
- expiry
- source directive/task
- enforcement mode (`hard` vs `soft`)

## Minimum additional spec required before coding

If the goal is "implementation can start without inventing policy", I would require the design to add these concrete artifacts:

1. **Pick one runtime model**
   - either single-thread GameLoop ticks experts
   - or experts own loops
   - not both

2. **Define executable contracts**
   - `ResourceRequest`
   - `ActionProposal`
   - `ActionResult`
   - typed `SuccessCondition` / `FailureCondition`
   - typed `Event`

3. **Define normalized WorldModel schemas**
   - actor schema
   - resource binding schema
   - snapshot/version/freshness fields
   - refresh cadence rules

4. **Define Resolver algorithm**
   - candidate generation
   - scoring
   - disambiguation
   - conversational referent memory

5. **Define task-control semantics**
   - cancel
   - supersede
   - preempt
   - retry
   - wait queue
   - mid-task rebinding

6. **Define dashboard/backend contracts**
   - websocket event names
   - task payload schemas
   - log payload schemas
   - user mode vs debug mode data contracts

## Bottom line

The design is now strong enough to explain the intended architecture to a human. It is not yet strong enough to let an engineer implement it without making up critical behavior.

The most dangerous gaps are:

1. runtime ownership contradiction
2. missing action/resource/condition/event contracts
3. underspecified resolver algorithm
4. underspecified event → reaction path
5. missing cancellation/preemption/supersession semantics

Those should be closed before implementation starts. Otherwise the first implementation will silently become the real spec.
