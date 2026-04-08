# OpenRA Parallel Audit Synthesis

Date: 2026-04-09  
Author: yu

This document consolidates the four parallel audit tracks:

1. Adjutant as top-level coordinator
2. Capability ownership over production/prerequisites
3. Future-unit reservation / allocator
4. Recon / combat hard unit ownership and action-surface gaps

The goal is to turn those audits into one actionable implementation sequence for the active OpenRA runtime.

---

## 0. One-Sentence Synthesis

The system is already on the right architectural path, but it is still missing the runtime contracts that make it feel mature:

- `Adjutant` is already the real top-level coordinator and should be strengthened rather than bypassed.
- `Capability` must become the explicit owner of shared production and prerequisites.
- `Kernel` needs a true future-unit reservation/allocator layer.
- `Recon` and `Combat` need hard unit ownership and a richer action surface.

The missing pieces are not “more LLM”.  
They are **ownership, contracts, and execution semantics**.

---

## 1. Track A — Adjutant

### Current truth

`Adjutant` is already the live top-level coordinator:
- player ingress
- NLU/rule routing
- query / reply / cancel
- capability merge
- stale-world fail-closed handling
- player-visible response formatting

It is not just a thin chat wrapper anymore.

### Main gap

It is still too thin as a battlefield controller.

Missing:
- a stronger curated battlefield snapshot
- better disposition semantics
- better player/task dialogue unification
- more stateful top-level coordination

### Most important implication

Do **not** introduce a near-term separate `Commander` layer.

The correct move is to make `Adjutant` stronger in:
- battle-state awareness,
- disposition,
- and top-level coordination.

### Best next implementation slices

1. Add a richer top-level battle snapshot for `Adjutant`
2. Improve `new / merge / override / interrupt / info` disposition logic
3. Unify task/player communication through `Adjutant`
4. Keep shrinking ordinary `TaskAgent`
5. Make `Adjutant` the explicit owner of top-level battlefield context, not just command routing

---

## 2. Track B — Capability Ownership

### Current truth

Capability exists directionally, but not yet as a mature ownership layer.

The system already moved ordinary managed tasks away from direct production planning, but the runtime contract is still incomplete.

### Main gap

Production/prerequisite ownership is still fragmented across:
- `Kernel`
- `EconomyJob`
- `QueueManager`
- world facts
- prompt policy

### Most important implication

The main leakage is no longer “ordinary tasks directly build buildings”.

The real leakage is:
- weak ownership of shared production,
- weak queue provenance,
- and mixed reasoning/control around future production.

### Best next implementation slices

1. Make capability-owned production state explicit
2. Make queue ownership and capability waiting/blocking states explicit
3. Keep capability-only buildability/prerequisite reasoning out of ordinary task context
4. Move toward one capability contract for:
   - what it owns,
   - what it can request,
   - what counts as blocked,
   - what ends a phase
5. Make queue repair behavior subordinate to capability-owned semantics, not a separate production brain

---

## 3. Track C — Future-Unit Reservation / Allocator

### Current truth

The low-level control surface is not the blocker.

The current runtime already supports:
- unit requests,
- idle-unit binding,
- bootstrap production,
- later assignment of produced units.

### Main gap

The missing thing is a first-class future-unit ownership lifecycle.

Current flow is:
- request
- maybe idle claim
- maybe bootstrap production
- later produced actor appears
- later assignment happens

But there is no strong object tying that together as a reservation with ownership.

### Most important implication

This is why the runtime still feels weaker than direct Python control:
- not because it cannot issue low-level commands,
- but because it cannot cleanly own the next produced unit before it exists.

### Best next implementation slices

1. Introduce `UnitReservation`
2. Introduce `ReservationStatus`
3. Add reservation state to `Kernel`
4. Make `EconomyJob` execute reservations rather than acting as the ownership layer
5. Treat `QueueManager` as health/repair logic, not allocator logic
6. Expose reservation state to diagnostics and task context

---

## 4. Track D — Recon / Combat Ownership and Action Surface

### Current truth

The runtime already has a broader low-level action surface than the task layer currently exposes.

Important asymmetry:
- `Movement` already supports explicit `actor_ids`
- `Recon` and `Combat` still mainly operate as resource requests

### Main gap

The system still does not let recon/combat reliably mean:

> control **my** units / squad

It still often means:

> give me some suitable units

This is why long-lived battlefield control still feels weak.

### Most important implication

The next leap in OpenRA operability is not another planner. It is:
- hard unit ownership,
- group/squad identity,
- and a richer execution surface.

### Best next implementation slices

1. Add `actor_ids` to `ReconJobConfig` and `CombatJobConfig`
2. Add optional `group_handle` above raw actor ids
3. Make recon/combat `get_resource_needs()` precise when ownership is known
4. Add a lightweight group registry
5. Expand action primitives:
   - regroup
   - disengage / stop attack
   - path move
   - better repositioning
   - explicit build/place actions where needed

---

## 5. Combined Interpretation

These four tracks fit together cleanly.

### What should happen first

1. Strengthen `Adjutant`
2. Make `Capability` truly own production/prerequisites
3. Add reservation/allocator for future units
4. Add hard unit ownership and richer tactical action surface

### Why this order matters

- `Adjutant` must decide and route cleanly before deeper runtime ownership makes sense
- `Capability` must own shared production before reservation logic can be stable
- reservation/allocator must exist before future units can belong to squads/tasks safely
- hard unit ownership then unlocks a stronger battlefield runtime without needing another general LLM layer

---

## 6. Concrete Next Implementation Slices

These are the best immediate slices, in order.

### Slice 1 — Adjutant Battlefield Snapshot + Disposition

Files likely involved:
- `adjutant/adjutant.py`
- `adjutant/runtime_nlu.py`
- `world_model/core.py`
- `task_agent/context.py`

### Slice 2 — Capability Contract and Phase-Bounded Broad Commands

Files likely involved:
- `kernel/core.py`
- `experts/economy.py`
- `task_agent/agent.py`
- `task_agent/context.py`
- `world_model/core.py`

### Slice 3 — Reservation Model in `models/` + `kernel/`

Files likely involved:
- `models/*`
- `kernel/core.py`
- `experts/economy.py`
- `queue_manager.py`

### Slice 4 — Recon/Combat `actor_ids` + Group Registry

Files likely involved:
- `models/configs.py`
- `experts/recon.py`
- `experts/combat.py`
- `experts/movement.py`
- `kernel/core.py`

### Slice 5 — Action-Surface Expansion

Files likely involved:
- `task_agent/handlers.py`
- `openra_api/action/*`
- `openra_api/jobs/*`
- related expert modules

---

## 7. Final Decision

The system does **not** need:
- a near-term standalone `Commander`,
- or a return to broad “one free LLM brain per task.”

The system **does** need:
- stronger `Adjutant`,
- real capability ownership,
- explicit reservation/allocator semantics,
- hard battlefield unit ownership,
- and a better execution surface.

That is the path from “good prototype with many fixes” to a coherent OpenRA runtime.
