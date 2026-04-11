# Slice 4 Audit — Hard Unit Ownership and Action Surface

Date: 2026-04-09

Scope inspected:
- `models/configs.py`
- `experts/recon.py`
- `experts/combat.py`
- `experts/movement.py`
- `task_agent/handlers.py`
- `task_agent/tools.py`
- `kernel/core.py`
- `openra_api/action/*`

Constraint:
- No code changes in this audit.
- Goal is the smallest viable path to hard unit ownership for recon/combat without overhauling the runtime.

## Bottom line

Slice 4 is **partially implemented already**.

The runtime already supports explicit `actor_ids` all the way through:
- config schemas
- tool schema
- handler parsing
- expert resource needs
- kernel fallback resource inference

So the smallest viable path is **not**:
- invent a new commander layer
- redesign the kernel
- introduce a full unit-group subsystem first

The smallest viable path is:
1. treat `actor_ids` as the first-class hard ownership primitive for `Recon` and `Combat`
2. make request/assignment results easier for task logic to reuse
3. add a very small `group_handle` indirection only after `actor_ids` flow is stable
4. expand action surface in a narrow, tactical way only after hard ownership lands

## What is already in place

### 1. Config layer already supports explicit ownership

`models/configs.py`
- `ReconJobConfig.actor_ids` already exists
- `CombatJobConfig.actor_ids` already exists
- `MovementJobConfig.actor_ids` already exists

This is the correct shape for the first ownership layer.

The main implication is:
- there is no need to redesign the config model before implementation
- `group_handle` can be added later as an optional convenience layer

### 2. Tool/handler surface already exposes `actor_ids`

`task_agent/tools.py`
- `scout_map` accepts `actor_ids`
- `move_units` accepts `actor_ids`
- `attack` accepts `actor_ids`

`task_agent/handlers.py`
- `handle_scout_map()` passes `actor_ids` into `ReconJobConfig`
- `handle_move_units()` passes `actor_ids` into `MovementJobConfig`
- `handle_attack()` passes `actor_ids` into `CombatJobConfig`

This means the LLM/tool layer is already capable of issuing explicit ownership-based jobs.

### 3. Experts already honor explicit actor ownership

`experts/movement.py`
- `MovementJob.get_resource_needs()` uses exact `actor_id` predicates when `actor_ids` is provided

`experts/recon.py`
- `ReconJob.get_resource_needs()` uses exact `actor_id` predicates when `actor_ids` is provided

`experts/combat.py`
- `CombatJob.get_resource_needs()` uses exact `actor_id` predicates when `actor_ids` is provided

So the job side is already doing the right thing for hard ownership.

### 4. Kernel fallback inference already understands explicit actor ownership

`kernel/core.py`
- `_infer_resource_needs()` already special-cases `ReconExpert`, `CombatExpert`, and `MovementExpert`
- when `actor_ids` is present, it creates one exact `ResourceNeed` per actor

This means hard ownership works even if a controller path falls back to kernel inference.

## What is still missing

The missing pieces are no longer "add `actor_ids`".
They are now about **continuity** and **ergonomics**.

### A. No durable task-level unit-group identity

Current state:
- jobs can be created with `actor_ids`
- `request_units` can wake a task with assigned `actor_ids`
- but there is no stable task-side handle for "this is my current recon/combat group"

Effect:
- a task can get units, but it has to keep reusing raw `actor_ids`
- there is no canonical place to patch/replace/swap those assignments over time
- chaining `recon -> move -> attack -> retreat` on the same force remains awkward

This is the real gap behind "hard unit ownership is not landed yet".

### B. `request_units` returns assignment, but the control-plane ergonomics are weak

Current state:
- `Kernel.register_unit_request()` can return fulfilled actor ids
- `_wake_waiting_agent()` resumes the task with `EventType.UNIT_ASSIGNED` and `actor_ids`
- unfulfilled requests / reservations are tracked in runtime state

But:
- there is no normalized task-facing "active unit group" field
- the agent still has to reason over raw returned ids
- there is no dedicated helper that turns assignment into a reusable control group abstraction

### C. Action surface is still too narrow for tactical ownership-based control

`openra_api/action/*` currently provides:
- move
- attack
- group
- build
- deploy

But what is still missing for practical hard ownership workflows:
- explicit stop/cancel action wrapper at the action layer
- narrow "regroup/retreat/hold-position" convenience actions
- a runtime-level abstraction that maps a logical group to the current actor set

This is not a blocker for Slice 4 minimal landing, but it is the next constraint after ownership is explicit.

### D. Recon/combat still default to generic allocation semantics when `actor_ids` is absent

Current fallback behavior:
- `Recon` asks for generic friendly actors
- `Combat` asks for generic `can_attack=true` actors

This is acceptable as fallback, but it means:
- ownership is hard only when callers explicitly provide `actor_ids`
- the system still defaults to "give me some suitable units" rather than "use my bound force"

For Slice 4, this is fine.
For later phases, the default should move toward a task-bound group handle.

## File-level recommendations

### `models/configs.py`
Recommendation:
- keep `actor_ids` exactly as the first ownership primitive
- do **not** add mandatory `group_handle` yet
- if added later, it should be optional and coexist with `actor_ids`

Reason:
- current schema is already sufficient for minimal hard ownership
- adding `group_handle` now without runtime support would create fake abstraction

