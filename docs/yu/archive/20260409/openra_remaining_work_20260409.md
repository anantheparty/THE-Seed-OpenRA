# OpenRA Remaining Work

Date: 2026-04-09  
Author: yu

This document compresses the current roadmap into one question only:

**What still needs to be built, cleaned up, and redesigned on the OpenRA project itself?**

It is intentionally narrower than the broader multi-agent roadmap. It focuses on the active OpenRA runtime, not on distant target-state concepts.

---

## 0. Locked Architectural Decisions

These should be treated as settled for the next implementation phase.

### 0.1 Adjutant is the top-level coordinator

Near-term, the system should not introduce a separate `Commander` runtime layer.

Reason:
- `adjutant/adjutant.py` already owns player ingress, NLU/routing, query/reply/cancel, capability merge, and player-facing coordination.
- Adding a second top-level orchestration brain would duplicate responsibility and slow convergence.

Interpretation:
- `Adjutant` should become stronger, not be bypassed.
- A future strategic `Commander` is only a far-target concept if the current `Adjutant` is ever split into two layers. It is not a near-term execution item.

### 0.2 Kernel stays deterministic

`kernel/core.py` is already the correct backbone for:
- task lifecycle
- job lifecycle
- resource arbitration
- unit request handling
- task messaging
- task completion state

The next step is not to replace Kernel logic with more LLM planning. The next step is to feed Kernel better objects and cleaner semantics.

### 0.3 Capability is the right direction for shared production and persistent domain control

Shared queues, prerequisites, production bootstrap, and broad economic requests should continue moving into persistent capability logic.

Ordinary managed tasks should not plan production. They should request execution resources and wait.

### 0.4 TaskAgent should keep shrinking

`TaskAgent` should remain only for:
- complex managed tasks,
- bounded local reasoning,
- situations where direct routing and capability cannot already solve the problem.

It should not regain responsibility for:
- production planning,
- shared queue management,
- long-horizon economic control,
- or general “battlefield command”.

---

## 1. What Is Still Missing On OpenRA

The remaining work clusters into seven concrete areas.

### 1.1 Front-door intelligence is still too weak

`Adjutant` is already the real coordinator, but it is still too thin in battlefield control.

Current issues:
- Routing quality is better than before, but still depends on a mixture of old NLU assets, hand-written rules, and fallback LLM classification.
- `Adjutant` still lacks a strong, curated battlefield snapshot for top-level reasoning.
- Command disposition is still shallow. It can route and merge, but it is not yet a real strategic interpreter for ongoing battlefield context.
- Player dialogue and task dialogue are still not fully unified into one clean interaction model.

What needs to be built:
- A stronger `Adjutant` state view: concise base state, threat state, capability state, squad state, pending commitments.
- Better command disposition logic: new order vs merge vs override vs interrupt vs info injection.
- A single player-facing message policy so `query_response`, notifications, warnings, and task messages feel like one system.
- Real task-to-player dialogue semantics for managed tasks, but routed through `Adjutant`.

### 1.2 Capability is not yet real enough

Capability exists directionally, but not yet as a mature shared control layer.

Current issues:
- Capability prompts and context still need hard boundaries and phase discipline.
- Broad commands such as “发展科技，经济” can still expand into noisy behavior without a minimal bounded plan.
- Capability sees better information than ordinary tasks, but the knowledge and dependency sources are still not unified enough.
- Production requests, prerequisites, and queue management are still partially spread across Kernel, EconomyExpert, QueueManager, and prompt logic.

What needs to be built:
- One explicit capability contract:
  - what it owns,
  - what it can produce,
  - what it may request,
  - what counts as waiting,
  - what counts as blocked,
  - what ends the current phase.
- Capability phase policy for broad economy/tech directives.
- A stronger boundary between:
  - ordinary task resource requests,
  - capability-owned production decisions,
  - queue maintenance/runtime repair.

