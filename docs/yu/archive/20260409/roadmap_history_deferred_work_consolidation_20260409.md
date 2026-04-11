# Roadmap / History / Deferred Work Consolidation Audit

Date: 2026-04-09  
Author: yu  
Scope: `docs/yu/pending_drift_fixes.md`, `docs/rts_agent_system_roadmap.md`, `docs/yu/archive/documentation_cleanup_status_20260406.md`, `docs/yu/wang_system_architecture_audit_20260406.md`, `docs/xi/expert_redesign.md`, `docs/wang/capability_task_design.md`, `docs/wang/optimization_tasks.md`, `docs/wang/archive/system_issues_and_design_gaps.md`, plus the closely related trace / prompt / runtime analysis docs.

## 0. Executive Summary

The doc set is not internally contradictory in the sense of “different projects”; it is contradictory in the sense of **time horizon and abstraction level**.

There are three different layers mixed together:

1. **Current runtime fixes / demo hardening**
   - stale world guards
   - log durability
   - queue cleanup
   - NLU routing consistency
   - Task trace / diagnostics

2. **Near-term architecture convergence**
   - Capability Task / EconomyCapability
   - Information Expert layer
   - structured runtime facts
   - task→player communication
   - phased handling of composite tasks

3. **Future target-state redesign**
   - InfluenceMap
   - UnitStatsDB
   - BuildOrderManager
   - CombatSimulation / CounterSystem / SquadManager / ExpansionAdvisor
   - Commander-style higher-level orchestration

The main conclusion is:

> The project should **not** continue as “many free-planning TaskAgents + prompt patches”.  
> It should converge to **one high-level directive / interpreter layer, persistent capability managers, deterministic execution experts, and explicit information experts**.

This is exactly the direction implied by the best recent LLM+RTS work and by Wang’s later architecture corrections. The problem is not direction; the problem is **roadmap order** and **doc hygiene**.

---

## 1. Consolidated Inventory: Deferred / Partial / Forgotten Items

### 1.1 Still open and high priority

These are the most important gaps that remain structurally unresolved:

| Item | Why it matters | Main sources |
|---|---|---|
| Task→player dialogue tool | Tasks still cannot reliably ask / warn / report through a formal channel | `pending_drift_fixes.md`, `system_issues_and_design_gaps.md`, `optimization_tasks.md` |
| Composite-task phase policy | Multi-step commands still drift across experts without bounded phases | `pending_drift_fixes.md`, `system_issues_and_design_gaps.md`, trace reports |
| Hard completion guard | LLM can still over-claim success without proof | `system_issues_and_design_gaps.md`, task trace reports |
| Structured runtime facts for ordinary tasks | Ordinary tasks must not see capability-only buildability/feasibility hints | `optimization_tasks.md`, capability design docs |
| Shared queue cleanup / queue manager | Build queue state outlives jobs; abort can leave queue residue | `pending_drift_fixes.md` |
| EconomyJob abort cleanup | Cancelled build jobs still leave shared queue artifacts | `pending_drift_fixes.md` |
| Execution verification | Deploy / recon / combat need outcome proof, not blind success | `optimization_tasks.md`, trace reports |
| Conversation compression | Task traces keep growing and become noisy | `optimization_tasks.md`, prompt/runtime analysis |
| Signal semantics / ordering | Event order can pollute the model’s causal understanding | `system_issues_and_design_gaps.md`, task trace analysis |
| LLM provider reliability | Timeouts / retries must not be left to incidental task-level handling | `optimization_tasks.md` |

### 1.2 Partially solved but still lingering in docs

These are important because they are already implemented or mostly implemented in code, but still show up in backlog-style docs:

