# OpenRA Action-Surface Next Slice Audit

Date: 2026-04-09  
Author: yu

Scope inspected:
- `openra_api/game_api.py`
- `openra_api/macro_actions.py`
- `openra_api/action/*`
- `experts/*`
- `experts/game_api_protocol.py`
- `task_agent/tools.py`
- `task_agent/handlers.py`
- `kernel/core.py`
- `OpenCodeAlert/Copilot/openra_ai/OpenRA_Copilot_Library/tests/Sample.py`
- prior notes: `docs/yu/minimal_tactical_action_surface_audit_20260409.md`, `docs/wang/archive/openra_control_research.md`

Constraint:
- No code changes in this audit.
- This audit assumes `stop_units` and hard unit ownership are already landing/landed.
- Goal: identify the **next minimal, high-value OpenRA action-surface additions** compared with prior direct-python control.

## Bottom line

After `stop_units` and hard unit ownership, the next minimal high-value action-surface slice should be:

1. **`repair_units` as a first-class ownership-based task tool/expert**
2. then **`set_rally_point` as a capability-owned production posture primitive**

Those two give the largest player-visible gain for the least semantic risk.

What should **not** be the next slice:
- a broad regroup/formation redesign
- a new planner layer
- camera/select/group UX actions
- engineer capture / occupy before the roster and ownership story are ready

## What the current task/runtime surface already exposes

Current task-facing action tools are:
- `deploy_mcv`
- `scout_map`
- `produce_units`
- `request_units`
- `move_units`
- `stop_units`
- `attack`

Current execution experts are:
- `DeployExpert`
- `EconomyExpert`
- `ReconExpert`
- `MovementExpert`
- `StopExpert`
- `CombatExpert`

Current runtime shape:
- `Kernel` already handles task/job lifecycle and actor ownership handoff
- `Movement` / `Recon` / `Combat` now have explicit actor ownership support
- `Adjutant` already has a top-level direct stop path

This means the system now has the minimum ownership/control substrate required for a few more narrow action slices.

## What prior direct-python control could do that the current stack still cannot

The older direct-python / direct-GameAPI control surface already had these concrete player-visible operations:

### 1. Send units to repair

Available in direct control:
- `openra_api/game_api.py` — `repair_units(...)`

Missing from current task/expert surface:
- no `repair_units` tool in `task_agent/tools.py`
- no handler in `task_agent/handlers.py`
- no `RepairExpert`
- no task/job/runtime semantics for “send my owned damaged units to repair now”

Player-visible effect missing today:
- “让这队坦克去修”
- “矿车回修”
- “血量低的单位先修理”

### 2. Set factory / airfield rally point

Available in direct control:
- `openra_api/game_api.py` — `set_rally_point(...)`

Missing from current task/expert surface:
- no tool
- no handler
- no expert/capability wrapper

Player-visible effect missing today:
- “兵营集结点设到前线”
- “车厂出兵直接去某处”
- “机场出机集结到指定点”

This matters because current production is still largely “produce, then later route”, rather than “set a stable spawn posture”.

### 3. Move by explicit waypoint path

Available in direct control:
- `openra_api/game_api.py` — `move_units_by_path(...)`
- `openra_api/action/move.py` already supports a `path`
- the old sample code used path-based exploration directly

Missing from current task/expert surface:
- no path-aware tool
- no path config
- no expert that treats a multi-waypoint path as a first-class order

Player-visible effect missing today:
- explicit sweep route
- deterministic corridor traversal
- scripted waypoint scouting

### 4. Engineer-style occupy / capture

Available in direct control:
- `openra_api/game_api.py` — `occupy_units(...)`

Missing from current task/expert surface:
- no tool
- no handler
- no expert

Player-visible effect missing today:
- “工程师占领建筑”
- “抢占油井/科技建筑”

Important caveat:
- this is not the next safest slice for the current simplified demo roster, because the present runtime emphasis is not on engineer/capture gameplay.

### 5. Explicit group/form-group operations

Available in direct control:
- `openra_api/game_api.py` — `form_group(...)`

Missing from current task/expert surface:
- no tool
- no handler
- no explicit expert

But this is lower priority now because:
- `Kernel` already has task-owned actor groups internally
- exported game-side numeric group IDs are not the current runtime’s ownership truth

### 6. Camera / select-unit actions

Available in direct control:
- `move_camera_to`
- `move_camera_by_location`
- `select_units`

These are intentionally not the next priority:
- they are debugging/operator conveniences
- not core control semantics for the AI runtime
- they do not materially increase autonomous or copilot execution ability

## Why `repair_units` is the best next slice

`repair_units` is the smallest high-value addition after `stop_units` because it is:

### 1. Already implemented at the GameAPI layer

No protocol invention is required at the OpenRA side:
- `openra_api/game_api.py` already supports `repair_units(...)`

### 2. Naturally ownership-based

It fits the new actor-ownership model cleanly:
- explicit `actor_ids` when known
- else reuse the task’s active actor group

Unlike production or capture, it does not need:
- allocator semantics
- queue semantics
- new planner logic
- future-unit ownership

### 3. Strongly player-visible

This fills a real “why is this runtime weaker than direct python?” gap:
- damaged tanks currently require awkward move/manual behavior
- `fix` is already part of the demo capability/buildability story
- repair is intuitively understandable to users and easy to demonstrate

### 4. Semantically narrow

This can remain a one-shot expert like `StopExpert`:
- claim exact actors
- call `repair_units(...)`
- succeed quickly