### 1.3 Present-resource and future-resource scheduling are still mixed

This is one of the biggest structural gaps.

Current reality:
- Kernel can bind present resources.
- Kernel can bootstrap some production via `UnitRequest`.
- Economy/queue logic can still leave ambiguity around who owns newly produced units and how future production should be prioritized.

Missing:
- a future-unit reservation layer,
- a single production allocator policy,
- explicit ownership transfer from “future unit” to “task-controlled unit”.

What needs to be built:
- `UnitRequest -> Reservation -> Production -> Assignment` as a first-class flow.
- Separation between:
  - present actors/resources,
  - future production commitments.
- Stronger cancellation/reclaim semantics for production requests.

### 1.4 OpenRA action surface is still too small

This is a major reason the runtime feels less capable than earlier direct Python control.

Current exposed task tools are effectively:
- `deploy_mcv`
- `scout_map`
- `produce_units`
- `request_units`
- `move_units`
- `attack`
- job lifecycle tools
- `query_world`
- `query_planner`
- `send_task_message`

That is enough for a prototype, but not enough for a mature RTS control runtime.

Current gaps:
- no explicit retreat/regroup/hold/guard/patrol style control surface
- no real formation/squad control layer
- no explicit action primitives for “reposition without full move semantics”
- no high-confidence battle posture transitions
- no explicit stop-fire / disengage / fallback style semantics
- no explicit expansion/repair/reinforcement control primitives beyond generic job composition

Important detail:
- `MovementJobConfig` already supports `actor_ids`
- `CombatJobConfig` and `ReconJobConfig` still do not

That means movement can already be “control this set of units”, while combat/recon still partially mean “give me some suitable units”. This is not enough for mature battlefield control.

What needs to be built:
- hard unit ownership for recon/combat
- `actor_ids` or `group_handle` on combat/recon configs
- a group/squad abstraction above individual job configs
- a richer execution expert surface, not just more prompt freedom

### 1.5 Knowledge, dependencies, and buildability are not yet one coherent truth

This is still too fragmented.

Current sources include:
- `unit_registry.py`
- `openra_state/data/dataset.py`
- `experts/knowledge.py`
- `world_model/core.py`
- prompt-level roster constraints

Current issues:
- demo roster and runtime buildability can diverge
- simplified OpenRA tech tree and general RA knowledge are not fully unified
- some dependency and blocker explanations still come from separate rule sets
- ordinary tasks and capability tasks do not consume a single canonical knowledge contract

What needs to be built:
- one canonical gameplay knowledge source for:
  - legal roster,
  - prerequisites,
  - role tags,
  - capability-only guidance,
  - blocker explanations.
- derived views for:
  - ordinary tasks,
  - capability,
  - diagnostics,
  - player explanations.

### 1.6 The agent framework is still structurally thin

The runtime is better than “LLM + tools”, but still too close to that shape where it matters.

Current issues:
- no first-class workflow/phase object
- state is spread across task state, jobs, runtime facts, messages, logs, and dialogue slices
- memory hierarchy is still weak
- prompt/context reinjection remains expensive on long tasks
- loops are more bounded than before, but still not systematically workflow-driven

What needs to be built:
- typed directive/workflow objects
- explicit phase semantics
- stronger success/failure/blocked guards
- bounded reasoning loops
- a clearer split between:
  - ordinary task reasoning,
  - capability memory,
  - information managers,
  - execution state.

### 1.7 Debugging and iteration UX are still not good enough

The project now has real logs. The pain is no longer missing data, but poor iteration ergonomics.

Current issues:
- too much raw data, not enough task summary
- replay is still mostly current-session centric
- task causality still has to be reconstructed mentally
- session triage is still manual
- developers still end up reading raw JSONL for too long

What needs to be built:
- task-centric summary views
- first-class waiting/blocking reasons
- better session browser / offline replay
- task export bundles
- causality stitching across logs, signals, and notifications

---

