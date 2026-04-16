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

### 1. Close task-owned force package drift before the next controlled E2E

- Problem: even after recent combat routing fixes, tasks still lose trust when the force package is vague or overly specific in the wrong layer: operator queries answer from narratives, generic combat claims can still overreach, and some task surfaces still hide why only a subset moved/fought.
- Goal: keep combat/movement ownership truthful and bounded so tasks explain and use force packages the same way the kernel actually does.
- Exit criteria:
  - operator/task queries prefer exact task-owned force/runtime truth over inferred stories
  - generic combat/movement requests do not silently behave like “claim everything”
  - task surfaces expose enough force-package truth to debug E2E incidents without reading raw jsonl

## Queue

- Close task-owned force package drift: combat/movement tasks and operator queries must answer from exact runtime/job truth, and force requests must not absorb unrelated idle vehicles.
- Revisit remaining attack-family normalization leftovers only where they still affect live E2E trust after the direct-lane fixes.
- Revisit remaining mixed economy/combat variants only if live E2E still shows economy-first half execution after the new workflow handoff.
- Revalidate short direct-build routing only if the latest combat-focused fixes still leave a reproducible operator-trust gap for phrases like `电厂` / `兵营` / `五个防空车`.
- Keep capability/user-surface polish and debug-panel polish out of the mainline unless they expose a truth bug that affects the next E2E.

## Blocked

- None.
