# Adjutant Coordinator — Next Minimal Slice

Date: 2026-04-09  
Author: yu

Scope of audit:
- `adjutant/*`
- `main.py`
- `world_model/core.py`
- `kernel/core.py`
- `task_agent/context.py`

Goal:
- strengthen `Adjutant` as the real top-level coordinator,
- without introducing a separate `Commander` layer,
- and without turning `Adjutant` into another execution engine.

---

## 0. Short conclusion

The main gap is not that `Adjutant` has no battlefield reasoning path.  
The main gap is that **the runtime already computes several coordinator-useful facts, but `Adjutant` still sees only a thin subset of them**.

Current reality:
- `Adjutant` already owns player ingress, NLU/rule routing, query/reply/cancel, info merge, capability merge, stale fail-closed behavior, and player-facing output.
- `WorldModel` already computes rich `runtime_facts`.
- `Kernel` already syncs active-task state, capability state, reservations, and task-owned actor groups into `WorldModel`.
- `main.py` already derives a good task-level `triage` summary for the dashboard.

But:
- `Adjutant._battlefield_snapshot()` still reads only coarse `world_summary` counts.
- `Adjutant._build_context()` still gives classification only `task_id / label / raw_text / status`.
- `Adjutant` does **not** currently consume:
  - `capability_status`
  - `unit_reservations`
  - `active_group_size`
  - task `triage`
  - `info_experts` threat/base-state output

So the next slice should be a **context-plumbing and disposition-tightening slice**, not a new planner layer.

---

## 1. Missing Battlefield Snapshot / Disposition Information

## 1.1 Top-level battlefield snapshot is too coarse

Current source:
- `Adjutant._battlefield_snapshot()`

Current fields are mostly:
- self/enemy unit counts
- combat values
- idle unit count
- low power / queue blocked
- explored %
- enemy bases / enemy spotted / frozen count
- derived `disposition` and `focus`

What is missing for a real top-level coordinator:
- current base shape:
  - `has_construction_yard`
  - `mcv_count`
  - `mcv_idle`
  - `power_plant_count`
  - `refinery_count`
  - `war_factory_count`
  - `radar_count`
  - `repair_facility_count`
  - `harvester_count`
- current execution capacity:
  - `active_group_size` for the focused task
  - whether we currently have free combat units vs fully committed units
- capability state:
  - whether capability is idle / busy / blocked
  - whether there are pending unit requests
  - whether there are bootstrap jobs or reservations in flight
- richer threat state:
  - `base_under_attack`
  - `threat_level`
  - `threat_direction`
  - enemy composition summary

These fields already exist in pieces:
- `WorldModel.compute_runtime_facts()`
- `WorldModel.info_experts`
- `Kernel._sync_world_runtime()`

But `Adjutant` does not consume them.

---

## 1.2 Active-task view is too thin for disposition

Current source:
- `Adjutant._build_context()`

Current task entries only carry:
- `task_id`
- `label`
- `raw_text`
- `status`

This is not enough for `new / merge / override / interrupt / info` decisions.

What is missing:
- `is_capability`
- active domain hint:
  - economy / combat / recon / general
- task `triage.state`
- task `triage.phase`
- task `triage.status_line`
- `waiting_reason`
- `blocking_reason`
- `active_expert`
- `active_group_size`
- whether the task is:
  - waiting for player
  - waiting for capability
  - blocked by runtime
  - already owning a group of units

Important current asymmetry:
- `main.py::_build_task_triage()` already computes exactly the kind of summary that `Adjutant` needs.
- but that summary is only pushed to the dashboard payload, not back into `Adjutant` classification/disposition context.

---

## 1.3 Capability state is present in runtime, absent in Adjutant

Current source of truth:
- `Kernel._sync_world_runtime()` writes `capability_status`
- `WorldModel.compute_runtime_facts()` exposes `capability_status`

Current fields already available:
- capability task id / label / status
- active capability job count
- active capability job types
- pending request count
- bootstrapping request count

`Adjutant` currently ignores all of this.

Effect:
- top-level economy/tech commands are routed/merged by regex + NLU, not by a real capability availability view
- `Adjutant` cannot answer simple top-level questions like:
  - is capability already busy?
  - should this be merged into capability or treated as a new battlefield task?
  - is the system waiting on production or on player input?

---

## 1.4 Threat/awareness information exists, but is not part of top-level command disposition

Current source of truth:
- `WorldModel.compute_runtime_facts()["info_experts"]`

Potentially useful existing info:
- `threat_level`
- `threat_direction`
- `enemy_count`
- `base_under_attack`
- `base_health_summary`
- `has_production`

Current problem:
- `Adjutant._battlefield_snapshot()` only infers pressure from coarse counts and low power
- it does not use the existing `info_experts` layer

Effect:
- top-level interruption / merge decisions are weaker than they should be
- urgent battlefield inputs still depend too much on freeform classification instead of bounded state

---

## 1.5 There is no explicit top-level “coordination snapshot” object