## 2. Repo Cleanup Still Needed

The OpenRA project is still carrying multiple eras of itself in parallel.

### 2.1 Top-level docs are still misleading

`README.md` and `PROJECT_STRUCTURE.md` are not aligned with the current runtime.

Current problems:
- still describe old `web-console/`
- still describe “LLM generates Python”
- still frame the repo as `the-seed` legacy plus future agents split
- still understate the current `Adjutant -> Kernel -> Experts -> WorldModel` runtime

These two files must be rewritten before broader onboarding or external review.

### 2.2 Active vs deprecated vs archived surfaces are not clear enough

At repo top level, the current runtime reality is obscured by parallel trees:
- `web-console/`
- `dashboard/`
- `default_runtime/`
- `adapter/`
- `tactical_core/`
- `the_seed/`
- `uni_mic/`

Some of these may still be useful as archived or reference material, but they should not look like equal active paths.

### 2.3 Generated/runtime output needs a lifecycle policy

Current repo surface still includes runtime/log/build residue:
- `Logs/`
- `web-console-v2/dist/`
- `node_modules/`
- other output directories

These should be formally treated as operational output, not quasi-source content.

---

## 3. OpenRA-Specific Implementation Order

This is the recommended order if the goal is to turn the current project into a mature OpenRA runtime rather than continue accumulating partial layers.

### Phase A — Make the current OpenRA runtime truthful and controllable

Priority:
- tighten `Adjutant` as top-level coordinator
- unify player-facing message behavior
- finish ordinary-task vs capability boundary enforcement
- remove dependency/buildability drift between prompt, world facts, and gameplay knowledge

Output:
- stable front door
- no production planning leakage from ordinary tasks
- cleaner, more truthful player interaction

### Phase B — Make Capability truly own shared production

Priority:
- explicit capability ownership over production/prerequisites
- phase-bounded broad directives
- stronger waiting/blocker semantics
- clearer queue/production repair policy

Output:
- capability becomes a real persistent controller, not just a special managed task

### Phase C — Add future-resource scheduling

Priority:
- reservations
- allocator policy
- assignment of new units to waiting tasks
- cancellation/reclaim semantics

Output:
- system can safely answer “who gets the next produced unit?”

### Phase D — Add hard unit ownership for battlefield tasks

Priority:
- combat/recon `actor_ids` or group handles
- squad/group abstraction
- reinforcement policy
- rebind and persistence semantics

Output:
- long-lived battlefield control becomes trustworthy

### Phase E — Expand OpenRA operability

Priority:
- enrich expert surface
- more control modes
- better battle posture transitions
- expansion/repair/reinforcement execution semantics

Output:
- runtime stops feeling “thinner than direct Python control”

### Phase F — Upgrade iteration UX and postmortem flow

Priority:
- task summary
- better session browser
- offline replay
- export bundles
- triage-first diagnostics

Output:
- debugging becomes sustainable instead of forensic

### Phase G — Rewrite docs and retire old surfaces

Priority:
- rewrite `README.md`
- rewrite `PROJECT_STRUCTURE.md`
- label active/deprecated/archived
- clean repo surface

Output:
- project stops misdescribing itself

---

## 4. Immediate Next Work For OpenRA

If implementation starts immediately, the next concrete block should be:

1. rewrite `README.md` and `PROJECT_STRUCTURE.md`
2. strengthen `Adjutant` state/disposition and unify player-facing communication
3. unify dependency/buildability/roster truth across `dataset`, `knowledge`, `world_model`, and prompts
4. make capability phase-bounded and explicitly own production/prerequisite control
5. design and implement reservation/allocator flow for future units
6. add hard ownership to recon/combat tasks
7. expand execution/action surface until the runtime no longer feels weaker than direct Python control
8. build better session/task triage UX

If these eight items are completed in order, the OpenRA project will stop looking like a prototype with many good pieces and start looking like a coherent real-time AI runtime.
