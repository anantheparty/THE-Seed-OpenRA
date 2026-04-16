# Yu Plan

Updated: 2026-04-17 19:57

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Re-audit operator-wide task truth after the two lifecycle/resource fixes

- Scope: revisit `session-20260416T192124Z` and confirm what remains after closing 1) direct-task double terminal cancellation and 2) `MovementExpert` explicit actor reacquisition.
- Goal: decide whether there is still a real operator-wide truth gap, or whether the remaining symptom chain was fully explained by those two root causes.
- Exit criteria:
  - the old `#009/#010/#011` chain is re-explained against the landed fixes
  - any remaining issue is reduced to one concrete next slice instead of a broad “operator-wide unstable”
  - no speculative unrelated refactor is mixed into the audit

## Queue

- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Ordinary managed combat/recon tasks should expose their own request/reservation truth more directly if the next E2E still shows “waiting without knowing why”.
- If operator-wide truth is sufficiently closed, return to the pending E2E register items around Adjutant routing and ordinary attack/retreat task semantics.
- After the current operator-wide live truth chain lands, start Xi's replacement-style test strategy at slice 0 (`tests/_adjutant_fixtures.py` mock hoist) before any larger test-governance sweep.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
