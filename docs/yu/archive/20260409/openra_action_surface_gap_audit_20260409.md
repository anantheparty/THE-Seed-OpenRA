# OpenRA Action-Surface Gap Audit (Direct Python Control vs Current Copilot)

Date: 2026-04-09  
Author: yu

Scope inspected:
- `openra_api/game_api.py`
- `openra_api/action/*`
- `openra_api/macro_actions.py`
- `openra_api/jobs/*`
- `experts/*`
- `task_agent/tools.py`
- `task_agent/handlers.py`
- `task_agent/agent.py`
- `adjutant/runtime_nlu.py`
- `main.py`

Constraint:
- No code changes in this audit.
- Goal is to identify concrete missing actions/permissions that make the current copilot feel weaker than direct Python control, then propose the smallest safe next implementation slice.

## Bottom line

The current copilot is no longer missing basic movement/interrupt/control primitives.
Those have largely landed:
- path movement
- stop
- repair
- rally point
- area attack
- ownership-based actor reuse

The most important remaining action-surface gap is now:

**precise target control on owned units**

In practice that means:
- direct Python can attack a specific visible unit (`GameAPI.attack_target`)
- the current copilot can mostly attack an area (`attack(target_position=...)`)
- this makes the copilot feel less sharp in real combat even after actor continuity landed

So the smallest next slice should shift from "more actions" to **one precise action**:

**`attack_actor` / focus-fire on a specific visible target actor**

## What direct Python control can do today that the copilot still cannot do cleanly

## 1. Precise target attack

Existing low-level support:
- `openra_api/game_api.py:847` — `attack_target(attacker, target)`
- `openra_api/action/attack.py` — `AttackAction`
- `openra_api/jobs/attack.py` already uses exact target ids in the old direct-control stack

Current copilot surface:
- `task_agent/tools.py` exposes only `attack(target_position, ...)`
- `task_agent/handlers.py` only builds `CombatJobConfig(target_position=...)`
- `models/configs.py` has no `target_actor_id` combat/strike config
- `experts/combat.py` is area-oriented FSM combat, not exact target execution

Player-facing effect:
- cannot cleanly say "打这个矿车/这台 V2/这个 MCV"
- even when enemy actor ids are visible, the copilot has to convert them into a position and let combat logic infer the rest
- this feels weak compared with direct Python control, especially for sniping, focus fire, and harassment

Assessment:
- **High impact**
- **Low-to-medium implementation risk**
- should be the next slice

## 2. Occupy / garrison / capture

Existing low-level support:
- `openra_api/game_api.py:826` — `occupy_units(occupiers, targets)`

Current copilot surface:
- no tool
- no handler
- no expert
- no NLU direct route

Player-facing effect:
- no explicit engineer capture / oil derrick capture / building garrison path from the copilot runtime
- if the simplified roster excludes engineers right now, this is not an immediate demo blocker
- but it is a real control-surface gap versus direct Python control

Assessment:
- **Medium impact** overall
- **Lower immediate value** if the current playable roster still excludes capture-centric units
- should come after precise target attack

## 3. Explicit posture wrappers (hold/regroup/retreat)

Current state:
- `move_units(move_mode="retreat")` already exists
- `attack(engagement_mode="hold")` already exists
- `stop_units` now exists
- task-owned actor reuse exists

What is still missing is not raw capability, but clean player/runtime semantics:
- regroup to where?
- hold with what leash?
- retreat by vector, by base anchor, or by rally position?

Assessment:
- **Real UX gap**, but more semantic than mechanical
- not the smallest next slice
- better treated after precise target attack lands

## 4. Game-side grouping / selection / camera control

Existing low-level support:
- `form_group`
- `select_units`
- camera movement

Why they should stay out for now:
- `Kernel` already owns the runtime truth for task-bound units
- game-native groups would create a second ownership system
- `select_units` and camera are UI/player affordances, not core copilot control primitives

Assessment:
- **Defer**
- do not expose these just because direct Python can use them

## 5. Production queue micromanagement for ordinary tasks

Existing low-level support:
- `query_production_queue`
- `manage_production`
- `place_building`

Why they should stay out for now:
- this would cut across the capability boundary
- ordinary tasks should not gain direct queue mutation powers again
- current roadmap explicitly moves shared production toward capability / reservation ownership

