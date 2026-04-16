# Yu Plan

Updated: 2026-04-16 18:46

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Close economy/combat mixed-intent drift before the next controlled E2E

- Problem: front-door attack/query truth is materially better now, but phrases like `建造五个火箭兵去攻击敌方目标` still degrade into half-truthful economy-first execution or misleading combat feedback.
- Goal: make mixed economy/combat directives either become an explicit truthful composite or fail closed, without letting capability/direct lanes silently eat the combat half.
- Exit criteria:
  - build-then-attack phrases no longer surface fake “missing visible target” feedback
  - direct economy fast paths do not silently discard a trailing combat intent
  - if the system cannot honor both halves truthfully, it clearly merges/fails closed instead of half-executing

## Queue

- Close task-owned force package drift: combat/movement tasks and operator queries must answer from exact runtime/job truth, and force requests must not absorb unrelated idle vehicles.
- Revisit remaining attack-family normalization leftovers only where they still affect live E2E trust after the direct-lane fixes.
- Revalidate short direct-build routing only if the latest combat-focused fixes still leave a reproducible operator-trust gap for phrases like `电厂` / `兵营` / `五个防空车`.
- Keep capability/user-surface polish and debug-panel polish out of the mainline unless they expose a truth bug that affects the next E2E.

## Blocked

- None.
