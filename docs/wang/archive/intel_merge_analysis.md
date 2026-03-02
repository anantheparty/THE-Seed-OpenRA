# Intel Merge Analysis

Date: 2026-03-29
Author: yu

Goal of this note: compare the two existing intel stacks and define the minimum WorldModel facade that could unify them without jumping into implementation.

Relevant code read:

- `openra_api/intel/service.py`
- `openra_api/intel/model.py`
- `openra_api/rts_middle_layer.py`
- `openra_state/intel/intelligence_service.py`
- `openra_state/intel/zone_manager.py`
- `agents/strategy/strategic_agent.py`
- `openra_api/jobs/manager.py`

## Executive summary

The repo does not have one intel system. It has two overlapping systems optimized for different consumers:

1. `openra_api.intel.IntelService`
   - optimized for cached snapshots and derived summaries
   - consumed by jobs/midlayer
2. `openra_state.intel.IntelligenceService`
   - optimized for periodic zone updates into a blackboard sink
   - consumed by strategy

They should not be treated as duplicates. Each has meaningful capabilities the other lacks.

The clean merge target is not "delete one and keep one." It is:

- one `WorldModel` facade
- one shared runtime-owned state surface
- two internal provider layers during migration

## 1. What `IntelligenceService` provides that `IntelService` does not

`openra_state.intel.IntelligenceService` contributes the strategy-facing spatial/blackboard layer.

### A. Zone graph and zone objects

The biggest unique asset is `ZoneManager`:

- resource-zone discovery from map resources
- zone centers, radius, bounding boxes
- zone neighbor topology
- zone owner classification
- per-zone my/enemy/ally combat strength
- per-zone structure counts
- per-zone `is_visible` / `is_explored`

`IntelService` has some map summaries, but not a persistent zone object model.

### B. Blackboard-style publishing

`IntelligenceService` publishes named fields into a sink:

- `map_width`
- `map_height`
- `zone_manager`
- `player_info`
- `cash`
- `resources`
- `total_funds`
- `power`
- `screen_info`
- `last_updated`

This blackboard shape is what `StrategicAgent` currently reads.

`IntelService` returns a single `IntelModel` object, but does not expose this blackboard update pattern.

### C. Explicit periodic map/unit refresh cadence

`IntelligenceService` has its own timing model:

- map updates every 10s
- unit updates every 2s
- fog refresh per zone

`IntelService` has TTL caches, but not this strategy-oriented world refresh loop.

### D. Multi-faction actor collection for zoning

`IntelligenceService` gathers:

- `己方`
- `敌方`
- `友方`
- `中立`

and uses that broader set to update zones and ownership.

`IntelService` mainly organizes around my/enemy snapshot views and base info.

### E. Better support for area-control reasoning

If the architecture wants "Shared World Model" to be area-centric, `ZoneManager` is currently the closest starting point in the repo.

## 2. What `IntelService` provides that `IntelligenceService` does not

`openra_api.intel.IntelService` contributes the richer summary/derivation layer.

### A. TTL caching and cache discipline

Unique strengths:

- snapshot cache
- map cache
- queue cache
- attribute cache

This is more mature and reusable than the strategy-side refresh logic.

### B. Structured `IntelModel`

`IntelService` already emits a cross-domain summary object with sections:

- `meta`
- `economy`
- `tech`
- `forces`
- `battle`
- `opportunities`
- `map_control`
- `alerts`
- `legacy`
- `actors_actions`

`IntelligenceService` has no equivalent unified derived model.

### C. Better economy/tech/queue abstraction

Unique or stronger here:

- production queue summarization
- queue blockage detection
- income rate estimate
- tech probes via `can_produce`
- owned key building summary
- anti-air / anti-armor estimates

Strategy-side blackboard publishes raw-ish `cash/resources/power`, but not these richer derivations.

### D. Enemy memory and tactical summaries

Unique or stronger here:

- `IntelMemory.enemy_last_seen`
- threat list around base
- force centroids
- opportunities
- battle section
- `actors_actions` with activity/order summaries

These are closer to what jobs and tactical experts need.

### E. Existing integration with midlayer and jobs

This stack is already shared by:

- `RTSMiddleLayer`
- `MacroActions`
- `JobManager`
- `AttackJob`
- `ExploreJob`

That makes it the current execution-facing intel source.

## 3. Overlap and mismatch

There is substantial conceptual overlap:

- both query map data
- both query actors
- both derive strategic state from raw API
- both maintain their own refresh/caching discipline

But the shapes differ.

### `IntelService` shape

- request/response
- pull-based
- derived-summary-first
- optimized for consumers that want one snapshot object now

### `IntelligenceService` shape

- background tick
- push-to-blackboard
- zone-model-first
- optimized for a long-running strategy loop

This is why they currently coexist instead of one obviously replacing the other.

## 4. Recommended merge principle

