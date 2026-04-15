# Yu Plan

Updated: 2026-04-16 20:12

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Close combat global-claim / over-parallelization drift before the next E2E

- Problem: the latest E2E round shows that some combat tasks are not merely "too parallel"; generic combat jobs can start with `actor_ids=None, unit_count=0` and effectively claim the whole combat pool, which makes `Adjutant` fan-out much more destructive than intended.
- Goal: keep the `Kernel owns allocation, TaskAgent asks for resources` model, but remove the current "all available = 999" combat semantics that destabilize parallel combat/recon tasks.
- Exit criteria:
  - `CombatExpert(actor_ids=None, unit_count=0)` no longer degenerates into a whole-pool claim
  - direct/auto combat tasks use bounded or task-owned force requests
  - focused tests pin the new combat allocation contract
  - latest E2E audit findings are written and linked from `progress.md`

## Queue

- Close EconomyCapability autonomy drift: no player directive means no self-starting economic action; keep planning truth without autonomous execution.
- Close mixed-domain routing drift: ambiguous or composite commands must fail closed to Capability / managed-task handling instead of wrong direct execution.
- Close direct-build fast-path drift: short commands such as `电厂` / `兵营` / `电厂兵营五个步兵` should either route correctly with high confidence or cleanly fall back, never misbuild.
- Close attack / retreat / harass intent separation: preparation, attack-now, stop-attack, and retreat-to-base phrases need distinct routing contracts.
- Close continuation / reply / overlap drift: follow-up utterances should merge into the right active task or pending question instead of spawning low-value side tasks.
- Close visible operator drift after runtime truth is green: replay-summary flicker, task-question cancel affordance, and task/expert collapse state.
- Run the next controlled E2E only after the above slices are green and the issue register has been updated from HEAD.

## Blocked

- None.