Assessment:
- **Intentional non-exposure**, not a missing action-surface bug

## What has already landed and should no longer be treated as a gap

The following were previously missing but are now present in the current runtime:

- `move_units_by_path`
  - `task_agent/tools.py`
  - `task_agent/handlers.py`
  - `experts/movement.py`
- `stop_units`
  - `task_agent/tools.py`
  - `task_agent/handlers.py`
  - `experts/stop.py`
- `repair_units`
  - `task_agent/tools.py`
  - `task_agent/handlers.py`
  - `experts/repair.py`
- `set_rally_point`
  - capability-only by design
  - `task_agent/tools.py`
  - `task_agent/handlers.py`
  - `experts/rally.py`

This matters because the action-surface problem has changed shape:
- it is no longer “we only have move and attack”
- it is now “we still lack precision and some game-specific tactical verbs”

## Prioritized gap list

### Priority 1 — Precise target strike / focus fire

Recommended runtime concept:
- `attack_actor` or `focus_fire_units`

Why first:
- largest immediate controllability gain in real combat
- builds directly on actor continuity
- uses existing `GameAPI.attack_target(...)`
- does not require capability redesign
- does not create a second group system

Exact repo targets:
- `task_agent/tools.py`
  - add `attack_actor` tool
- `task_agent/handlers.py`
  - add `handle_attack_actor()`
  - default to task active actor group when `actor_ids` omitted
- `models/configs.py`
  - add a tiny config, e.g. `StrikeJobConfig(actor_ids: Optional[list[int]], target_actor_id: int)`
- `experts/`
  - add `strike.py` or `focus_fire.py`
  - one-shot exact-target expert
- `experts/game_api_protocol.py`
  - already sufficient (`attack_target` exists)
- `experts/__init__.py`
  - export it
- `main.py`
  - register expert and add summary formatting
- tests
  - handler reuse of active actor group
  - explicit actor_ids pass-through
  - one-shot successful attack order emission

Implementation shape:
- **one-shot expert**, not a new combat FSM
- no generic actor acquisition when no active group exists
- explicit visible target actor id required

This is the smallest next slice.

### Priority 2 — Occupy / capture / garrison

Recommended runtime concept:
- `occupy_target`

Exact repo targets:
- `task_agent/tools.py`
- `task_agent/handlers.py`
- `models/configs.py`
- new `experts/occupy.py`
- `main.py`
- tests

Why second, not first:
- game-specific and valuable
- but less broadly useful than precise strike in the current simplified runtime
- capture/garrison semantics depend on roster/map mode more heavily

### Priority 3 — Posture wrappers on top of existing primitives

Potential wrappers:
- `hold_units`
- `regroup_units`
- `retreat_to`

Why third:
- the raw mechanics already exist in fragments (`stop`, `attack hold`, `move retreat`)
- what is missing is policy clarity, not raw engine capability
- these should be designed after precise strike is available

## Permissions / routing gaps (not pure action gaps)

A second, smaller issue is that some already-landed actions are still not fast-routed at the Adjutant/NLU layer.

Examples worth revisiting later:
- simple move / retreat phrasing
- repair commands
- rally-point commands

Files involved:
- `adjutant/runtime_nlu.py`
- `adjutant/adjutant.py`

This is real, but it is a **routing latency issue**, not an action-surface gap.
It should not be the next slice unless simple-command responsiveness becomes the immediate priority.

## Do now / defer

## Do now

Implement **precise target strike** as the next minimal slice.

Working name:
- `attack_actor`
- or `focus_fire_units`

Recommended order:
1. `models/configs.py`
2. `experts/strike.py`
3. `experts/__init__.py`
4. `main.py`
5. `task_agent/tools.py`
6. `task_agent/handlers.py`
7. focused tests

Reason:
- biggest combat-control gain per line changed
- complements actor continuity directly
- avoids broad redesign

## Defer

Defer for now:
- `occupy_target`
- `regroup_units`
- `hold_units`
- `retreat_to` wrapper
- `form_group`
- `select_units`
- camera controls
- production queue mutation exposure outside capability

## Final recommendation

The current copilot no longer mainly suffers from "too few actions" in the abstract.
It now suffers from **missing precision**.

Therefore the next action-surface slice should be:

**ownership-based precise strike on a visible target actor**

not another broad wave of wrappers.
