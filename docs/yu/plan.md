# Yu Plan

Updated: 2026-04-16 00:13

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Next Live E2E Intake

- Problem: the startup passive-capability and replay-flicker regressions are now fixed locally, but the next useful work must come from a fresh live run rather than more speculative code churn.
- Goal: use the next clean live E2E to collect only net-new issues, then cut them into minimal slices with the new post-run log workflow.
- Exit criteria:
  - the next live session is reviewed against `docs/yu/e2e_log_triage.md`
  - any new issues are reduced to concrete engineering slices instead of open-ended observations
  - no already-fixed startup passive-capability or replay-flicker regression reappears

## Queue

- Add task/expert expand-collapse UI follow-up only after the current runtime truth issues are green.
- Add the next live E2E issue chain only after the rerun produces a clean reproducible session.

## Blocked

- None.
