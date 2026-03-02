# Yu Investigation Report

Date: 2026-03-29
Author: yu

Roadmap first impression after reading `docs/rts_agent_system_roadmap.md`:

- The roadmap wants a thin-but-strong Kernel with explicit lifecycle, preemption, resource arbitration, and formal outcomes.
- The current codebase has useful pieces in place, but they are still split into two different stacks:
  - `openra_api/jobs/*` is a thin long-running job layer bound to explicit actors.
  - `openra_state/intel/*` plus `agents/strategy/*` is a separate strategic stack with its own blackboard-like world summary.
- The biggest architectural gap is that there is no single shared execution/kernel contract joining jobs, world model, and expert instances.

## Task 1: Job System Deep Dive

Files read:

- `openra_api/jobs/manager.py`
- `openra_api/jobs/base.py`
- `openra_api/jobs/attack.py`
- `openra_api/jobs/explore.py`
- `openra_api/macro_actions.py`
- `main.py`

### 1. Current Job lifecycle

Current lifecycle is:

1. Job object is instantiated (`AttackJob`, `ExploreJob`) and registered with `JobManager.add_job`.
2. Actors are explicitly bound to a job through `assign_actor_to_job` or `assign_actor_id_to_job`.
3. Background loop calls `JobManager.tick_jobs()` roughly once per second in `main.py`.
4. `tick_jobs()` refreshes assigned actors via `api.update_actor`, marks dead ones, and unassigns them before job execution.
5. `tick_jobs()` groups live actors by `job_id` and calls `job.tick(ctx, actors)`.
6. `Job.tick()` transitions `pending -> running` on first tick, then delegates to `_tick_impl`.
7. Each concrete job computes per-actor `ActorAssignment` records and issues commands if cooldown allows.
8. Job remains `running` indefinitely unless:
   - code sets `status` manually to `paused` / `completed`, or
   - `_tick_impl` raises and `Job.tick()` converts that to `failed`.

Important detail: there is no built-in admission/binding/completion phase in manager logic. The roadmap's proposed states like `admitted`, `binding`, `waiting`, `blocked`, `superseded`, `aborted`, `partial_succeeded` do not exist yet.

### 2. Can a Job fail? How is failure handled?

Yes, but only in a narrow sense.

- Base `Job.tick()` wraps `_tick_impl` in `try/except`.
- Any uncaught exception sets:
  - `status = failed`
  - `last_error = str(exc)`
  - `last_summary = "失败: ..."`
- After that, later ticks return immediately because failed jobs are terminal in base `tick()`.

What does not count as failure today:

- No assigned actors.
- Missing map info.
- Move command returns `ok=False`.
- Target actor lookup fails.
- Attack command returns false.

Those cases mostly degrade into summaries and retries rather than terminal failure.

Per-job specifics:

- `AttackJob` is very forgiving. Missing targets, missing actor positions, or failed `attack_target` calls do not fail the job; they just reduce issued commands.
- `ExploreJob` is also forgiving. Failed movement increments `_move_fail_streak`, increases cooldown, and can force retargeting after repeated failures, but does not mark the job failed.

So: failures are exception-driven, not outcome-driven.

### 3. Can Jobs be preempted? Can actors be reassigned mid-job?

Actors can be reassigned mid-job. Jobs themselves cannot be formally preempted.

What exists:

- `assign_actor_to_job()` will automatically detach an actor from its old job before binding it to the new one.
- Detach path calls `old_job.on_unassigned(actor_id)`, which clears per-actor assignment state.
- `MacroActions.dispatch_explore()` and `dispatch_attack()` rely on this explicit assignment model.
- `MacroActions.stop_attack()` can explicitly stop units and unassign them from `attack`.

What does not exist:

- No job-level preemption state like `superseded` or `aborted`.
- No manager-side reason tracking for why reassignment happened.
- No resource arbitration layer deciding that a higher-priority job steals actors.
- No automatic replacement/rebinding when a running job loses critical actors.

Special case:

- `AttackJob.set_externally_controlled(True)` is a soft handoff. The job stays alive and still owns actors, but stops issuing commands and reports that strategy has taken over. This is not real preemption; it is "ownership retained, execution suppressed."

