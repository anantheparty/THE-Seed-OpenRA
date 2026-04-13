# Yu Plan

Updated: 2026-04-13 12:12

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.

## Now

### 1. Composite Managed-Task Phase Policy

- Problem: multi-step commands can still drift across Experts because managed tasks lack hard phase templates.
- Goal: add bounded phase workflows for the highest-value composite command families instead of letting the LLM improvise the whole chain.
- First targets:
  - `produce_units_then_recon`
  - `tech_up_then_recon`
  - `recover_economy_then_expand` only if it fits the same template model cleanly
- Exit criteria:
  - managed tasks show a current phase and blocker clearly
  - common composite commands stop jumping across unrelated Experts
  - ordinary failure modes become “blocked/waiting on phase condition” instead of noisy retries

### 2. EconomyCapability Reservation / Production Semantics

- Problem: `UnitReservation` exists, but future-unit ownership and queue/abort semantics are still only partially explicit.
- Goal: tighten reservation lifecycle, bootstrap reconciliation, cancel/abort cleanup, and operator-facing truth without starting a big new allocator architecture.
- Exit criteria:
  - reservation state transitions are explicit and stable
  - capability/runtime/debug surfaces all agree on the same reservation truth
  - queue/bootstrap side effects are visible and recoverable rather than inferred indirectly

### 3. Adjutant / Capability / TaskAgent Boundary Hardening

- Problem: some prompt/tool boundaries still allow semantic drift even though the intended role split is now clear.
- Goal: keep ordinary managed tasks request-only for missing units/prereqs, keep Capability truth aligned with actual buildability/tools, and remove remaining weak-reference prompt lies.
- Exit criteria:
  - ordinary tasks do not self-supplement prerequisites or production
  - capability prompts only advertise actions the runtime can actually support
  - coordinator/task/operator surfaces tell the same story about blockers and ownership

### 4. Live E2E Breadth After the Above Three Are Yellow-Green

- Problem: live harness breadth is useful, but it should validate stabilized behavior rather than discover basic truth drift.
- Goal: expand real game-in-loop coverage only after composite policy and capability semantics are tighter.
- Exit criteria:
  - representative live flows cover bootstrap, composite command, cancel, reply, and query paths
  - diagnostics/history remain consistent with live runtime during those flows

## Next

- Top-level docs consolidation: keep `README.md`, `PROJECT_STRUCTURE.md`, and design docs aligned with current runtime truth.
- Historical diagnostics enrichment only in explicit slices, one surface at a time.
- Queue/runtime/operator polish driven by real operator pain, not speculative UI churn.

## Later

- Better faction-aware long-term capability support beyond the current demo-safe roster.
- Additional persistent capabilities beyond economy, if and only if the economy/task split is already stable.
- Distant architecture work such as standalone `Commander` remains a future design topic, not an active implementation lane.

## Watchlist

- `QueueManager` mode/policy tuning based on live behavior, not theory.
- Runtime fault taxonomy and aggregation polish.
- Replay/history compaction if scan cost becomes a real operator problem again.
