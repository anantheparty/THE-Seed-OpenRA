# Yu Plan

Updated: 2026-04-15 23:58

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. E2E Follow-Through: autonomous capability actions + diagnostics replay stability

- Problem: in the latest clean E2E startup, the diagnostics panel flickers because `Persisted Replay Summary` appears/disappears, and `EconomyCapability` appears to create/action tasks without an explicit player command.
- Goal: audit the newest runtime session end to end, identify the root causes with log evidence, land minimal fixes so startup idles cleanly without unauthorized capability execution, and keep diagnostics stable while live runtime updates stream in.
- Exit criteria:
  - latest session findings are reduced to a concrete issue list with evidence and engineering slices
  - no capability task/action is started on clean startup without an explicit player intent or a clearly-scoped system event
  - diagnostics replay/session summary surface no longer flickers between frames during normal live idle
  - the post-E2E log analysis workflow is written down as a reusable yu-owned reference

## Queue

- Add a small `docs/yu` E2E log-analysis workflow note based on this run, then archive or reference it from active docs.
- Add task/expert expand-collapse UI follow-up only after the current runtime truth issues are green.

## Blocked

- None.
