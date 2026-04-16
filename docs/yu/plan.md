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

### 1. Stop ordinary task completion / partial summaries from borrowing other-task causality

- Problem: ordinary `TaskAgent` completion still over-narrates from `[其他任务报告]` and coarse battlefield state, so user-facing `partial` / completion summaries can claim success that this task did not actually own.
- Goal: make task completion truth self-owned and fail-closed, especially for `partial` endings after long combat/recon reasoning loops.
- Exit criteria:
  - ordinary task completion context stops treating other-task reports as success evidence
  - when no owned job succeeded, `partial` can stay uncertain but cannot narrate borrowed battlefield success as if this task caused it
  - focused tests pin the self-owned completion-truth contract without reopening broad prompt/test churn

## Queue

- Revisit remaining operator-wide force-package issues only if the next dry run still shows partial pickup for `全员出击` / `现有单位也移动过去`.
- Revisit mixed economy/combat routing only if a fresh E2E still shows economy-first half execution after the managed-workflow handoff.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
