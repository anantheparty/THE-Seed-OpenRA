# OpenRA Execution Board

Date: 2026-04-09  
Author: yu

This board translates the current architecture direction into implementation tracks for the active OpenRA runtime.

It is intentionally execution-first:
- what to build,
- in what order,
- what each track owns,
- and what each track must not swallow.

---

## 0. Guiding Constraints

These constraints are currently locked.

- `Adjutant` remains the top-level coordinator / front door.
- No near-term standalone `Commander` runtime layer.
- `Kernel` stays deterministic and should not absorb more free-form planning.
- `Capability` owns shared production and prerequisite control.
- ordinary managed tasks request execution resources; they do not plan production.
- `TaskAgent` continues shrinking into a bounded local reasoner.

---

## 1. Track A — Adjutant as Battlefield Coordinator

### Goal

Turn `Adjutant` from “front-door router” into a stronger battlefield coordinator without creating a second top-level command layer.

### Owns

- player ingress
- command disposition
- query / reply / cancel / info injection
- player-visible output normalization
- high-level routing into:
  - direct execution
  - capability
  - bounded managed tasks

### Must not own

- low-level resource arbitration
- shared production queue mutation
- detailed execution loops

### Open work

- curated top-level battle snapshot
- better disposition model (`new / merge / override / interrupt / inject-info`)
- stronger player/task dialogue unification
- cleaner top-level statefulness across commands

### Files most likely involved

- `adjutant/adjutant.py`
- `adjutant/runtime_nlu.py`
- `main.py`
- `task_agent/context.py`
- `world_model/core.py`

---

## 2. Track B — Capability Ownership

### Goal

Make `Capability` the true owner of shared production and prerequisite control.

### Owns

- production/prerequisite planning
- queue-aware build/produce decisions
- broad economy/tech directives
- capability-level waiting/blocking state

### Must not own

- battlefield micro
- direct move/attack/recon execution
- all player interaction

### Open work

- explicit capability contract
- bounded phases for broad directives
- blocker semantics
- unified capability memory/state
- clearer separation from ordinary managed tasks

### Files most likely involved

- `kernel/core.py`
- `experts/economy.py`
- `queue_manager.py`
- `task_agent/agent.py`
- `task_agent/context.py`
- `world_model/core.py`

---

## 3. Track C — Future-Unit Reservation / Allocator

### Goal

Separate present-resource binding from future-resource ownership.

### Owns

- `UnitRequest -> Reservation -> Production -> Assignment`
- future-unit priority policy
- production ownership
- cancellation/reclaim semantics

### Must not own

- ordinary task reasoning
- direct battlefield execution

### Open work

- reservation objects / statuses
- allocator policy
- assignment of produced units to waiting tasks
- cancellation and reclaim behavior
- integration with capability-owned production

### Files most likely involved

- `kernel/core.py`
- `models/`
- `experts/economy.py`
- `queue_manager.py`
- `world_model/core.py`

---

## 4. Track D — Hard Unit Ownership + Action Surface

### Goal

Make recon/combat operate on persistent owned groups and expand OpenRA control primitives until the runtime no longer feels weaker than direct Python control.

### Owns

- squad/group identity
- recon/combat ownership semantics
- action-surface growth
- missing tactical primitives

### Must not own

- top-level command disposition
- production planning
- generalized strategic routing

### Open work

- `actor_ids` or `group_handle` for recon/combat
- reinforcement/rebinding semantics
- richer tactical primitives
- better control modes and posture changes

### Files most likely involved

- `models/configs.py`
- `experts/recon.py`
- `experts/combat.py`
- `experts/movement.py`
- `task_agent/handlers.py`
- `openra_api/action/*`
- `openra_api/jobs/*`

---

## 5. Cross-Cutting Track — Truth, Knowledge, and Debugging

This track is not one subsystem. It is the layer that prevents all four tracks from drifting apart.

### Owns

- one coherent gameplay knowledge truth
- consistent buildability/dependency derivation
- runtime facts separation (ordinary vs capability)
- developer replay / triage UX

### Open work

- unify knowledge sources
- remove dependency/buildability drift
- improve task/session summaries
- make replay and triage faster

### Files most likely involved

- `unit_registry.py`
- `openra_state/data/*`
- `experts/knowledge.py`
- `world_model/core.py`
- `logging_system/*`
- `web-console-v2/src/components/*`
- `main.py`

---

## 6. Implementation Order

This is the current recommended order.

### Step 1
Track A: strengthen `Adjutant`

### Step 2
Track B: make `Capability` truly own production/prerequisites

### Step 3
Track C: add reservation / allocator for future units

### Step 4
Track D: hard unit ownership and richer action surface

### Step 5
Cross-cutting truth / knowledge / replay UX cleanup

This order is important:
- `Adjutant` decides correctly first
- `Capability` owns production next
- allocator then makes ownership explicit
- hard unit ownership then makes battlefield control trustworthy
- replay/knowledge cleanup then improves iteration and correctness across all tracks

---

## 7. Immediate Integration Tasks

These are the practical next actions before code-heavy refactors begin.

1. finish subagent audits on Tracks A-D
2. extract one concrete implementation slice for each track
3. choose only one or two code tracks to implement in parallel
4. keep top-level docs aligned with runtime truth
5. avoid new architecture layers until Track A and Track B are substantially complete
