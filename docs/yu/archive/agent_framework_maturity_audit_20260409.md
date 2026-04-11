# Agent Framework Maturity Audit

Date: 2026-04-09  
Author: yu  
Focus: “Why the current framework still feels too thin”, what primitives are missing, what abstractions should be added/removed, and what a more modern but still practical framework shape should look like for this repo.

## 0. Short Answer

The current framework is **not just LLM + tool use**, but it is still **too close to that shape** in the places that matter for real-time RTS reliability.

It already has:
- a deterministic Kernel
- a shared WorldModel
- execution Experts / Jobs
- NLU front-end routing
- logging / session traces
- WS dashboard plumbing

But these are not yet assembled into a mature runtime with:
- a typed state model that all agents read the same way
- explicit workflow / phase semantics
- bounded loops with clear success guards
- a durable memory / replay story
- a stable distinction between ordinary tasks, capability managers, and information managers
- a proper scheduler contract instead of “every task gets its own brain and hopes for the best”

So the right diagnosis is:

> The repo already has pieces of a mature agent runtime, but the pieces are not yet composed into a fully modern real-time framework.

The biggest gap is not “more tools”. It is **missing runtime primitives**.

---

## 1. What the Current Framework Actually Is

Current runtime shape:

- `Adjutant` is the user entry point.
- `RuntimeNLU` + rule routing catch some safe/simple commands.
- `Kernel` creates `Task`s and `Job`s, binds resources, and dispatches experts.
- `TaskAgent` is the per-task LLM loop.
- `WorldModel` is the shared state and derived-facts layer.
- `Experts` are the execution and planning modules.
- `logging_system` and `ws_server` provide observability.

That sounds modern, but the practical behavior is still:

1. User says something.
2. Adjutant classifies / routes.
3. A TaskAgent is created.
4. The TaskAgent gets a large context packet and a tool list.
5. The LLM chooses tools / jobs / completion.
6. Kernel and Experts do the execution.

This is already better than raw “LLM writes Python”, but it is still architecturally thin in three ways:

- **state is not explicit enough**
- **workflow is not bounded enough**
- **memory/replay is not first-class enough**

---

## 2. What Is Structurally Too Thin Today

### 2.1 The state model is not first-class enough

The code has state, but it is split across many surfaces:

- `WorldModel.state`
- `Kernel.tasks`
- `Kernel.jobs`
- `Kernel.unit_requests`
- `TaskAgent` dialogue history
- `pending_questions`
- `TaskMessage`
- session logs

The problem is not that state exists. The problem is that there is no single, typed “agent runtime state” abstraction that says:

- what is the current directive
- what is the current phase
- what facts are stable
- what facts are stale
- what capabilities are active
- what inputs are pending
- what is the current workflow boundary

Because of that, LLMs receive a lot of raw structured data but still have to infer:

- whether the current task is ordinary or capability-like
- whether the world is degraded
- whether the task is waiting or actually failed
- whether a repeated signal is a retry artifact or a true new state

This is why the framework still feels “thin”: the state is present, but not normalized into a proper runtime contract.

### 2.2 Workflow semantics are too loose

The system has jobs and tasks, but not a robust workflow engine.

Examples:
- composite commands can drift across experts
- `EconomyJob` used to be able to declare success too early
- stale world could let the system keep answering from old state
- completion guards have been added in places, but not as a universal runtime discipline

What is missing is not just “better prompt”.
What is missing is a **bounded workflow model**:

- explicit phase templates
- explicit entry / exit criteria
- explicit restart / retry boundaries
- explicit negative evidence
- explicit “stop guessing, ask the player” conditions

Without that, the framework remains a collection of smart pieces that can still improvise badly when given a messy command.

### 2.3 Memory exists, but it is not yet a real memory architecture

The repo now has:
- per-session logs
- per-task log slices
- task trace output
- diagnostics replay of current live state

That is already useful, but it still is not a full memory architecture because:

- replay is currently centered on current backend session, not on durable history as a primary contract
- the LLM context is still built from ad hoc slices
- there is no strict separation between:
  - short-term working memory
  - task memory
  - capability memory
  - world memory
  - long-term archived trace memory

So the runtime can explain itself better than before, but it still lacks a mature memory hierarchy.