### 4. Is there any priority system?

Not in the job system.

- `JobManager` stores jobs in a plain list and ticks them in insertion order.
- Actor assignment is "one actor -> one job" with last assignment winning.
- There is no numeric priority, no priority hint, no queue, no arbitration, and no conflict resolution beyond overwrite-on-rebind.

There are priority-like ideas elsewhere in the codebase:

- Economy logic has internal urgency ordering.
- Strategy prompt talks about company weights and strategic importance.

But none of that is integrated into `JobManager`.

### 5. What happens when assigned actors die?

Manager handles death as unassignment, not job failure.

- `tick_jobs()` calls `api.update_actor(actor)` for every assigned actor.
- If update fails or returns false, actor is marked dead.
- Dead actors are immediately passed through `unassign_actor()`.
- `unassign_actor()` removes manager-side mappings and calls `job.on_unassigned(actor_id)`.
- Base `on_unassigned()` removes `assignments[actor_id]`.
- `ExploreJob.on_unassigned()` also clears `_scout_state` and `_move_fail_streak`.

Result:

- The job survives actor death.
- If all actors die, job usually remains `running` and reports "暂无分配..." on later ticks.
- There is no automatic job completion/failure when required actors are gone.

### Concrete behavior of the two shipped jobs

#### AttackJob

- Input state:
  - uses `IntelService.get_snapshot()`
  - uses `IntelService.get_intel()`
- Command style:
  - visible enemies -> explicit `attack_target`
  - no visible enemies -> `MoveAction(..., attack_move=True)` toward threat direction
- Internal state:
  - per-actor `ActorAssignment`
  - `_advance_anchor`
  - `_externally_controlled`
- Completion:
  - none
- Failure:
  - exception only

#### ExploreJob

- Input state:
  - uses `IntelService.get_map_info()`
  - reads `MapWidth`, `MapHeight`, `IsExplored`
- Command style:
  - `MoveAction(..., attack_move=False)` toward chosen unexplored targets
- Internal state:
  - per-scout `_ScoutState`
  - `_move_fail_streak`
- Retarget logic:
  - sticky target until reached or considered stuck
  - random-ray search with expanding radius and dropping unexplored threshold
  - corner patrol fallback
- Completion:
  - none
- Failure:
  - exception only; movement failures become retry/backoff

### Bottom line for Task 1

The current job layer is best described as:

- explicit actor binding
- periodic per-tick command synthesis
- lightweight per-actor local state
- weak lifecycle semantics

It is not yet the roadmap's Kernel. It is a useful execution substrate, but not a scheduler/arbitrator/outcome system.

## Task 2: IntelService + WorldModel Gap

Files read:

- `openra_api/intel/service.py`
- `openra_api/intel/model.py`
- `openra_state/intel/zone_manager.py`
- `openra_state/intel/intelligence_service.py`
- `openra_api/rts_middle_layer.py`

There are actually two intel layers in the repo:

1. `openra_api.intel.IntelService`
   - summary/caching layer returning `IntelModel`
   - used by midlayer/jobs
2. `openra_state.intel.IntelligenceService`
   - zone/blackboard updater
   - used by `StrategicAgent`

They overlap conceptually but are not unified.

### 1. What game state does IntelService already abstract vs what's raw?

#### Already abstracted in `openra_api.intel.IntelService`

It already lifts raw API state into a fairly rich derived model:

- Snapshot abstraction:
  - `my_actors`
  - `enemy_actors`
  - `base_info`
  - timestamp
- Caching / TTL:
  - snapshot cache
  - map cache
  - production queue cache
  - unit attribute cache
- Economy abstraction:
  - cash/resources
  - power summary
  - refinery / power plant / war factory counts
  - miner counts
  - income-rate estimate
  - production queue summaries
  - queue block reason
- Tech abstraction:
  - inferred tech level
  - buildable buildings / trainable units probe
  - owned key buildings
- Force abstraction:
  - counts by type / category
  - estimated army value
  - anti-air / anti-armor estimates
  - centroids
  - enemy threat list
  - enemy last-seen memory
