# Yu Plan

Updated: 2026-04-17 19:48

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Let explicit-actor retreat jobs reacquire released-but-busy actors after preemption

- Root cause from `session-20260416T192124Z`: after operator-wide combat is cancelled, the selected actors become unbound first but can remain non-idle in the world for a while. Explicit-group retreat jobs currently reacquire only `idle_only=True` actors, so they can stay `WAITING` forever with an early `resource_lost` and never tick before the session ends.
- Goal: preserve the explicit-actor safety contract while allowing a follow-up retreat/move job to re-bind its selected package once those same actors are merely released-but-still-busy.
- Exit criteria:
  - reproduce the current stuck-`WAITING` case with a focused resource-assignment or live-runtime regression test
  - fix the reacquisition path without reopening generic global-force stealing
  - adjacent operator-wide retreat/attack tests stay green

## Queue

- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Ordinary managed combat/recon tasks should expose their own request/reservation truth more directly if the next E2E still shows “waiting without knowing why”.
- Operator-wide retreat/attack logs should be re-audited after the double-terminal fix to see whether any real partial-start / partial-complete truth gap remains.
- After the current operator-wide live truth chain lands, start Xi's replacement-style test strategy at slice 0 (`tests/_adjutant_fixtures.py` mock hoist) before any larger test-governance sweep.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