The minimum merge should be a facade merge, not an immediate implementation merge.

Meaning:

- Define one `WorldModel` surface for all consumers.
- Internally, allow adapters over both existing stacks during transition.
- Gradually move derived logic and zone logic under that facade.

This avoids a risky "rewrite intel first" step.

## 5. Minimal WorldModel facade that wraps both

Below is the smallest useful facade I can justify from current code.

### A. Raw-state access

The facade should expose:

- `snapshot()`
  - my actors
  - enemy actors
  - base info
  - timestamp
- `map_info()`
  - raw `MapQueryResult`

Reason:

- execution experts and adapters still sometimes need near-raw state
- this should centralize fetching/caching instead of each expert calling `GameAPI`

### B. Derived intel access

The facade should expose:

- `intel()`
  - equivalent to today's `IntelModel`, or a dict view of it

Reason:

- jobs and execution/planner experts need battle/economy/tech/opportunity summaries

### C. Spatial/zone access

The facade should expose:

- `zones()`
  - iterable of zone records
- `zone_manager()`
  - transitional direct access for existing strategy code
- `get_zone_for_position(location)`

Reason:

- strategy currently depends on zone-centric state
- future world model should support area-control and threat-zone queries

### D. Runtime/task/resource access

This is the missing part not owned by either intel stack today, but the facade must include it or it will not become the roadmap's shared world model.

The facade should expose:

- `task_records()`
- `get_task(job_id)`
- `resource_bindings()`
  - actor -> task
  - queue -> task
  - squad -> task
- `constraints()`

Reason:

- roadmap explicitly wants jobs/executors/resources inside the world model
- if the facade omits this, it will just be another intel wrapper

### E. Memory/hypothesis access

The facade should expose:

- `memory()`
  - enemy last seen
  - strategic notes/history
- `hypotheses()`
  - enemy base candidates
  - tech-path guesses
  - hidden-force estimates

Today only `enemy_last_seen` really exists. But the facade should reserve the slot now.

### F. Tick/update orchestration

The facade should expose:

- `refresh(force=False)`
- `now()`

Reason:

- one place decides whether to use cached summary data, refresh map zones, or update task bindings

## 6. Suggested facade shape by responsibility

If I rewrite the merge as responsibilities instead of methods:

### WorldModel should own

- raw game snapshot
- derived intel summary
- zone graph / area control state
- runtime task records
- resource bindings
- memory / hypotheses
- unified refresh cadence

### Experts should not own privately anymore

- their own incompatible copies of world state
- ad hoc actor ownership maps
- isolated strategic blackboards

## 7. Migration-friendly wrapper plan

Without writing production code yet, the safest merge path looks like this:

### Phase 1: facade only

- Add a `WorldModel` facade object.
- Internally delegate:
  - summary methods -> `IntelService`
  - zone methods -> `IntelligenceService` / `ZoneManager`
  - runtime binding methods -> new lightweight task/resource registry

This is mostly an integration layer.

### Phase 2: pull strategy onto facade

- Replace direct reads of `intel_sink.data` in `StrategicAgent` with facade methods.
- Keep `ZoneManager` internal but accessible through the facade.

### Phase 3: pull jobs/combat/economy onto facade

- Make jobs and new experts consume `WorldModel` instead of raw `IntelService`.
- Remove direct `GameAPI` reads from expert layers unless they are adapter-only operations.

### Phase 4: consolidate internals

- Decide whether to keep one implementation and retire the other:
  - either embed zone logic into the `openra_api.intel` line, or
  - promote a new `world_model` package and demote both current intel modules behind adapters

I would strongly favor the second option. Both current intel modules are already shaped by old consumers.

## 8. Practical recommendation

If we want minimum disruption:

- Keep `IntelService` as the source of cached raw/derived summary data.
- Keep `ZoneManager` as the source of zone graph and area-control primitives.
- Stop exposing `IntelligenceService` blackboard fields directly to strategy code.
- Introduce one facade that wraps both and adds the missing runtime task/resource layer.

This gives the roadmap the correct architectural direction without forcing a premature rewrite.

## 9. Compressed answers

### What does `IntelligenceService` have that `IntelService` lacks?

- zone graph
- zone ownership/strength/visibility state
- blackboard publishing
- explicit map/unit refresh loop
- broader multi-faction world collection for spatial reasoning

### What does `IntelService` have that `IntelligenceService` lacks?

- disciplined TTL caching
- unified `IntelModel`
- richer economy/tech/queue derivations
- enemy memory and tactical summaries
- current integration with jobs/midlayer

### What is the minimal merge target?

A `WorldModel` facade that exposes:

- raw snapshot/map access
- derived intel summary
- zone access
- runtime task/resource bindings
- memory/hypotheses
- unified refresh orchestration

If the facade does not include task/resource state, it will unify intel but still fail the roadmap's "Shared World Model" requirement.