| Item | Current reality | Doc drift risk |
|---|---|---|
| Runtime log durability | Session logs are now persisted per backend run, with per-task slices | Some docs still describe it as a live-only / in-memory gap |
| Task trace / diagnostics replay | Diagnostics can now expose task traces and llm input/history | Old “we cannot see task lifecycle” language is outdated |
| Stale-world fail-closed guards | Adjutant now degrades stateful commands / queries when world sync is stale | Some docs still frame the issue as only raw query failure |
| NLU runtime integration | Legacy NLU routing has been integrated into Adjutant for safe routes | Some roadmap text still reads as if NLU is a future addition |
| Queue manager / build-queue handling | Runtime queue management and cleanup have been introduced | `pending_drift_fixes.md` still reads like it is purely future work |
| Ordinary vs capability runtime facts split | Capability-only buildability hints are now separated from ordinary tasks | Older docs still imply a shared facts surface |

### 1.3 Forgotten / under-emphasized items

These are not always explicit blockers, but they recur across docs and code traces:

- Documentation status tagging is missing or stale in several top-level docs.
- `README.md` / `PROJECT_STRUCTURE.md` still lag behind runtime reality.
- A clear “current / target / archived” label is missing in some Wang and Xi design docs.
- The runtime still lacks a durable postmortem story that spans backend restarts and live sessions.
- The distinction between `TaskAgent` as local reasoning and `Capability` / persistent managers as long-lived control is still not reflected cleanly in every doc.

---

## 2. Cross-Doc Overlaps and Contradictions

### 2.1 `rts_agent_system_roadmap.md` vs `capability_task_design.md`

These two docs are **compatible**, but they sit at different abstraction levels:

- `rts_agent_system_roadmap.md` defines the target architecture:
  - Interpreter
  - Kernel
  - Shared World Model
  - Expert Systems
  - Game Adapter
- `capability_task_design.md` defines the most important near-term specialization:
  - ordinary tasks request units
  - Kernel fulfills what it can deterministically
  - EconomyCapability handles the remaining global economic/production policy

**No true contradiction exists** here. The contradiction only appears if `capability_task_design.md` is treated as a full current implementation spec instead of a targeted bridge.

### 2.2 `optimization_tasks.md` vs `pending_drift_fixes.md`

These docs overlap heavily, but from different angles:

- `optimization_tasks.md` is an **information-first priority backlog**
  - runtime facts
  - prompt/context quality
  - task→player communication
  - signal ordering
  - completion guards
- `pending_drift_fixes.md` is a **live drift / implementation gap backlog**
  - composite task drift
  - queue cleanup
  - log durability
  - task dialogue drift
  - stale state issues

The overlap is not a contradiction; it is a sign that the same root problems are being described twice:
- once as “missing information”
- once as “runtime drift”

The right consolidation is to **merge them into one phased roadmap** where “information plane” and “execution plane” are separate but sequenced.

### 2.3 `expert_redesign.md` vs current runtime

`docs/xi/expert_redesign.md` is explicitly future-state only.

Its proposed modules:
- InfluenceMap
- UnitStatsDB
- BuildOrderManager
- CombatSimulation
- CounterSystem
- Kiting
- SquadManager
- ExpansionAdvisor

These are **not contradictory** to current runtime. They are the natural next layer after the information plane is stable.

The mistake would be to read this doc as a short-term implementation plan. It is not. It presupposes:
- better world facts
- better tactic/strategy separation
- better execution guarantees

### 2.4 `wang_system_architecture_audit_20260406.md` vs `architecture_crisis.md` / `capability_task_design.md`

These docs are aligned on the broad conclusion:

- “many free-planning TaskAgents” is the wrong final shape
- LLM should move upward into interpretation / coordination
- deterministic execution and persistent capability managers should take over the repetitive work

Any tension is about *how fast* to transition:
- the audit says “the final direction should be hierarchical”
- the capability doc says “the immediate bridge is EconomyCapability / request_units”

That is a sequencing difference, not a design contradiction.

### 2.5 `documentation_cleanup_status_20260406.md` vs everything else

This doc is a meta-document, and its main warning is still valid:

- top-level docs are lagging behind runtime reality
- several “target-state” docs are not labeled clearly enough
- several resolved items remain in backlog-like language

This doc should be treated as a **governance / hygiene document**, not an execution blueprint.

---

## 3. Literature-Derived Design Constraints

The recent LLM+RTS literature does not support “let one LLM improvise the whole RTS stack”.

The common patterns across the reviewed work are:

1. **Hierarchical control**
   - higher-level language reasoning sits above lower-level execution
   - the model should not directly micromanage everything

2. **Summarization / compression**
   - large state spaces need filtered context, not raw replay dumps
   - repeated history must be compressed into decision-relevant summaries

3. **Structured world state**
   - the model needs an explicit semantic world representation
   - raw observations are too noisy for stable strategy

4. **Role separation**
   - planner, coordinator, executor, and information modules should not be conflated

5. **Deterministic execution beneath language**
   - language can choose policy, but execution should be dependable and auditable

This lines up with the recent papers reviewed in the system audit:
- *LLMs Play StarCraft II* (summarization + hierarchical control)
- *SwarmBrain* (LLM as embodied coordinator, not raw micromanager)
- *Harnessing Language for Coordination* (role-based multi-agent control)
- *Hierarchical Expert Prompt* / *TextStarCraft II* (expertized hierarchical prompting)
- *VLMs Play StarCraft II* (structured observation + decision pipeline)
- *Self-Evolving Multi-Agent Framework for RTS* (hierarchical multi-agent control)

### Design implication

The codebase should continue moving toward:

- **Interpreter / Adjutant**
  - extract intent
  - resolve ambiguity
  - route safely

- **Capability managers**
  - economy / queue / production / maybe future strategic managers
  - persistent, stateful, not per-task throwaways

- **Execution experts**
  - local, deterministic, verifiable

- **Information experts**
  - structured observations, hypotheses, threat/tech/queue summaries

This is the stable path.

---

## 4. Single Prioritized Roadmap

Below is the roadmap I would recommend if the goal is to ship a convincing, maintainable system instead of continuing to accumulate ad hoc fixes.

### Phase 0 — Demo safety and truthfulness

**Goal:** Stop the system from confidently saying or doing the wrong thing.

**Work items:**
- stale-world fail-closed behavior on stateful commands and queries
- provider timeout / dependency fail-fast
- response routing correctness (`query_response` vs notifications)
- log durability across backend restarts
- task trace visibility / replay
- NLU routing consistency for the live command set

**Why first:**
- If this layer is not reliable, every higher-level redesign is built on false runtime facts.
- This phase protects the demo and prevents the worst class of user-visible lies.

**Success criterion:**
- The system can say “I don’t know / world sync is stale / please retry” instead of inventing a confident answer.
- Live debugging and postmortem become possible after restart.

### Phase 1 — Capability consolidation

**Goal:** Move repetitive, shared, production-style control out of free-form task LLMs.

**Work items:**
- complete `EconomyCapability` / queue manager semantics
- keep `request_units` / `UnitRequest` as the ordinary-task interface
- keep buildability/feasibility out of ordinary task context
- explicitly manage shared production queue cleanup and auto-place behavior
- formalize task→player communication for capability / task events

**Why second:**
- Production and queue management are the biggest source of accidental multi-agent conflict.
- If capability is not centralized, every task brain will keep re-learning the same failure modes.

**Success criterion:**
- ordinary tasks request units / outcomes
- capability managers own the shared economy and build queue
- building queue residue and “my task but someone else’s queue” bugs are contained

### Phase 2 — Execution semantics and guards

**Goal:** Make jobs truthful and auditable.

**Work items:**
- deploy / recon / combat success proof
- hard completion guards
- signal ordering / causality cleanup
- abort semantics for queued work
- constraint propagation where it matters

**Why now:**
- Even with good routing and capability separation, the system still fails if job success/partial/fail semantics are fuzzy.