### 2.4 The system still uses “many LLM-shaped surfaces”

The current framework exposes a lot of LLM-facing surfaces:

- `TaskAgent`
- `Adjutant` query/classify paths
- `ProductionAdvisor`
- `RuntimeNLU`
- `Info Experts`

This is not automatically bad.
The issue is that the surfaces are not yet sufficiently specialized.

For a mature runtime, each surface should have a tight contract:
- interpreter decides intent
- planner chooses policy
- info experts summarize state
- capability managers manage shared resources
- execution experts execute bounded jobs

In the current code, the boundaries exist but are still soft enough that LLMs can leak across them.

---

## 3. Core Primitives That Are Missing

If we wanted this repo to feel like a mature real-time agent runtime, these primitives are the main missing pieces.

### 3.1 A typed directive / workflow object

There is not yet a strong, universal object that says:

- what the player wants
- whether this is a one-shot command, a managed workflow, or a capability-level background policy
- what phase the workflow is in
- what counts as success
- what facts are needed before continuing

The framework needs a first-class object like:

- `Directive`
- `WorkflowSpec`
- `PhaseState`

Right now that information is spread across prompt text and ad hoc runtime facts.

### 3.2 A runtime facts contract that separates “ordinary” and “capability” views

This is partly present now, but still not a mature primitive.

You need explicit facts such as:
- world health / stale state
- base status
- resource status
- buildability
- feasibility
- capability-only buildability hints
- per-task job status
- phase / wait reason / blocker

And you need an explicit rule for which task classes may see which facts.

Without that, the same context packet still serves too many roles.

### 3.3 A real scheduler contract

`Kernel` is a strong dispatcher, but it is not yet a scheduler in the full sense.

Missing scheduler-like primitives:
- priority + urgency + capability ownership as a formal queueing policy
- per-task resource reservation/provenance
- bounded admission rules
- clear cancellation and reclaim semantics
- queue-manager style background maintenance

This is why shared queue and build completion logic keep turning into special cases.

### 3.4 A durable replay / postmortem layer

The project now has logs, but not a fully mature memory store.

What is still missing:
- a durable, indexed session record that can be reopened independently of the live backend
- task timelines that are easy to query by phase, expert, failure cause, and decision
- a stable export format for “what the agent saw at step N”

This matters because RTS debugging is temporal.  
Without durable replay, the system remains harder to trust.

### 3.5 A formal “information expert” layer with subscription semantics

The docs and code already point in this direction, but the abstraction is still incomplete.

The mature shape should include:
- information experts as persistent readers/analysers
- explicit subscription of tasks/capabilities to information feeds
- stable derived facts, not just raw snapshots
- system-level summaries that the LLM can trust as current, not guessed

Today this is still partly prompt engineering and partly WorldModel aggregation.

### 3.6 A clear distinction between:

- **ordinary task**
- **managed task**
- **capability manager**
- **information expert**
- **execution expert**
- **constraint**

The code uses these words, but not always with enough rigor.

That is one reason the framework can feel thin:
the same LLM loop sometimes acts like a planner, a dispatcher, a controller, and a narrator.

In a mature runtime, those roles should be more explicit.

---

## 4. What Should Be Added

### 4.1 Add a real runtime state model layer

This should sit above raw `WorldModel` and below the LLM-facing prompt.

It should answer:
- What is the current directive?
- What phase is the workflow in?
- What is stable vs stale?
- What resources are reserved?
- What capabilities are active?
- What experts are currently controlling what?
- What should the LLM not re-decide?

This is the missing “glue primitive” that turns a bunch of good modules into a framework.

### 4.2 Add workflow templates for common RTS families

This is not the same as hardcoding scripts.
It is a bounded workflow shape.

Examples:
- deploy / expand
- build opening
- produce + scout
- defend under attack
- explore + report
- economy recovery

Each workflow should define:
- phases
- allowed experts per phase
- success conditions
- fallback / question / escalation conditions

### 4.3 Add persistent capability managers

This repo is already heading there.

Likely persistent managers:
- Economy / Production
- QueueManager
- Threat / Defense manager
- Information manager

These should not be re-instantiated as disposable per-task brains.

### 4.4 Add explicit memory strata

At minimum:
- working memory
- task memory
- capability memory
- world memory
- session replay memory

