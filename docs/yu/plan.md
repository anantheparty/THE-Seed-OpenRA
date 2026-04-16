# Yu Plan

Updated: 2026-04-17 20:03

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Audit shorthand economy / NLU routing regressions from the latest E2E rounds

- Scope: reconstruct the repeated user-facing failures around short or composite economy utterances, including forms like `兵营3步兵`, `建造电厂兵营五个步兵`, `五个防空车`, and `建造两个矿场一个车间`, across `Adjutant`, runtime NLU, capability merge, and any direct-economy fast paths.
- Goal: reduce these complaints to one concrete front-door routing root cause family and one smallest safe fix slice.
- Exit criteria:
  - one specific misroute path is traced end to end in logs/code
  - the next slice is phrased as a bounded routing contract change, not a broad “NLU is bad”
  - no speculative product change is mixed into the audit

## Queue

- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Ordinary managed combat/recon tasks should expose their own request/reservation truth more directly if the next E2E still shows “waiting without knowing why”.
- If operator-wide truth is sufficiently closed, return to the pending E2E register items around Adjutant routing and ordinary attack/retreat task semantics.
- After shorthand economy routing is re-grounded, return to ordinary attack/retreat task semantics from the latest E2E rather than operator-wide overrides.
- After the current operator-wide live truth chain lands, start Xi's replacement-style test strategy at slice 0 (`tests/_adjutant_fixtures.py` mock hoist) before any larger test-governance sweep.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