- Battle abstraction:
  - nearby threat / target summaries
  - unit attribute driven battle section
- Map abstraction:
  - explored ratio
  - frontier points
  - nearby unexplored points
  - coarse resource summary
  - coarse map_control section
- Alert abstraction:
  - low power
  - no refinery / miners / barracks
  - queue blockage
  - anti-air shortage
  - army weakness
  - scout stalled
- Activity/order abstraction:
  - `actors_actions` summarizes visible actor activities and resolved orders

#### Still mostly raw or thinly wrapped

- Raw actor objects are still passed around widely.
- No unified ownership/occupancy/resource reservation state.
- Map semantics are still primitive:
  - frontier points
  - nearby unexplored
  - simple resource centroid
  - no chokepoints, ramps, watchpoints, expansions, lanes
- Enemy knowledge is mostly "last seen", not maintained hypotheses.
- There is no explicit task/world state join:
  - no active jobs in intel
  - no executor instances
  - no task outcomes
  - no actor-to-job reservations inside world model

### 2. What's missing for a "Shared World Model"?

From roadmap perspective, these are the major gaps.

#### Missing A: execution/runtime state

Roadmap says world model should include current jobs, executor instances, constraints, and resource occupancy.

Current status:

- `IntelModel` has none of these.
- `JobManager` keeps actor bindings separately.
- Strategy stack keeps company state separately.
- Economy keeps queue decisions separately.

This is the biggest missing layer.

#### Missing B: robust area control

Current map control is partial and split:

- `openra_api.IntelService` has a coarse `map_control` section.
- `ZoneManager` has stronger spatial constructs:
  - resource zones
  - base ownership
  - per-zone combat strength
  - zone neighbors
  - visibility/exploration status

But there is still no full area-control model with:

- control confidence / contest level
- front line evolution
- safe corridor vs danger corridor
- travel-time-aware influence
- persistent control history

#### Missing C: threat zones

Current threat information is pointwise or zone-local:

- enemy threats near base
- per-zone enemy strength

Missing:

- continuous threat fields
- artillery/AA/surface threat layers
- approach risk maps
- "entering this region with this squad is unsafe" style query support

#### Missing D: hypotheses

Roadmap asks for enemy hypotheses and strategic uncertainty.

Current status:

- `IntelMemory.enemy_last_seen` is a memory cache, not a hypothesis system.
- There is no candidate enemy base inference, no tech path inference, no hidden-army posterior, no uncertainty scoring.

#### Missing E: strategic memory

Current memory is short-horizon and tactical:

- last snapshot time
- last explored ratio
- queue caches
- enemy last seen

Missing:

- "enemy expanded here before"
- "this flank was repeatedly attacked"
- "we lost first push because AA was missing"
- persistent doctrine/adaptation memory

#### Missing F: unification between the two intel stacks

There is a design split:

- `openra_api.intel.IntelService` returns `IntelModel` for jobs/midlayer.
- `openra_state.intel.IntelligenceService` updates a blackboard with `ZoneManager` and economic fields for strategy.

That means there is no single shared world model object that all experts read/write.

### 3. How do different agents currently access IntelService?

#### Jobs / midlayer path

- `RTSMiddleLayer` creates one `IntelService` instance and stores it as `self.intel_service`.
- `MacroActions` receives that same intel instance.
- `setup_jobs()` creates `JobManager(api=api, intel=mid.intel_service)`.
- So the human-side jobs and the midlayer share one `openra_api.intel.IntelService` instance.

This is the closest thing to a shared instance today.

#### Strategy path

- `StrategicAgent` does not use `openra_api.intel.IntelService`.
- It creates its own `openra_state.intel.IntelligenceService(self.state_api, self.intel_sink)`.
- It then reads blackboard data from `self.intel_sink.data`.

So strategy uses a separate intel implementation and separate state object.

#### Combat path

- `CombatAgent` does not read either intel service directly.
- It gets state from:
  - `UnitTracker`
  - `SquadManager`
  - direct spatial enemy scans via `GameClient._send_request("query_actor", ...)`

