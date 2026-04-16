# Yu Plan

Updated: 2026-04-17 19:20

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Read-only audit: operator-wide task logs with early `resource_lost` only

- Scope: inspect `logs/runtime/session-20260416T192124Z` for `t_03bd3fd3` (`全军出击`), `t_b5b2274e` (`全军撤退！`), and `t_6763a183` (`撤退回基地`), plus directly relevant component logs.
- Goal: determine whether the remaining truth gap is in expert ticking, signal routing, task-log capture, or session ending, and reduce it to one concrete root cause if possible.
- Exit criteria:
  - the three-task chain is reconstructed from task + component logs
  - one root cause is named, or the smallest competing hypotheses are ranked
  - no speculative code change is mixed into this audit

## Queue

- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Ordinary managed combat/recon tasks should expose their own request/reservation truth more directly if the next E2E still shows “waiting without knowing why”.
- Operator-wide retreat/attack logs should be re-audited against `MovementExpert` / `CombatExpert` partial-start and partial-complete semantics before touching Adjutant again.
- After the current operator-wide live truth chain lands, start Xi's replacement-style test strategy at slice 0 (`tests/_adjutant_fixtures.py` mock hoist) before any larger test-governance sweep.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