**Success criterion:**
- a job only ends as success when the underlying world confirms it
- the trace never says “success” just because the LLM guessed it

### Phase 3 — Information plane

**Goal:** Give the model the facts it actually needs.

**Work items:**
- structured runtime facts for ordinary vs capability tasks
- Information Experts for base state, threat, map semantics, queue pressure
- conversation compression
- better task trace / llm input / context snapshot surfacing
- keep the important “negative proof” facts visible

**Why here:**
- Once execution is truthful, better information meaningfully improves planning.
- Doing this before semantics are fixed only gives the model more noise.

**Success criterion:**
- the model stops having to infer basic state from noisy summaries
- repeated failures become explainable, not mysterious

### Phase 4 — Planner / composite task policy

**Goal:** Make multi-step intent behave like a bounded workflow, not free improvisation.

**Work items:**
- phase policy for composite commands
- task templates for `produce_units_then_recon`, `tech_up_then_recon`, etc.
- planner expert expansion from ProductionAdvisor into more complete domain advisors
- stronger question/clarification behavior when the intent is ambiguous

**Why after information plane:**
- good phase policy requires good facts and truthful execution signals
- otherwise templates become brittle and overfit to one trace

**Success criterion:**
- “build + scout” tasks follow a bounded order rather than exploring random expert combinations

### Phase 5 — Tactical substrate and commander target-state

**Goal:** Only after the above is stable, invest in the richer strategic modules.

**Work items:**
- InfluenceMap
- UnitStatsDB
- BuildOrderManager
- CombatSimulation
- CounterSystem
- SquadManager
- ExpansionAdvisor
- eventually commander-style or opponent-style higher-level control

**Why last:**
- these modules are expensive and only useful when the underlying facts, queue semantics, and execution guarantees are already reliable

**Success criterion:**
- higher-level tactical modules improve decisions instead of just adding more complexity

---

## 5. What Should Be Marked as Closed vs Kept as Deferred

### 5.1 Should be removed from “open drift” once docs are cleaned

If the current code state is taken as baseline, these items should no longer be described as future gaps in the same form:

- runtime log durability
- stale-world fail-closed behavior
- NLU runtime integration for the safe route set
- queue manager / shared build queue monitoring
- structured runtime facts split for ordinary vs capability tasks

### 5.2 Should remain deferred but moved into the new roadmap order

- task→player dialogue
- phase policy / composite task templates
- completion guards
- execution verification for all experts
- conversation compression
- deeper Information Experts
- tactical substrate modules
- commander / opponent / voice expansion

---

## 6. Recommended Documentation Cleanup

The docs themselves currently create one extra layer of reality drift.

### Must update
- `README.md`
- `PROJECT_STRUCTURE.md`

### Must label clearly
- `docs/wang/design_v_next.md`
- `docs/wang/capability_task_design.md`
- `docs/wang/adjutant_redesign.md`
- `docs/wang/architecture_crisis.md`
- `docs/xi/expert_redesign.md`

### Must keep as active backlog
- `docs/yu/pending_drift_fixes.md`

### Must treat as execution references, not future blueprints
- `docs/yu/wang_system_architecture_audit_20260406.md`
- `docs/yu/archive/task_agent_prompt_runtime_report.md`
- `docs/yu/archive/task001_trace_analysis.md`

---

## 7. Final Recommendation

If I compress all the docs, traces, and literature into one sentence:

> **Finish the capability / information / execution split before chasing commander-level sophistication.**

That means:
- no more “one free LLM brain per task” as the long-term shape
- no more prompt-only fixes for missing structure
- no more treating all architecture docs as if they were the same maturity level

The right order is:
1. truthfulness and demo safety
2. capability centralization
3. execution guards
4. information plane
5. composite-task policy
6. tactical substrate
7. commander / advanced opponent work

If the team follows that order, the current project can become a credible and maintainable OpenRA assistant. If the order is inverted, the system will keep accumulating “works in one trace, fails in the next” behavior.
