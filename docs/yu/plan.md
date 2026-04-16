# Yu Plan

Updated: 2026-04-17 16:20

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Investigate shorthand economy composite routing like `兵营3步兵`

- Problem: semantically equivalent economy composites still split across different front-door boundaries; shorthand forms like `兵营3步兵` can fall into ordinary managed tasks while explicit forms like `兵营然后3步兵` merge into Capability.
- Goal: reconstruct the exact current routing/code-path difference, identify the smallest boundary that would normalize shorthand onto the same economy/capability path without reviving blocked direct jobs, and name the smallest focused tests to add.
- Exit criteria:
  - exact front-door divergence is traced to concrete functions/guards
  - the recommended normalization boundary stays in Capability-oriented routing, not direct blocked jobs
  - the proposed tests cover shorthand vs explicit composites with minimal surface area

## Queue

- Normalize shorthand economy composites like `兵营3步兵`

- Problem: semantically equivalent economy composites still split across different front-door boundaries; shorthand forms like `兵营3步兵` can fall into ordinary managed tasks while explicit forms like `兵营然后3步兵` merge into Capability.
- Goal: normalize concise build+produce utterances onto the same economy/capability path without over-expanding NLU direct execution or reintroducing blocked direct jobs.
- Exit criteria:
  - shorthand and explicit composite economy utterances land on the same intended boundary
  - non-immediately-buildable composites still fail closed to Capability instead of direct blocked jobs
  - focused routing tests cover the normalized forms without adding another large contract matrix
- Revisit generic managed attack wording/tool use so attack workflows stop querying global idle actors after a failed ownership-bound `attack` call.
- Audit disposition (`override` / `interrupt`) paths against direct combat/retreat routing only after the request/reservation closure slice lands.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