Right now, the coordinator view is reconstructed in multiple partial places:
- `Adjutant._battlefield_snapshot()`
- `Adjutant._build_context()`
- `main._build_task_triage()`
- `WorldModel.compute_runtime_facts()`
- `Kernel._other_active_tasks_for()`

This increases drift risk.

Minimal missing abstraction:
- not a new agent
- not a new planner
- just one explicit **Adjutant-facing snapshot** assembled from existing truth

---

## 2. The Minimum Next Slice

This should stay narrow.

## 2.1 Add one explicit Adjutant-facing coordination snapshot

Add one structured view for `Adjutant`, sourced from existing runtime truth:

Suggested fields:
- `battlefield`
  - `disposition`
  - `focus`
  - `base_under_attack`
  - `threat_level`
  - `threat_direction`
  - `enemy_bases`
  - `enemy_spotted`
  - `frozen_enemy_count`
- `base_state`
  - `has_construction_yard`
  - `mcv_count`
  - `mcv_idle`
  - `power_plant_count`
  - `refinery_count`
  - `war_factory_count`
  - `radar_count`
  - `repair_facility_count`
  - `harvester_count`
  - `low_power`
  - `queue_blocked`
- `capability`
  - `status`
  - `active_job_types`
  - `pending_request_count`
  - `bootstrapping_request_count`
- `tasks`
  - top-level per-task summary entries with `triage`

This should be treated as the **single top-level coordinator input**, not another planning layer.

---

## 2.2 Feed richer active-task summaries into `Adjutant._build_context()`

Minimal change in spirit:
- keep `active_tasks`
- but upgrade each entry from:
  - `label/raw_text/status`
- to:
  - `label`
  - `raw_text`
  - `status`
  - `is_capability`
  - `domain`
  - `triage.state`
  - `triage.phase`
  - `triage.status_line`
  - `triage.waiting_reason`
  - `triage.blocking_reason`
  - `triage.active_expert`
  - `active_group_size`

Why this is the right minimum:
- the dashboard triage work is already pointing in the same direction
- this improves top-level reasoning without changing execution semantics

---

## 2.3 Tighten disposition logic with a few deterministic guards

Do not build a general strategic planner yet.

Do add a few top-level deterministic rules:

### Guard A — economy/production commands
- if the command is economy/production shaped and capability exists:
  - merge to capability first
- if capability is already blocked/waiting on production:
  - do not create a parallel managed task

### Guard B — battlefield urgent info
- if `base_under_attack` or `threat_level` is high and the input is combat-ish info:
  - prefer `interrupt` or `merge` to the active combat/defense path

### Guard C — player follow-up to waiting tasks
- if there is a task in:
  - `waiting_player`
  - `waiting_units`
  - `blocked`
- and the player input matches its domain:
  - prefer merge/info injection instead of creating a new task

These are not a new “Commander brain”.  
They are minimal coordinator rules built on top of existing runtime state.

---

## 2.4 Reuse existing runtime state; do not invent another memory system yet

The next slice should consume:
- `WorldModel.compute_runtime_facts()`
- `Kernel._sync_world_runtime()`
- `main._build_task_triage()`

It should **not** introduce:
- a new long-lived strategic memory object
- another planner prompt
- a second orchestration agent

---

## 3. Do First / Do Not Do Yet

## 3.1 Do first

### First
- create one Adjutant-facing coordination snapshot from existing runtime truth

### Second
- enrich `Adjutant._build_context()` active-task entries with triage-like fields

### Third
- add a very small set of deterministic disposition guards using:
  - capability state
  - top threat state
  - task triage state

### Fourth
- update classification/query prompts to explicitly consume the new snapshot fields

These are all “small coordinator strengthening” work, not architecture churn.

---

## 3.2 Do not do yet

### Do not add a standalone Commander layer
That would duplicate `Adjutant` responsibility before the current top layer is even fed correctly.

### Do not move resource arbitration out of Kernel
This slice is about coordination context, not execution ownership.

### Do not turn Adjutant into a low-level executor
It should route, merge, interrupt, query, and summarize.
It should not directly own battle micro loops.

### Do not add a full workflow engine first
No task-graph/planner refactor yet.
Top-level context and disposition quality is still the smaller, safer win.

### Do not add another LLM just for disposition
The immediate gap is missing coordinator facts, not missing model count.

---

## 4. Recommended Order for the Next Implementation Slice

1. expose one Adjutant-facing coordination snapshot
2. feed triage-enriched active-task summaries into `Adjutant._build_context()`
3. use capability/threat/task-state fields to tighten `merge / override / interrupt / info`
4. only after that, reassess whether any larger Adjutant redesign is still needed

---

## 5. Bottom line

The next slice should **not** ask:

> should we add Commander?

It should ask:

> how do we make `Adjutant` see the top-level state the runtime already knows?

That is the most realistic, lowest-risk path to making `Adjutant` feel like the real battlefield coordinator.
