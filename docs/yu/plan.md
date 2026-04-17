# Yu Plan

Updated: 2026-04-18 02:43

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

- Wait for fresh E2E verification of the `Task 007` force-acquisition chain after the priority-aware handoff fence fix.
  Acceptance:
  - Use the next live E2E run to confirm whether the `Task 007` class of issue is now closed after the idle reclaim, handoff fence, and high-priority preemption correction slices.
  - If live still fails, reopen only with the exact new symptom and task/log sample; do not speculate beyond the reproduced surface.
  - Until then, keep mainline work on other independently evidenced slices only.

## Queue

- Ordinary managed combat/recon request/reservation truth is now task-scoped in ordinary context and task-specific query focus; reopen only if a fresh E2E still shows opaque waiting despite the landed task-local pipeline fields.
- Live/replay `status_line` now carries shared compact unit-pipeline progress; reopen only if a fresh E2E still feels opaque despite the landed `assigned/produced/status/start` surface.
- Explicit-group movement/retreat stall visibility is now surfaced both in triage (`group=bound/requested`) and through selective task-message mirroring of `MovementExpert` `resource_lost` / `progress` / `risk_alert`; reopen only if a fresh E2E still shows silent retreat stalls.
- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Shorthand economy routing is currently test-pinned; do not reopen it unless a fresh live E2E reproduces a current-code failure.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