That keeps the slice safe.

## Why `set_rally_point` should be next after repair

`set_rally_point` is the best second slice because it improves long-running production behavior without inventing a new control brain.

Why it is valuable:
- makes produced units flow somewhere meaningful immediately
- reduces follow-up movement churn
- aligns naturally with capability-owned production posture
- is player-visible and easy to explain

Why it should be **after** repair:
- it needs clearer ownership over production buildings / airfields
- it belongs more to `Capability` than to ordinary tasks
- a rushed implementation could split “who owns production posture” between task and capability again

## Why `move_units_by_path` is useful but not first

It is real value, especially for:
- deterministic scouting routes
- corridor traversal
- repeatable sweeps

But it is not the first slice because:
- `ReconExpert` already provides a functioning exploration substrate
- path semantics need a bit more design:
  - one-shot path?
  - persistent patrol loop?
  - path abort/replan behavior?
- it is less immediately valuable than repair for the current demo/runtime maturity

Recommended position:
- third action-surface slice, after repair + rally

## Why `occupy_units` should wait

Even though `occupy_units(...)` exists in direct control, it should not be the immediate next slice.

Reasons:
- current simplified runtime focus is not engineer capture play
- it introduces target-type and legality semantics
- it likely wants stronger preconditions:
  - target classification
  - engineer ownership
  - building capture success/failure semantics

Recommended position:
- defer until engineer/capture gameplay is active in the legal roster and task language

## Recommended implementation order

## Slice A — `repair_units`

### Player-visible intent
- “修理这队坦克”
- “受伤单位回修”
- “矿车先去修”

### Safest file targets
- `models/configs.py`
  - add `RepairJobConfig`
- `experts/game_api_protocol.py`
  - add `repair_units(...)`
- `experts/repair.py`
  - add `RepairJob` and `RepairExpert`
- `experts/__init__.py`
  - export `RepairExpert`
- `task_agent/tools.py`
  - add `repair_units` tool
- `task_agent/handlers.py`
  - add `handle_repair_units()`
  - reuse active task actor group when explicit ids are absent
- `main.py`
  - register `RepairExpert`
  - include a short job description branch for diagnostics/task list
- tests
  - `tests/test_tool_handlers.py`
  - new `tests/test_repair_expert.py`
  - maybe one kernel/runtime integration assertion if needed

### Recommended semantics
- one-shot job
- exact actor ownership only
- no generic actor acquisition
- fail cleanly if:
  - no owned actors
  - no repair facility available
- succeed once repair order is issued, not after full heal

### Why this is safe
- mirrors the `StopExpert` pattern
- no queue/allocation interaction
- no new long-running policy loop

## Slice B — `set_rally_point`

### Player-visible intent
- “兵营集结点设到这里”
- “车厂集结到前线”
- “机场出机往这个方向集合”

### Safest file targets
- `models/configs.py`
  - add `RallyJobConfig`
- `experts/game_api_protocol.py`
  - add `set_rally_point(...)`
- `experts/rally.py`
  - add `RallyJob` and `RallyExpert`
- `task_agent/tools.py`
  - add `set_rally_point`
- `task_agent/handlers.py`
  - handler should require explicit production-building actor ids at first
- `main.py`
  - register `RallyExpert`
- capability-facing plumbing:
  - likely `adjutant/adjutant.py`
  - maybe `task_agent/agent.py` only for capability tasks, not ordinary tasks

### Recommended semantics
- capability-owned or explicit-actor-only first
- do not let ordinary managed tasks discover production buildings and set rally freely
- start with barracks / war factory / airfield only

### Why this should follow repair
- it is slightly wider semantically
- it crosses into production ownership
- it belongs on the capability path, not generic task path

## Slice C — path movement

### Player-visible intent
- “按这条路线侦察”
- “沿路推进”
- “穿过这些点位”

### Safest file targets
- `models/configs.py`
  - either extend `MovementJobConfig` or add `PathMoveJobConfig`
- `experts/movement.py`
  - path-aware movement job, or a dedicated `PathMoveExpert`
- `task_agent/tools.py`
  - add a narrow `move_units_by_path`
- `task_agent/handlers.py`
  - path handler with explicit actor ownership

### Recommended semantics
- one-shot path traversal
- no patrol loop in first version
- use explicit waypoint list

### Why third
- useful, but not as immediately high-value as repair
- needs more semantics than stop/repair

## What not to prioritize next

### 1. `form_group`

Do not prioritize game-native group ids next.

Reason:
- internal task actor groups already exist
- game numeric groups are not the current runtime ownership contract

### 2. camera / select actions

Do not prioritize for the runtime.

Reason:
- debugging/operator convenience only
- low leverage for actual copilot execution

### 3. `occupy_units`

Defer until engineer/capture gameplay is a real active lane.

### 4. broad regroup/hold/guard redesign

Do not jump there yet.

Reason:
- `stop_units` and `attack(... hold)` already cover part of the space
- proper regroup/guard semantics need a bit more policy design

## Final recommendation

After `stop_units` and hard unit ownership, the safest next action-surface sequence is:

1. **`repair_units`**
2. **`set_rally_point`**
3. **`move_units_by_path`**
4. **`occupy_units`** only when engineer/capture becomes active again

That order best matches:
- current ownership model
- current capability boundaries
- direct player-visible value
- minimal implementation risk

The key rule is:

> Prefer narrow ownership-based actions that already exist in `GameAPI`, and only then widen the tactical language.

