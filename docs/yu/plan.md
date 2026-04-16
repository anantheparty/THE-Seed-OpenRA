# Yu Plan

Updated: 2026-04-17 05:19

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Surface ordinary managed combat/recon request truth more directly

- Scope: tighten the user-visible/runtime-facing truth for ordinary managed combat/recon tasks that are correctly blocked behind `request_units`, but still look opaque in chat/ops because the task mostly says `wait` without surfacing what it is waiting for.
- Goal: expose compact per-task request/reservation progress so “accepted quickly but没反应” can be distinguished from real routing/execution bugs.
- Exit criteria:
  - ordinary managed combat/recon tasks show a compact wait reason tied to their own request/reservation state
  - the surface does not leak full capability-planning state or global idle-force hints
  - focused tests pin the exact compact truth surface without adding a broad narrative layer

## Queue

- `袭击` is now aligned across direct attack routing, ordinary workflow classification, and owned-force guards; reopen only if a fresh E2E shows another attack synonym splitting those contracts.
- Ordinary movement/retreat tasks should expose explicit-group progress more truthfully if the next E2E still shows long `resource_lost` stalls after the landed completion-truth fix.
- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Shorthand economy routing is currently test-pinned; do not reopen it unless a fresh live E2E reproduces a current-code failure.
- Start Xi's replacement-style test strategy at slice 0 (`tests/_adjutant_fixtures.py` mock hoist) after the current product slice, before any larger test-governance sweep.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
