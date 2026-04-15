# Yu Plan

Updated: 2026-04-16 05:30

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Close EconomyCapability autonomy drift

- Problem: without an explicit player directive, EconomyCapability can still keep acting from stale internal context or over-eager wake conditions, which violates the “副官而不是指挥官” contract.
- Goal: keep capability planning/context visible, but require explicit demand (`PLAYER_MESSAGE`, active directive, or live request pressure) before any autonomous economic action starts.
- Exit criteria:
  - no economy job starts when there is no explicit player directive and no live request pressure
  - capability can still surface `next_step` / blockers / planning truth while idle
  - focused tests pin “plan-only when idle, act only on demand”
  - next E2E idle startup no longer self-expands or self-techs

## Queue

- Close direct-build fast-path drift: short commands such as `电厂` / `兵营` / `电厂兵营五个步兵` should either route correctly with high confidence or cleanly fall back, never misbuild.
- Close combat supervision / overclaim drift after bounded allocation: bounded combat allocation is fixed, but managed combat tasks still react too weakly to `resource_lost` / `risk_alert` / enemy visibility changes, and completion summaries can overstate unverified battle results.
- Close mixed-domain routing drift: ambiguous or composite commands must fail closed to Capability / managed-task handling instead of wrong direct execution.
- Close attack / retreat / harass intent separation: preparation, attack-now, stop-attack, and retreat-to-base phrases need distinct routing contracts.
- Close continuation / reply / overlap drift: follow-up utterances should merge into the right active task or pending question instead of spawning low-value side tasks.
- Close visible operator drift after runtime truth is green: replay-summary flicker, task-question cancel affordance, and task/expert collapse state.
- Run the next controlled E2E only after the above slices are green and the issue register has been updated from HEAD.

## Blocked

- None.
