# Yu Plan

Updated: 2026-04-16 19:03

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Audit remaining operator-wide force-package pickup before the next dry run

- Problem: the latest live feedback still reports some `全员出击` / `现有单位也移动过去` style commands moving only part of the expected force package. We have already bounded generic combat claims and normalized operator-wide aliases, but we have not re-audited the remaining pickup path after those fixes.
- Goal: confirm whether there is still a real operator-wide force-package drift, and if so close the smallest remaining slice without reopening broad combat/task architecture work.
- Exit criteria:
  - either confirm the remaining pickup complaint is already explained by old logs and remove it from the mainline
  - or land one bounded fix in the operator-wide selection/start path with focused regressions
  - keep the work at the `Adjutant`/direct expert boundary; do not reopen generic task-agent combat planning in this slice

## Queue

- Revisit remaining operator-wide force-package issues only if the next dry run still shows partial pickup for `全员出击` / `现有单位也移动过去`.
- Revisit mixed economy/combat routing only if a fresh E2E still shows economy-first half execution after the managed-workflow handoff.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
