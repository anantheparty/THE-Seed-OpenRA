# Minimal Tactical Action-Surface Audit (Post Actor Continuity)

Date: 2026-04-09  
Author: yu

Scope inspected:
- `openra_api/action/*`
- `openra_api/game_api.py`
- `experts/*`
- `experts/game_api_protocol.py`
- `task_agent/tools.py`
- `task_agent/handlers.py`
- `kernel/core.py`
- supporting docs: `docs/yu/slice4_hard_unit_ownership_action_surface_audit_20260409.md`, `docs/yu/openra_execution_board_20260409.md`

Constraint:
- No code changes in this audit.
- Goal is the smallest safe tactical control slice after task actor continuity landed.

## Bottom line

The next minimal tactical slice should be **`stop_units` as a first-class task tool/expert path**.

Why this is the right next slice:
- task-bound actor continuity now exists (`Kernel.task_active_actor_ids()` / handler autofill), so owned forces can finally be interrupted explicitly
- `GameAPI.stop(...)` already exists and is battle-tested through the Adjutant direct `stop_attack` path
- the current task runtime has **no ownership-based interrupt primitive**, so once a managed task has moved/attacked/scouted a force, the only tactical controls are "move somewhere else" or "start another attack/recon job"
- broader wrappers like `regroup` / `retreat` / `hold` either already exist in another form or require more semantics than this slice should take on

## What already exists

### 1. Ownership continuity is good enough for a narrow tactical slice

Already landed:
- `kernel/core.py`
  - `task_active_actor_ids(task_id)`
  - `task_has_running_actor_job(task_id)`
  - task actor group registry / wake-time handoff
- `task_agent/handlers.py`
  - `_default_actor_ids()`
  - `move_units`, `attack`, `scout_map` auto-reuse current task actor group when safe
- `models/configs.py`
  - `MovementJobConfig.actor_ids`
  - `ReconJobConfig.actor_ids`
  - `CombatJobConfig.actor_ids`

Implication:
- the runtime can now preserve a task-owned force across jobs
- the next missing capability is not allocation, but **interrupt/control**

### 2. A stop primitive already exists at the GameAPI level

Existing implementation:
- `openra_api/game_api.py:921` — `GameAPI.stop(self, actors)`

There is also a top-level direct path already using it:
- `adjutant/adjutant.py:1163` — `_handle_runtime_nlu_stop_attack(...)`
- `adjutant/adjutant.py:1185` — direct `self.game_api.stop(...)`

Implication:
- the system already trusts `stop` enough for direct player commands
- the gap is specifically that **task-owned units cannot use the same primitive through the task/expert surface**

### 3. The current task action surface is still asymmetric

Current task tools:
- `deploy_mcv`
- `scout_map`
- `produce_units`
- `request_units`
- `move_units`
- `attack`

Missing:
- `stop_units`

This asymmetry matters after actor continuity landed:
- movement/recon/combat can reuse a task-owned group
- but there is no equally first-class "cancel current motion/engagement now" control

## Root cause of the current controllability gap

The runtime still treats tactical control as:
- start recon
- start movement
- start combat

It does **not** yet treat interruption as a first-class owned-group action.

That is why the system still feels weaker than older direct Python control: once a task has a force, it can route and retarget it, but it cannot cleanly tell that exact force to stop without going through a top-level direct Adjutant stop path.

This is now the smallest meaningful gap.

## Recommended minimal slice

## Do now

### A. Add `stop_units` as a task tool

Exact targets:
- `task_agent/tools.py`
  - add `stop_units` tool definition
  - shape should mirror `move_units` / `attack`
  - `actor_ids` optional, omitted = reuse task active group

Recommended tool contract:
- `actor_ids?: list[int]`
- `unit_count?: int` only if you intentionally want generic fallback; safer first pass is **no `unit_count` at all**

Recommendation:
- keep it ownership-first
- if `actor_ids` omitted, handler should reuse task actor group via `_default_actor_ids()`
- if no active group exists, return a clean "nothing to stop" result instead of allocating generic actors

Why:
- stop is an interrupt primitive, not a unit-acquisition primitive
- letting it grab arbitrary idle actors would make semantics muddy

### B. Add a one-shot `StopExpert`

Exact targets:
- `models/configs.py`
  - add `StopJobConfig(actor_ids: Optional[list[int]] = None)`
- `experts/stop.py`
  - add `StopJob`
  - add `StopExpert`
- `experts/game_api_protocol.py`
  - add `stop(self, actors: List[Actor]) -> None`
- `experts/__init__.py`
  - export `StopExpert`, `StopJob`
- `main.py`
  - register `StopExpert` in expert map
  - add a concise `_describe_job()` branch for `StopExpert`

