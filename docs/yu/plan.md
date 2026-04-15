# Yu Plan

Updated: 2026-04-16 22:49

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Close capability replacement drift on abstract follow-up commands

- Problem: abstract follow-up text such as `先建造再攻击` can currently “replace task #002”, which means ordinary continuation/override routing is allowed to target `EconomyCapability`. Capability must not be deleted/replaced by normal player follow-up phrasing.
- Goal: keep Capability as a persistent singleton while still allowing ordinary task replacement/override behavior for non-capability tasks.
- Exit criteria:
  - continuation / override / interrupt selection never chooses the capability task as the task to replace
  - `先建造再攻击`-style text no longer surfaces `已取代任务 #002`
  - focused regressions pin the protection at the Adjutant routing boundary

## Queue

- Close EconomyCapability autonomy drift: capability must stay plan-only without explicit directive or live demand.
- Close direct-build fast-path drift: short commands such as `电厂` / `兵营` / `电厂兵营五个步兵` should either route correctly with high confidence or cleanly fall back, never misbuild.
- Close task-owned force package drift exposed by task #007: `request_units(vehicle, hint=重坦)` must not absorb unrelated idle vehicles; per-task battle queries must answer from exact runtime/job truth.
- Close composite build-then-act intent drift: phrases like `建造五个火箭兵去攻击敌方目标` should no longer be blocked by attack feedback, but they still collapse to direct economy execution instead of a truthful composite plan.
- Close combat supervision / overclaim drift after bounded allocation: bounded combat allocation is fixed, but managed combat tasks still react too weakly to `resource_lost` / `risk_alert` / enemy visibility changes, and completion summaries can overstate unverified battle results.
- Close task-query explanation drift: task-specific questions should answer from exact runtime/job truth before falling back to coarse battlefield snapshots.
- Close mixed-domain routing drift: ambiguous or composite commands must fail closed to Capability / managed-task handling instead of wrong direct execution.
- Close attack / retreat / harass intent separation: preparation, attack-now, stop-attack, and retreat-to-base phrases need distinct routing contracts.
- Close continuation / reply / overlap drift: follow-up utterances should merge into the right active task or pending question instead of spawning low-value side tasks.
- Close visible operator drift after runtime truth is green: replay-summary flicker, task-question cancel affordance, and task/expert collapse state.
- Run the next controlled E2E only after the above slices are green and the issue register has been updated from HEAD.

## Blocked

- None.
