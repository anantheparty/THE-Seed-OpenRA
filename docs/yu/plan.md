# Yu Plan

Updated: 2026-04-13 13:22

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Capability Issue-Now Truth De-Overlap

- Problem: `Capability` prompt/context still carries overlapping truth buckets such as broad `buildable`, immediate `buildable_now`, and blocked-but-prereq-satisfied state, which creates decision noise for the agent.
- Goal: reduce capability action truth to one primary “can act now / blocked now / next step” contract so the agent stops reasoning over three near-duplicate surfaces.
- Exit criteria:
  - `buildable_now` is the only direct action surface
  - blocked buildable items surface through an explicit blocker lane rather than broad “available” truth
  - prompt/context/policy wording no longer instruct the model through overlapping sections

## Queue

### 2. Ops Top-Level Multi-Request Surfacing

- Problem: top-level operator views still flatten multiple in-flight requests/reservations into one `unit_pipeline_focus`, so concurrent task work is not visible enough.
- Goal: surface a compact multi-request preview on the shared runtime/dashboard path without inventing another state machine.
- Exit criteria:
  - `world_snapshot` can expose 2-3 concurrent request/reservation summaries without losing current focus truth
  - `OpsPanel` can show those summaries compactly
  - one narrow live/mock-integration pin protects the new shared surface

### 3. Task-Owned Unit Continuity Surfacing

- Problem: runtime already tracks `active_actor_ids` / `active_group_size`, but task/operator surfaces still do not clearly say when a task already owns units and is executing with them.
- Goal: make task ownership continuity visible in task triage, task panel, and adjutant-facing summaries so “waiting for units” and “already has units” stop looking the same.
- Exit criteria:
  - triage/runtime payload carries a compact ownership summary
  - `TaskPanel` exposes the same ownership truth without large UI churn
  - adjutant/task summaries reuse the same field instead of inferring ownership indirectly

### 4. Docs / Knowledge Cleanup After the Current Product Chain

- Problem: `docs/yu/agents.md` still contains some stale “remaining gap / blind spot” backlog phrasing that no longer matches the current runtime state.
- Goal: keep `docs/yu` as current knowledge plus active backlog, not a second hidden todo system.
- Exit criteria:
  - stale blind-spot bullets are either deleted or rewritten as current facts
  - `plan.md` remains the only active backlog for Yu-owned work

## Blocked

- None.
