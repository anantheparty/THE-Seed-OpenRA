# Yu Plan

Updated: 2026-04-17 20:10

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Audit ordinary attack/retreat task semantics from the latest E2E rounds

- Scope: reconstruct the user-visible failures where non-operator-wide combat/movement commands were accepted quickly but then produced weak or no in-game effect, including the recent reports around ordinary `attack`, `retreat`, “先建造再攻击”, and managed combat tasks waiting on force ownership or target clarity.
- Goal: isolate one concrete downstream contract gap between `Adjutant`, `TaskAgent`, `request_units`, and `Kernel` resource assignment instead of treating all “一般攻击没反应” reports as one bucket.
- Exit criteria:
  - one specific ordinary attack/retreat failure is traced end to end in logs/code
  - the next slice is phrased as a bounded contract change, not a broad “combat task不智能”
  - no speculative product redesign is mixed into the audit

## Queue

- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Ordinary managed combat/recon tasks should expose their own request/reservation truth more directly if the next E2E still shows “waiting without knowing why”.
- Shorthand economy routing is currently test-pinned; do not reopen it unless a fresh live E2E reproduces a current-code failure.
- After the current operator-wide live truth chain lands, start Xi's replacement-style test strategy at slice 0 (`tests/_adjutant_fixtures.py` mock hoist) before any larger test-governance sweep.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
