# Yu Plan

Updated: 2026-04-17 04:22

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Revisit generic managed attack wording/tool use after failed ownership-bound `attack`

- Problem: after task-owned combat requests miss or conflict, ordinary managed tasks still tend to ask about global idle actors or emit weak generic attack narration instead of staying inside the owned-force/request boundary.
- Goal: tighten task-agent guidance and, if necessary, the handler boundary so generic managed attack flows stop degrading into “query world idle army” behavior after an ownership-bound `attack` fails.
- Exit criteria:
  - ordinary managed combat tasks stop suggesting or querying unrelated global idle units as if they were task-owned
  - failed ownership-bound `attack` calls lead to request/wait/clarify behavior instead of vague continuation
  - focused tests pin the behavior without growing another broad prompt-surface suite

## Queue

- Audit disposition (`override` / `interrupt`) paths against direct combat/retreat routing only after the request/reservation closure slice lands.
- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