Recommended behavior:
- one-shot expert
- resource needs = exact `actor_ids`
- if no actors resolved -> fail cleanly
- call `game_api.stop(...)`
- emit immediate `TASK_COMPLETE(result="succeeded")`

Why this shape:
- keeps runtime observability/job history intact
- avoids bypassing Kernel/Task trace semantics
- narrower and safer than inventing a generalized posture system first

### C. Add handler wiring with active-group reuse

Exact targets:
- `task_agent/handlers.py`
  - `register_all()` add `stop_units`
  - add `handle_stop_units()`
  - reuse `_default_actor_ids()`

Recommended handler rule:
- if explicit `actor_ids` present -> pass through
- else -> use `_default_actor_ids()`
- if still none -> return a non-error explanatory payload (`ok=false`, `reason="no_active_actor_group"` or similar)

Why:
- aligns with the new ownership model
- avoids surprising "stop some random units" behavior

### D. Keep kernel changes minimal

Exact targets:
- `kernel/core.py`

Recommendation:
- do **not** redesign allocator logic for this slice
- optionally add a small `_infer_resource_needs()` branch for `StopExpert` only for parity
- do **not** add `StopExpert` to `task_has_running_actor_job()` long-running actor job set; it should remain a one-shot interrupt action

Important point:
- if `StopJob.get_resource_needs()` is implemented properly, Kernel changes can stay near-zero
- this slice should not touch reservation / future-unit logic at all

### E. Add focused tests only

Exact targets:
- `tests/test_tool_handlers.py`
- likely a new `tests/test_stop_expert.py` or extend an existing expert test file
- `tests/test_adjutant.py` only if you want to assert top-level stop and task-level stop semantics stay aligned

Recommended cases:
1. `stop_units` handler reuses task active actor group when safe
2. explicit `actor_ids` pass through unchanged
3. no active group -> no-op explanatory result
4. `StopExpert` emits immediate completion after `game_api.stop(...)`
5. task trace / runtime summary renders `StopExpert` sanely

## Defer

### 1. `regroup_units`

Why defer:
- not a true primitive yet
- regroup needs a policy: regroup to centroid? regroup to caller position? regroup while preserving facing/formation?
- without that policy, it becomes a vague alias for `move_units`

Can be revisited after `stop_units` lands.

### 2. `retreat_units` wrapper

Why defer:
- `move_units(move_mode="retreat")` already exists
- `CombatExpert` also already has retreat semantics via `retreat_threshold`
- a separate wrapper is mostly UX sugar right now, not a missing engine capability

Recommendation:
- only add a dedicated wrapper later if user-facing language or capability planning clearly benefits

### 3. `hold_units` / `hold_position`

Why defer:
- `attack(..., engagement_mode="hold")` already covers a real combat hold mode
- a true hold-position primitive should likely combine:
  - stop
  - local defensive posture
  - maybe a short leash/do-not-chase constraint
- that is more than a minimal slice

Recommendation:
- treat `hold` as a follow-on slice after `stop_units`, not as part of the same change

### 4. `group_handle` / squad abstraction

Why defer:
- actor continuity just landed
- introducing `group_handle` now would add a second abstraction before the first one has proven itself in live runtime

Recommendation:
- keep `actor_ids` as the ownership truth for now
- consider `group_handle` only after stop/regroup/hold semantics have stabilized

### 5. Broad action-surface redesign

Why defer:
- `openra_api/action/*` is still a thin imperative wrapper library
- the current runtime bottleneck is not missing 20 actions
- it is missing one clean ownership-based interrupt primitive

## Recommended implementation order

1. `task_agent/tools.py`
   - add `stop_units`
2. `models/configs.py`
   - add `StopJobConfig`
3. `experts/game_api_protocol.py`
   - add `stop(...)`
4. `experts/stop.py`
   - implement one-shot `StopExpert`
5. `experts/__init__.py`
   - export stop expert
6. `main.py`
   - register expert and summary label
7. `task_agent/handlers.py`
   - add `handle_stop_units()` with active-group reuse
8. tests
   - handler + expert focused regressions
9. optional parity cleanup
   - `openra_api/action/stop.py`
   - `openra_api/action/__init__.py`

## Notes on `openra_api/action/*`

The action layer should **not** drive this slice.

Reason:
- current runtime experts mostly call `GameAPI` directly
- adding `StopAction` alone would not make the task runtime more controllable

However, for wrapper parity and future direct-script usage, a thin:
- `openra_api/action/stop.py`
- export in `openra_api/action/__init__.py`

is reasonable as a secondary cleanup after the expert/tool path lands.

## Final recommendation

If only one tactical slice is taken next, it should be:

**`stop_units` + `StopExpert` + active-group reuse**

This is the smallest change that:
- materially improves controllability
- matches the new ownership model
- uses already-existing GameAPI capability
- avoids broad redesign
- and creates a clean base for later `hold/regroup/retreat` wrappers.
