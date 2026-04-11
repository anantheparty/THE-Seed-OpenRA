# Real-Time Multi-Agent Runtime Roadmap

Date: 2026-04-09  
Author: yu

This document consolidates:

- the current runtime reality,
- previously deferred or partially solved work,
- the next-stage direction for a low-latency real-time multi-agent system,
- and the developer-iteration tooling needed to make that system maintainable.

It is intended to replace scattered “roadmap + backlog + audit” fragments with one execution-oriented plan.

---

## 0. Executive Summary

The project should stop thinking of itself as:

- “a task agent that controls a game,” or
- “a prompt-heavy RTS bot.”

It should instead be treated as:

**a real-time multi-agent runtime for player-facing AI copilot / opponent systems, with OpenRA as the proving ground.**

The current codebase is already on the right path in several ways:

- `Adjutant` is the real top-level coordinator/front door.
- `Kernel` is already a deterministic lifecycle and arbitration core.
- `WorldModel` is already the shared data plane.
- `Experts` already form a usable execution substrate.
- `EconomyCapability` / `UnitRequest` already point toward persistent domain controllers.
- runtime logging, task traces, and persistent session logs now exist.

What is still missing is not “more LLM.”

What is still missing is:

1. **persistent capability managers with hard boundaries**
2. **explicit present-resource vs future-resource scheduling**
3. **hard unit ownership for long-lived control tasks**
4. **event-driven low-latency wake semantics instead of conversational drift**
5. **developer-first replay/triage tooling**

The correct next step is not a new Commander rewrite. It is:

**Adjutant → Capability Managers → Deterministic Experts → Information Plane**, with `TaskAgent` shrinking into a constrained local reasoner for only the tasks that truly need it.

---

## 1. Current Roadmap Baseline

This is the current near-term truth, distilled from the active docs and current code.

### 1.1 What is already structurally correct

#### Adjutant is already the top-level coordinator

The real entry stack is already:

- `adjutant/adjutant.py`
- `adjutant/runtime_nlu.py`
- `main.py`

It already handles:

- user command ingress
- simple/direct routing
- query/reply/cancel
- capability merge
- task-facing player replies

So the project should treat `Adjutant` as the actual top-level coordinator, not invent another near-term “Commander” layer.

#### Kernel is already the deterministic backbone

`kernel/core.py` already owns:

- task creation and lifecycle
- job creation and lifecycle
- resource arbitration
- unit request tracking
- capability-related fast paths
- question/response bookkeeping
- task status and result transitions

This is the correct place for deterministic coordination semantics.

#### Experts are already the execution substrate

The runtime already has usable execution experts:

- `DeployExpert`
- `EconomyExpert`
- `ReconExpert`
- `MovementExpert`
- `CombatExpert`

The direction should be to make their contracts tighter and their state more explicit, not to replace them with general LLM reasoning.

#### WorldModel and runtime facts are the right information substrate

`world_model/core.py` and `task_agent/context.py` already point in the right direction:

- shared world state
- runtime facts
- structured context
- current task/job summaries

This should keep getting thicker and more reliable.

### 1.2 What the current roadmap already says correctly

Across:

- `docs/rts_agent_system_roadmap.md`
- `docs/wang/capability_task_design.md`
- `docs/yu/wang_system_architecture_audit_20260406.md`
- `docs/yu/roadmap_history_deferred_work_consolidation_20260409.md`

the consistent correct line is:

1. simple commands should avoid slow LLM loops;
2. shared production should move into persistent capability/control;
3. ordinary managed tasks should request execution resources, not plan production;
4. information should be explicit and structured, not inferred repeatedly from raw logs or weak summaries.

That baseline should be considered stable.

---

## 2. What Is Still Missing for a Real-Time Low-Latency Multi-Agent System

The missing pieces fall into five buckets.

### 2.1 Capability layer is not yet fully real

`EconomyCapability` exists directionally, but the full pattern is not finished.

Missing:

- tighter capability ownership over production and prerequisites
- clearer wake/sleep semantics for capability tasks
- stronger contract between `UnitRequest`, bootstrap production, and capability planning
- queue semantics that are no longer distributed across task-local reasoning

Current risk:

- ordinary managed tasks still drift into capability-adjacent reasoning if prompt/context boundaries weaken
- broad commands can expand indefinitely without a phase cap

### 2.2 Future-resource scheduling is still weak

The runtime can already bind **present** units and do limited bootstrap production, but it still lacks a real reservation layer for **future** units.

That means the system still has no complete answer to:

- who owns the next produced unit?
- how should competing requests be prioritized before production starts?
- when should one request block another?

This is the biggest missing piece in turning the current runtime into a true multi-agent scheduler.

### 2.3 Hard unit ownership is incomplete

Movement can already target specific units.

Combat and recon still do not fully operate as:

- “control my group”

and instead still partially operate as:

- “give me matching units”

For a true long-lived multi-agent system, this is not enough.

The runtime needs:

- `actor_ids` or `group_handle` for recon/combat configs
- explicit squad/group identity
- ownership that survives phase changes

### 2.4 Wake semantics are still too conversational

Even after recent fixes, the system still spends too much cost on:

- repeated review wakes
- repeated prompt/context reinjection
- LLM loops over long-running tasks

For a low-latency system, the wake model should become more like:

- event-driven state transitions,
- bounded review,
- explicit waiting/blocking phases,
- and “only wake the reasoner when there is a meaningful decision to make.”

### 2.5 Iteration tooling is still too raw

The system now has logs, but not enough usable tooling.

It is still too easy to end up:

- staring at huge traces,
- reading long JSONL logs manually,
- reconstructing causality by hand.

This is acceptable for a prototype, but not for a mature real-time system.

---

## 3. Structural Adjustments the System Still Needs

This section is the most important design correction.

### 3.1 Do not add a near-term Commander layer

Near-term structure should be:

- `Adjutant`
- `Capability Managers`
- `Kernel`
- `Execution Experts`
- `Information Plane`
- optional `TaskAgent`

Not:

- `Adjutant -> Commander -> Capability -> TaskAgent -> Experts`

Reason:

- `Adjutant` already plays the front-door coordinator role.
- Adding a new near-term Commander would duplicate responsibility and reintroduce architecture churn.

### 3.2 Split “reasoning” from “ownership”

The runtime must stop conflating:

- who reasons about the task,
- who owns the units,
- who owns the shared queue,
- who owns the strategic state.

The target separation is:

- `Adjutant` owns user intent routing and task disposition
- `Capability` owns persistent domain policy
- `Kernel` owns arbitration and assignment
- `Experts` own deterministic execution
- `TaskAgent` owns only local bounded reasoning for complex tasks

### 3.3 Add an explicit reservation layer above production

This project is mature enough to need two different scheduling domains:

1. **present resource scheduling**
   - existing units
   - existing queues
   - active jobs

2. **future resource scheduling**
   - units not built yet
   - production reservations
   - deferred ownership

This should become an explicit production reservation / allocator layer, rather than remaining implicit in `EconomyJob` behavior.

### 3.4 Separate information plane from execution plane more aggressively

Not every useful system module should become an execution expert.

The project needs a stronger distinction between:

- **Information experts / analyzers**
  - threat
  - base state
  - queue state
  - awareness
  - strategic summaries

and

- **Execution experts**
  - deploy
  - economy
  - movement
  - recon
  - combat

This is the only scalable path to low-latency control without pushing everything into prompts.

---

## 4. Recommended Implementation Order

This order is designed to maximize stability, developer leverage, and architectural convergence.

### Phase 0 — Truthfulness, safety, and runtime trust

Goal:

- make the system reliably say/do the truthful thing before making it more capable.

Priority work:

- stale-world fail-closed behavior
- reliable result verification (`deploy`, `recon`, `combat`, `economy`)
- provider timeout/retry correctness
- response routing correctness
- current documentation truthfulness

Success criteria:

- wrong confident behavior is minimized
- runtime can safely say “waiting / blocked / stale / retry”

### Phase 1 — Capability consolidation

Goal:

- make `EconomyCapability` real enough that ordinary tasks no longer leak into production planning.

Priority work:

- strict ordinary-task boundaries
- stronger capability-only runtime facts
- queue manager / abort cleanup alignment
- capability phase policy for broad commands
- capability wake/sleep semantics

Success criteria:

- ordinary managed tasks do not self-bootstrap production or prerequisites
- broad economy commands go through one stable channel

### Phase 2 — Present/future scheduling split

Goal:

- make resource assignment semantically sound.

Priority work:

- `UnitReservation`
- production allocator
- explicit ownership of produced units
- reservation lifecycle and cancellation semantics

Success criteria:

- “who gets the next unit?” is answered deterministically
- shared production is no longer semantically ambiguous

### Phase 3 — Hard unit ownership and control groups

Goal:

- make long-lived control tasks actually control persistent squads/groups.

Priority work:

- `actor_ids` / `group_handle` for recon/combat
- group identity in Kernel
- control-task binding semantics
- handoff / reinforcement semantics

Success criteria:

- move → recon → combat can operate on one stable bound group
- tasks no longer “re-grab” arbitrary units every phase

### Phase 4 — Information plane thickening

Goal:

- make the system reason from compact, high-value semantic state.

Priority work:

- more information experts
- queue-state expert
- awareness/threat/base-state summaries
- capability-oriented strategic facts
- stronger context compression

Success criteria:

- LLM loops get shorter, clearer, and more stable
- runtime facts replace repeated inference from raw traces

### Phase 5 — Composite-task bounded policy

Goal:

- stop multi-step commands from drifting into free-form doctrine.

Priority work:

- phase templates
- bounded expert sets per phase
- phase transition conditions
- stronger completion/partial/fail semantics

Success criteria:

- `tech-up then recon`
- `produce units then attack`
- `defend then regroup`

behave like bounded workflows, not open-ended LLM improvisation.

### Phase 6 — Tactical substrate

Goal:

- add the missing medium-term substrate for a stronger opponent/copilot.

Priority work:

- influence map
- unit stats DB
- squad/group manager
- counter-production logic
- combat state evaluation
- safer pathing / tactical routing

Success criteria:

- the system is no longer only “workflow-correct”
- it also becomes tactically more competent

### Phase 7 — Learning and eval loop

Goal:

- turn the runtime into a sustainable platform, not just a rule stack.

Priority work:

- structured replay dataset
- session comparison
- failure clustering
- human feedback pipeline
- RL / imitation / offline evaluation hooks

Success criteria:

- improvements can be measured and iterated systematically

---

## 5. Improving Developer Iteration Instead of Reading Fatal Amounts of Logs

This area deserves its own sub-roadmap because it directly affects engineering velocity.

Based on `docs/yu/logging_diagnostics_iteration_audit_20260409.md`, the current priority should be:

### 5.1 First: triage-first task summaries

Developers should see, per task:

- current phase
- waiting/blocking state
- last decisive event
- next likely action

before they read raw logs.

### 5.2 Second: structured “waiting on capability / waiting on resources / blocked” state

The UI should not infer this from free-text summaries.

It should consume explicit fields such as:

- `phase`
- `waiting_reason`
- `blocked_reason`
- `capability_name`
- `retry_count`

### 5.3 Third: offline session replay

The current system persists logs, but cannot yet reload them as a first-class session browser.

Needed:

- open past session
- replay task traces from disk
- compare sessions
- inspect prior failed E2E runs after restart

### 5.4 Fourth: task-centric export and root-cause bundle

For any failed task, the system should export one bundle containing:

- task trace
- task logs
- benchmark slices
- world snapshot slices
- player notifications / replies

This is the fastest path to useful postmortem.

### 5.5 Fifth: causality view

The system should stitch:

- task updates
- log entries
- notifications
- benchmark timings

into one readable timeline, instead of leaving developers to infer cause from raw interleaving.

---

## 6. What to Remove, Merge, or Downgrade

### Keep

- `Adjutant` as the front door and top-level coordinator
- `Kernel` as deterministic runtime core
- `WorldModel` as shared information substrate
- execution experts
- runtime NLU
- session logging and task traces

### Merge / absorb

- “Commander” should be absorbed into the long-term conceptual role of `Adjutant`, not added near-term as another active runtime layer
- queue cleanup logic should merge into a stronger capability/allocator story over time
- broad strategy prompt hacks should be replaced with bounded policy templates and stronger information plane

### Downgrade

- `TaskAgent` should be treated as an optional local reasoner, not the default owner of system intelligence
- generic conversation history should not be treated as durable task state

### Remove from near-term execution plans

- premature large Commander rewrite
- too many future-state experts before the information plane is stable
- free-form “let the LLM decide the doctrine” task flows

---

## 7. One-Year Outcome If This Roadmap Is Followed

If the roadmap above is executed in order, the system can evolve from:

- a promising but mixed copilot prototype

into:

- a stable real-time AI copilot runtime,
- a reusable multi-agent scheduling substrate,
- a high-quality OpenRA experimentation platform,
- and a stronger base for later opponent, RL, and investor-facing product directions.

By that point, the project would have:

- real persistent capability control
- explicit present/future scheduling
- hard unit ownership
- strong replay/triage tooling
- an actual data/eval loop
- and a credible story for both product and research expansion

That is enough scope for at least one year of meaningful development without artificial padding.