#### Economy path

- `EconomyEngine` is pure decision logic over `EconomyState`.
- `EconomyAgent` observes directly from `GameAPI` through `EconomyState.update(self.game_api)`.
- It does not read either intel service directly.

#### EnemyAgent path

- `EnemyAgent` does not use intel services directly.
- It calls `executor._observe()` for textual game state and `executor.run(command)` for action execution.

### Bottom line for Task 2

The repo already has meaningful state abstraction, but not a true shared world model.

Current reality is:

- one summary/cache intel model for jobs and LLM midlayer
- one zone/blackboard intel model for strategy
- direct raw API access in combat/economy/enemy

That is exactly the architectural gap the roadmap is trying to close.

## Task 3: Agent Interface Audit

Files read:

- `agents/enemy_agent.py`
- `agents/combat/combat_agent.py`
- `agents/economy/engine.py`
- `agents/economy/agent.py` (supporting wrapper, needed to understand `engine.py` in use)
- `agents/strategy/strategic_agent.py`
- `agents/combat/squad_manager.py` (supporting interface context)

### 1. What's the common interface pattern?

There is no single common interface across these agents.

#### EnemyAgent

- lifecycle:
  - `start()`
  - `stop()`
- execution loop:
  - internal background thread
  - periodic `_tick()`
- state exposure:
  - `get_state()`
  - `reset()`
  - `set_interval()`

This is an autonomous threaded agent.

#### CombatAgent

- lifecycle:
  - `start()`
  - `stop()`
- execution loop:
  - internal `_agent_loop()`
  - per-company tactical cycle threads
- command interface:
  - `set_company_order(company_id, order_type, params)`
  - `set_tactical_enhancer(...)`

This is also autonomous/threaded, but subordinate to strategy.

#### StrategicAgent

- lifecycle:
  - `start()`
  - `stop()`
- execution loop:
  - top-level `while self.running`
  - repeated `_strategy_loop()`
- side-effect control:
  - `enable_economy()`
  - `disable_economy()`
  - `set_controlled_actor_ids(...)`

Also autonomous/threaded, orchestrating sub-agents.

#### EconomyEngine

- no `start/stop`
- pure function-style interface:
  - `decide(state) -> List[Action]`

This is not an agent loop. It is a decision engine embedded inside `EconomyAgent`.

Conclusion:

- Common pattern does not exist at contract level.
- Three modules (`EnemyAgent`, `CombatAgent`, `StrategicAgent`) are long-running threaded controllers.
- `EconomyEngine` is stateless-ish domain logic called from another agent.

### 2. How do they get game state?

#### EnemyAgent

- gets a textual observation from `self.executor._observe()`
- does not use structured intel services directly

#### CombatAgent

- gets ally state from `UnitTracker` / `SquadManager`
- gets enemy state from direct `query_actor` requests near a target position
- does not use `IntelService` / `IntelligenceService`

#### EconomyEngine

- does not touch game state itself
- expects already-prepared `EconomyState`
- in practice `EconomyAgent.tick()` populates that via `self.state.update(self.game_api)`

#### StrategicAgent

- gets strategic state from `openra_state.intel.IntelligenceService`
- reads results from `intel_sink.data`:
  - map size
  - zone manager
  - cash/resources/power
  - timestamps
- also reads squad state from `SquadManager`

Conclusion:

- Strategy is the only audited agent using an explicit intel service.
- Combat and economy still rely on direct or near-direct API/state wrappers.
- EnemyAgent relies on a text observation channel instead of structured world state.

### 3. How do they issue commands?

#### EnemyAgent

- indirect command path
- produces one Chinese command string
- sends it to `command_runner(command)` or `executor.run(command)`
- actual execution then happens downstream via executor/midlayer

This is the most LLM-mediated command interface.

#### CombatAgent

- direct low-level API commands through `GameClient`
- examples:
  - `attack_move(ids, target_pos)`
  - `_send_request("move_actor", ...)`
  - `attack([attacker_id], [target_id])`

This is the most direct tactical command path.

#### EconomyEngine

