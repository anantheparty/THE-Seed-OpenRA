# Yu Plan

Updated: 2026-04-15 23:20

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Docs / Knowledge Hygiene Follow-Through

- Problem: the product-side blocker work is now green enough for a new E2E round, but the supporting docs must stay aligned with the actual live contract instead of drifting behind the code.
- Goal: keep `plan.md` as the only active backlog, keep the live checklist aligned with the runner contract, and avoid hidden stale todos in reference docs while you start the next E2E round.
- Exit criteria:
  - no stale pre-E2E blocker remains listed here once code and live checklist agree
  - new yu-owned reference docs do not reintroduce hidden backlog language
  - any new E2E findings get logged as concrete slices instead of implicit notes

## Queue

- None.

## Blocked

- None.
