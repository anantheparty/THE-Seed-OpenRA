# Yu Plan

Updated: 2026-04-17 18:36

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Audit operator-wide retreat/attack completion semantics after preemption hardening

- Scope: stay on the same operator-wide combat/retreat chain after fixing repeated-retreat preemption.
- Goal: isolate whether the remaining live friction is now in partial completion / completion truth (`min_complete_count`, retreat finish, attack lingering), not in front-door routing.
- Exit criteria:
  - one concrete runtime truth gap is isolated from recent operator-wide task logs
  - the next patch boundary stays below “new planner / redesign” scale
  - verification stays on focused owner suites or live trace reconstruction, not broad speculative coverage

## Queue

- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Ordinary managed combat/recon tasks should expose their own request/reservation truth more directly if the next E2E still shows “waiting without knowing why”.
- Operator-wide retreat/attack logs should be re-audited against `MovementExpert` / `CombatExpert` partial-start and partial-complete semantics before touching Adjutant again.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