- outputs domain `Action` objects only
- no direct GameAPI calls
- practical execution happens in `EconomyAgent._execute_actions()`, which calls:
  - `game_api.produce(...)`
  - `game_api.deploy_units(...)`

#### StrategicAgent

- does not directly move units in most cases
- emits high-level orders to `CombatAgent.set_company_order(...)`
- can enable companies and update company weight through `SquadManager`
- economy control is delegated to `EconomyAgent`

Conclusion:

- No unified command abstraction exists.
- Command layers differ by module:
  - EnemyAgent: natural-language command -> executor
  - Strategy: company order API
  - Combat: direct socket/game commands
  - Economy: action objects executed by wrapper agent

### 4. Do any of them track their own "task" state?

Yes, but each does it differently.

#### EnemyAgent

- tracks:
  - `_tick_count`
  - `_last_action_summary`
  - `_battle_mood`
  - `_battle_signals`
  - player-message history
  - taunt cooldown state

This is not task state in a formal execution-job sense, but it is local conversational/behavioral context.

#### CombatAgent

- explicitly tracks tactical task state in `company_states`
- per company:
  - `status` (`combat` / `relocate`)
  - `params`
  - `strategic_target_pos`
  - `is_processing`
  - relocate timing / stability fields
- also buffers pending strategic commands in `pending_orders`

This is the strongest local task-state implementation among the audited agents.

#### EconomyEngine

- tracks macro decision state:
  - `build_sequence`
  - `build_order_index`
  - `economy_matured`
  - target ratios

This is policy progression state, not runtime task objects.

#### StrategicAgent

- tracks:
  - `running`
  - controlled actor whitelist
  - user command file/default command
  - references to sub-systems
- the actual actionable task state is mostly externalized to:
  - `SquadManager`
  - `CombatAgent.company_states`
  - `intel_sink.data`

Conclusion:

- Yes, several agents track their own task-like state.
- But this state is fragmented and private, not represented in a shared kernel/world model.

## Cross-cutting conclusions

### What is already good substrate for the redesign

- Explicit actor binding in `JobManager` is a solid starting point for resource ownership.
- `AttackJob` and `ExploreJob` already follow the roadmap's "no while loop inside executor" principle.
- `ZoneManager` is a good seed for area-centric world modeling.
- `CombatAgent` already behaves like an execution expert with local runtime state.
- `EconomyEngine` already behaves like a planner/policy expert rather than a low-level executor.

### Main mismatches vs roadmap

1. There is no shared world model. There are at least three state planes:
   - `IntelModel`
   - `intel_sink.data + ZoneManager`
   - private agent state (`company_states`, job bindings, economy flags)
2. There is no kernel with formal lifecycle/outcomes/preemption.
3. Strategy, combat, economy, jobs, and enemy control all use different interfaces.
4. Execution ownership and observational state are not unified.

### Practical implications for redesign

- The existing job system should probably be treated as an execution substrate, not the final kernel.
- `CombatAgent.company_states` is a strong reference point for executor-instance state.
- `ZoneManager` should likely be promoted into the shared world model instead of staying strategy-only.
- `IntelService` and `IntelligenceService` should probably be merged or wrapped behind one world-model facade, otherwise the architecture will keep splitting by subsystem.

## Short answers for architecture design

If you need compressed answers for direct reuse:

- Job lifecycle today: create -> register -> explicit actor bind -> periodic tick -> indefinite running unless exception/manual status.
- Job failure today: uncaught exception only.
- Preemption today: actor reassignment yes, job-level supersession no.
- Priority today: none in `JobManager`.
- Actor death today: auto-unassign, job survives.
- Intel abstraction today: decent tactical/economic summaries, weak runtime/shared-world semantics.
- Shared world model gaps: execution state, control/threat fields, hypotheses, strategic memory, unified state plane.
- Agent interface today: heterogeneous; no common contract.
- State access today: strategy uses zone-blackboard intel, jobs use `IntelService`, combat/economy mostly use direct API wrappers.
- Command issuance today: enemy via executor text commands, strategy via company orders, combat via direct API, economy via `Action` objects executed by wrapper.

