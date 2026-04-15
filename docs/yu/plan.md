# Yu Plan

Updated: 2026-04-16 18:24

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Close EconomyCapability autonomy drift

- Problem: the latest E2E rounds still show capability behaving or surfacing truth as if it may continue acting without a fresh player directive, which violates the “AI 副官, not AI commander” boundary.
- Goal: make the no-directive / directive-pending / active-execution contract explicit in code and consistent across runtime projection, Adjutant summary, and operator UI.
- Exit criteria:
  - no player directive means capability does not self-start economic action
  - planning truth can still surface without implying autonomous execution
  - runtime/exported capability state remains internally consistent
  - focused regression coverage is added
  - targeted verification is green

## Queue

- Close mixed-domain routing drift: ambiguous or composite commands must fail closed to Capability / managed-task handling instead of wrong direct execution.
- Close direct-build fast-path drift: short commands such as `电厂` / `兵营` / `电厂兵营五个步兵` should either route correctly with high confidence or cleanly fall back, never misbuild.
- Close attack / retreat / harass intent separation: preparation, attack-now, stop-attack, and retreat-to-base phrases need distinct routing contracts.
- Close continuation / reply / overlap drift: follow-up utterances should merge into the right active task or pending question instead of spawning low-value side tasks.
- Close visible operator drift after runtime truth is green: replay-summary flicker, task-question cancel affordance, and task/expert collapse state.
- Run the next controlled E2E only after the above slices are green and the issue register has been updated from HEAD.

## Blocked

- None.
