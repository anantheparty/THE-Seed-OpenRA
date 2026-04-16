# Yu Plan

Updated: 2026-04-17 04:16

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Normalize shorthand economy composites like `兵营3步兵` onto the capability path

- Problem: concise shorthand economy composites still fall through to ordinary managed tasks after runtime NLU declines, even though the stable fail-closed behavior should be to land on Capability rather than generic task reasoning.
- Goal: make shorthand forms like `兵营3步兵` reliably merge into Capability after NLU ambiguity rejection, without reopening blocked direct jobs or broadening combat/recon misroutes.
- Exit criteria:
  - shorthand composite phrases like `兵营3步兵` no longer create ordinary managed tasks
  - blocked shorthand still fails closed to Capability rather than direct blocked jobs
  - focused routing tests cover the shorthand form without building another giant precedence matrix

## Queue

- Revisit generic managed attack wording/tool use so attack workflows stop querying global idle actors after a failed ownership-bound `attack` call.
- Audit disposition (`override` / `interrupt`) paths against direct combat/retreat routing only after the request/reservation closure slice lands.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