### `experts/recon.py`
Recommendation:
- keep `actor_ids` path as the primary hard-ownership path
- do not redesign scouting logic first
- later add optional `group_handle -> actor_ids` resolution upstream, not inside the expert

Reason:
- the expert already respects explicit actor assignment
- the remaining problem is assignment continuity, not recon pathfinding semantics

### `experts/combat.py`
Recommendation:
- treat explicit `actor_ids` as authoritative whenever present
- keep generic `unit_count` fallback only as compatibility path
- later add more tactical action primitives only after ownership is stable

Reason:
- current combat logic is already ownership-capable
- the missing value is preserving the same force across successive jobs

### `experts/movement.py`
Recommendation:
- use `MovementExpert` as the reference pattern
- future `Recon`/`Combat` ownership semantics should follow movement's "exact ids if known, generic fallback otherwise" shape

Reason:
- this is already the cleanest implementation in the current runtime

### `task_agent/handlers.py`
Recommendation:
- no conceptual redesign needed
- keep `actor_ids` pass-through for `scout_map` and `attack`
- later add a tiny helper path so task logic can say "reuse currently assigned units" without restating raw ids every time

Reason:
- handler layer is already structurally correct
- the missing abstraction belongs above it, not inside it

### `task_agent/tools.py`
Recommendation:
- keep `actor_ids` in tool schemas
- later add clearer descriptions that explicit ids mean hard ownership
- do not expose `group_handle` until runtime semantics exist

Reason:
- current tool contract is already sufficient for minimal implementation
- adding a schema field before runtime meaning exists would confuse the agent

### `kernel/core.py`
Recommendation:
- do not overhaul resource arbitration for Slice 4
- keep `_infer_resource_needs()` behavior
- add the smallest possible task-bound group registry later, owned by Kernel

Desired minimal future responsibility:
- `task_id -> active_unit_group`
- stores current actor ids assigned to the task's control group
- allows patch/reuse across jobs

Reason:
- kernel already owns resource truth
- a tiny group registry fits naturally here without changing the allocator model

### `openra_api/action/*`
Recommendation:
- do not start with a broad action-surface redesign
- after hard ownership lands, add only the smallest tactical additions:
  - `StopAction`
  - maybe `RetreatAction` as a thin move wrapper
  - optionally reuse `GroupAction` only for game-side grouping, not as the core runtime ownership model

Reason:
- current issue is not "missing 30 tools"
- it is "the runtime cannot preserve a task's force cleanly across recon/combat jobs"

## Smallest viable implementation path

### Step 1 — Formalize `actor_ids` as the official hard ownership path

No runtime redesign.
Just standardize the rule:
- if a task knows its units, recon/combat/movement must use `actor_ids`
- generic `scout_count` / `unit_count` is fallback only

This step is mostly contract clarification plus tests.

### Step 2 — Add a kernel-owned task unit-group registry

Minimal addition:
- store `task_id -> actor_ids`
- update it when:
  - `request_units` is fulfilled
  - reservations produce assigned actors
  - tasks explicitly patch their force

Do **not** expose `group_handle` to the LLM first.
Keep the runtime API internal initially.

This gives the system one stable place to say:
- these are the units currently owned by this task

### Step 3 — Add "reuse current group" semantics above handlers

Minimal options:
- either TaskAgent context explicitly surfaces current bound actor ids
- or handler/bootstrap paths can resolve "use current task group" into explicit `actor_ids`

This avoids repeatedly asking the model to juggle raw actor ids from memory.

### Step 4 — Only then introduce optional `group_handle`

`group_handle` should be:
- a runtime convenience identifier
- resolved upstream into `actor_ids`
- not required by experts

Experts should still only need:
- `actor_ids`

That keeps the expert layer simple and deterministic.

### Step 5 — Expand tactical action surface narrowly

After hard ownership is stable:
- add `StopAction`
- add explicit regroup/retreat helpers if needed
- consider richer combat/recon control patterns

Do not block Slice 4 on this.

## Recommended migration order

1. **Contract cleanup**
   - treat `actor_ids` as authoritative hard ownership path
   - document generic count fields as fallback only

2. **Task-bound unit-group registry in Kernel**
   - smallest possible internal representation
   - no planner/commander overhaul

3. **Task-facing reuse path**
   - let tasks reuse the currently assigned group without manual actor-id bookkeeping

4. **Optional `group_handle`**
   - only after the registry exists

5. **Action surface expansion**
   - `stop`
   - narrow regroup/retreat helpers
   - only then broader tactical actions if still needed

## What not to do

- Do not introduce a new top-level commander abstraction for this slice.
- Do not redesign the resource allocator before ownership continuity exists.
- Do not make experts aware of `group_handle` first; keep them centered on explicit `actor_ids`.
- Do not block the slice on a large OpenRA action-surface overhaul.

## Practical conclusion

The good news is that Slice 4 is not starting from zero.
The bad news is that the remaining gap is subtle: the system can already control explicit units, but it cannot yet preserve that control cleanly across a task's whole lifecycle.

So the smallest viable path is:
- keep `actor_ids` as the hard truth
- add a tiny task-bound group registry in Kernel
- add reuse ergonomics above the handler layer
- postpone `group_handle` and broad tactical API expansion until after ownership continuity is real
