# THE-Seed OpenRA Project Structure

This file describes the **current runtime reality** of the repository.

It replaces older descriptions that framed the project as:

- a `the-seed`-driven legacy agent,
- a separate “next-gen agents” tree,
- or an “LLM writes Python” execution pipeline.

The active OpenRA runtime is now centered on:

- `Adjutant`
- `Kernel`
- `TaskAgent`
- `Experts`
- `WorldModel`
- `OpenRA API / OpenCodeAlert`
- `web-console-v2`

---

## 1. Runtime Overview

### 1.1 Main execution path

```text
Player / Frontend
        |
        v
Adjutant
  - NLU / routing
  - query / reply / cancel
  - top-level coordination
        |
        v
Kernel
  - task lifecycle
  - job lifecycle
  - resource arbitration
  - unit requests
  - task messaging
        |
        +--> Capability-like control paths
        +--> Optional TaskAgent for complex managed tasks
        |
        v
Experts
  - deploy
  - economy
  - recon
  - combat
  - movement
        |
        v
Game API / OpenCodeAlert / OpenRA
```

Shared substrate:

- `world_model/`
  - shared state
  - runtime facts
  - event detection
  - production queue visibility
- `logging_system/`
  - structured logs
  - per-session persistence
  - per-task slices
- `ws_server/`
  - websocket transport to frontend

---

## 2. Active Code Areas

### 2.1 Runtime entry and orchestration

- `main.py`
  - backend entrypoint
  - wires runtime components together
- `adjutant/`
  - front door and top-level coordinator
- `kernel/`
  - deterministic coordination and lifecycle core
- `task_agent/`
  - bounded per-task LLM reasoning loop

### 2.2 Execution and information layers

- `experts/`
  - execution experts
  - info helper modules
  - planner helper modules
- `world_model/`
  - world refresh
  - derived facts
  - event detection
- `queue_manager.py`
  - singleton queue maintenance for stuck ready buildings

### 2.3 Game integration

- `openra_api/`
  - Python-side query and action surface
- `openra_state/`
  - supporting game data, datasets, and knowledge inputs
- `OpenCodeAlert/`
  - game-side patched bridge; active integration surface, not an ignore-only subtree
- `unit_registry.py`
  - YAML-backed unit/building registry

### 2.4 Frontend and observability

- `ws_server/`
  - websocket server
- `logging_system/`
  - structured logging and session persistence
- `web-console-v2/`
  - active frontend
- `voice/`
  - optional voice subsystem

### 2.5 Tests and docs

- `tests/`
  - runtime and regression tests
- `docs/`
  - active roadmap, audits, reports, and design notes

---

## 3. Important Runtime Boundaries

### 3.1 Adjutant is already the top-level coordinator

Do not treat `Adjutant` as a disposable chat wrapper.

It currently owns:
- user command ingress
- NLU/rule routing
- player query/reply flow
- some direct execution short-circuits
- capability merge
- player-visible response formatting

Near-term work should strengthen `Adjutant`, not bypass it with a second top-level command layer.

### 3.2 Kernel is deterministic infrastructure, not a planner

`Kernel` should continue to own:
- task/job bookkeeping
- resource arbitration
- unit request registration
- capability handoff hooks
- task messaging
- deterministic state transitions

It should not become a hidden second planner.

### 3.3 TaskAgent is not the whole system

`TaskAgent` remains useful for complex managed tasks, but it is no longer the default answer to every command.

Simple and safe commands should continue to be handled via:
- direct NLU/routing
- capability logic
- deterministic expert/job creation

### 3.4 Capability is the convergence path for shared production

Shared production, prerequisites, and queue-related economic control should continue moving into capability logic rather than remaining distributed across many ordinary tasks.

---

## 4. Transitional Areas

These areas are still important, but should be treated as transitional rather than final architecture.

### 4.1 `nlu_pipeline/`

This is not the old runtime in full, but it still supplies assets reused by the current front door:
- matching
- routing
- shorthand recognition
- legacy intent handling pieces

### 4.2 `agents/`

Parts of `agents/` reflect older standalone experiments and parallel designs.  
They are useful historical/reference material, but they are not the source of truth for the current runtime path.

### 4.3 `voice/`

Voice remains optional and is disabled by default unless explicitly enabled.  
It should be treated as an add-on subsystem, not part of the runtime core.

---

## 5. Deprecated / Historical Surfaces

These paths should not be read as the current architecture:

- `web-console/`
- `dashboard/`
- `default_runtime/`
- `adapter/`
- `tactical_core/`
- `the_seed/` as a direct description of the current execution stack
- old top-level “Legacy vs Next-Gen” framing

They may still contain reference code, but they should be considered deprecated, historical, or experimental unless explicitly reactivated.

---

## 6. What Still Needs To Be Built

The repo is no longer in pure feasibility mode. The main remaining work is:

- making `Adjutant` a stronger battlefield coordinator
- making capability control real for production and prerequisites
- separating present-resource scheduling from future-unit scheduling
- adding hard unit ownership for recon/combat style control
- expanding the OpenRA action surface so the runtime is no longer weaker than direct Python control
- improving diagnostics, replay, and developer iteration UX
- cleaning up stale docs and retired control planes

For the execution order, see:

- `docs/yu/openra_execution_board_20260409.md`
- `docs/yu/archive/20260409/realtime_multiagent_system_roadmap_20260409.md`