The system already has pieces of this; it needs a formalized shape.

### 4.5 Add a structured “reasoning trace” object

Logs exist, but they should be easy to query as structured trace events:
- input seen
- facts seen
- phase decided
- expert chosen
- job started
- signal received
- guard triggered
- completion reason

That trace object should be the canonical view for debugging, not a side effect.

---

## 5. What Should Be Removed or Demoted

### 5.1 Demote “LLM as improvisational planner of everything”

This is the biggest architectural correction.

The LLM should not be the place where:
- production policy is invented every turn
- shared queue semantics are rederived
- basic RTS doctrine is guessed from raw state
- success is narrated without proof

It should still be important, but at a higher layer and with bounded freedom.

### 5.2 Demote overly broad generic context packets

Context packets are currently helpful, but they are too generic.

They should not be the “one blob that everything depends on”.
They need to become structured views:
- ordinary task view
- capability view
- info expert view
- debug replay view

### 5.3 Demote per-task free-planning on shared macro systems

Shared macro systems such as:
- economy
- queueing
- defense escalation
- world health recovery

should not be left to each task brain independently.

That is the source of a lot of drift.

---

## 6. A More Modern But Still Practical Shape for This Repo

The practical target should be:

> **Directive-driven runtime with bounded workflows, persistent capability managers, and deterministic execution experts, all fed by a shared structured state plane.**

Concretely, the repo should look like this:

### 6.1 Front door

- `Adjutant`
  - interprets user input
  - routes to direct NLU / direct commands / managed workflows / queries
  - degrades safely when state is stale

### 6.2 Planning and dispatch

- `Kernel`
  - owns task lifecycle
  - owns admission
  - owns scheduling / resource arbitration
  - owns completion guards
  - owns request-unit semantics

### 6.3 Shared state plane

- `WorldModel`
  - raw world snapshots
  - runtime facts
  - health/staleness
  - queued work
  - threat / base / map / economy summaries

- `Information Experts`
  - turn raw facts into actionable summaries and hypotheses

### 6.4 Capability layer

- `EconomyCapability` / `QueueManager` / future capability managers
  - long-lived
  - shared
  - own repeated macro work
  - not tied to one task brain

### 6.5 Execution layer

- `Execution Experts`
  - short-lived or task-scoped
  - deterministic
  - verifiable
  - produce explicit progress and completion evidence

### 6.6 LLM layer

LLM should be used for:
- ambiguous intent resolution
- high-level planning choice among bounded options
- player questions
- explanation / recovery / fallback

Not for:
- low-level queue micromanagement
- repeated retry loops without new evidence
- re-deriving game doctrine from raw state each wake

### 6.7 Debug / replay layer

Must include:
- per-session logs
- per-task logs
- task trace
- llm input snapshot
- world facts snapshot
- replayable history after restart

This is necessary for trust and iteration speed.

---

## 7. The Main Structural Diagnosis

If I compress everything into one diagnosis:

### Too thin today because:

- the state model is distributed and not strict enough
- workflows are not explicitly bounded
- memory exists but is not yet a first-class runtime primitive
- the LLM still sees too many responsibilities at once
- capability and ordinary-task views are still too easy to blur

### What the repo should become:

- a **real-time directive runtime**
- with a **thin but strong Kernel**
- **persistent capability managers**
- **explicit information experts**
- **deterministic execution experts**
- **structured memory and replay**
- **safe degraded behavior under stale or partial state**

That is modern enough to be serious, but still practical for this codebase.

---

## 8. Recommended Next Roadmap for Framework Maturity

1. **Finish state normalization**
   - explicit runtime state / phase / facts surfaces

2. **Formalize workflow templates**
   - especially deploy / economy / recon / defense families

3. **Strengthen persistent capability managers**
   - queue, economy, future strategic managers

4. **Make traces durable and queryable**
   - task timeline, per-session replay, restart-safe debugging

5. **Keep shrinking free-form task agent freedom**
   - let the LLM choose among bounded options, not invent the whole policy

6. **Push more “common sense” into information and capability layers**
   - not prompt text
   - not ad hoc runtime guessing

That is the most realistic path from “thin agent with tools” to “mature real-time agent runtime” for this repo.

