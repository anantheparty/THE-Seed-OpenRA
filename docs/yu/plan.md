# Yu Plan

Updated: 2026-04-16 02:19

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Return To Live E2E Intake

- Problem: the latest live issue chain around demo roster truth is closed; the next mainline work is to resume E2E intake and handle the next reproducible runtime defect, not keep polishing this slice.
- Goal: restart from the next clean live repro, triage the highest-signal issue, and keep the fix scoped to one runtime truth chain at a time.
- Exit criteria:
  - a new live issue is reproduced cleanly
  - the root cause is isolated to one bounded chain
  - the next slice is ready to implement without reopening closed roster/prompt truth work

## Queue

- Add task/expert expand-collapse UI follow-up only after the current runtime truth issues are green.
- Add the next live E2E issue chain only after the rerun produces a clean reproducible session.

## Blocked

- None.
