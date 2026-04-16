# Yu Plan

Updated: 2026-04-16 22:01

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Normalize the remaining attack-family and task-truth contracts before the next controlled E2E

- Problem: the highest-noise front-door drifts have started to close, but attack-family semantics and task-owned truth are still inconsistent enough to create low-trust combat behavior in E2E.
- Goal: finish the stage-close blocker set with bounded changes to `Adjutant`, runtime NLU, and task-truth surfaces, without reopening architecture churn.
- Exit criteria:
  - attack-family commands (`prepare / harass / attack now / all-force override / retreat`) have one coherent front-door contract
  - mixed economy/combat commands fail closed or become truthful composites instead of half-executing
  - task answers and battle progress summaries prefer exact runtime/job truth over coarse narrative inference

## Queue

- Close attack-family normalization drift: `prepare / harass / attack now / all-force override / retreat` must no longer share contradictory routes or silently disagree with runtime-NLU metadata.
- Close economy/combat mixed-intent drift: phrases like `建造五个火箭兵去攻击敌方目标` must become a truthful composite plan or fail closed, not direct-economy execution with misleading combat feedback.
- Close task-owned force package drift: combat/movement tasks and operator queries must answer from exact runtime/job truth, and force requests must not absorb unrelated idle vehicles.
- Revalidate short direct-build routing only if the latest combat-focused fixes still leave a reproducible operator-trust gap for phrases like `电厂` / `兵营` / `五个防空车`.
- Keep capability/user-surface polish and debug-panel polish out of the mainline unless they expose a truth bug that affects the next E2E.

## Blocked

- None.
