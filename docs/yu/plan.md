# Yu Plan

Updated: 2026-04-17 18:18

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Re-audit operator-wide combat/retreat E2E semantics after owned-force hardening

- Scope: next product slice after ordinary-task owned-force prompt/tool/context surfaces were tightened.
- Goal: verify operator-wide attack/retreat commands still behave deterministically under Kernel resource semantics, especially partial-group startup, retreat completion, and task preemption.
- Exit criteria:
  - one concrete operator-wide runtime truth gap is isolated from the current E2E backlog
  - the next patch boundary stays below “new planner / redesign” scale
  - verification stays on focused owner suites or live trace reconstruction, not broad speculative coverage

## Queue

- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Ordinary managed combat/recon tasks should expose their own request/reservation truth more directly if the next E2E still shows “waiting without knowing why”.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
