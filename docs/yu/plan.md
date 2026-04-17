# Yu Plan

Updated: 2026-04-18 02:21

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

- Re-audit `Task 007` reservation truth after the idle actor reclaim fix.
  Acceptance:
  - Use the same `session-20260417T010527Z` chain to decide whether a meaningful residual still remains after reclaiming idle request-linked actors from generic jobs.
  - If the next residual is real, narrow it precisely (for example non-idle request-linked actors or post-handoff re-steal) before cutting code.
  - Do not broaden into generic combat/routing redesign unless the logs force that conclusion.

## Queue

- Ordinary managed combat/recon request/reservation truth is now task-scoped in ordinary context and task-specific query focus; reopen only if a fresh E2E still shows opaque waiting despite the landed task-local pipeline fields.
- Live/replay `status_line` now carries shared compact unit-pipeline progress; reopen only if a fresh E2E still feels opaque despite the landed `assigned/produced/status/start` surface.
- Explicit-group movement/retreat stall visibility is now surfaced both in triage (`group=bound/requested`) and through selective task-message mirroring of `MovementExpert` `resource_lost` / `progress` / `risk_alert`; reopen only if a fresh E2E still shows silent retreat stalls.
- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Shorthand economy routing is currently test-pinned; do not reopen it unless a fresh live E2E reproduces a current-code failure.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
