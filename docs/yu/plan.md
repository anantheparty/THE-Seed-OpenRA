# Yu Plan

Updated: 2026-04-17 21:40

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Surface explicit-group movement/retreat stall truth more directly

- Scope: ordinary/direct retreat and move tasks with explicit `actor_ids` can now avoid false success, but when they stay alive behind `resource_lost` the user still sees weak progress truth about how much of the requested group is back, missing, or reacquired.
- Goal: expose compact per-task explicit-group progress so “任务没反应” can be distinguished from “正在等待剩余 actor package 回收/重绑”.
- Exit criteria:
  - explicit-group movement/retreat tasks surface requested-vs-bound progress without claiming false completion
  - the surface is task-local and does not introduce new global force claims
  - focused tests pin the compact progress surface without changing movement execution semantics

## Queue

- `袭击` is now aligned across direct attack routing, ordinary workflow classification, owned-force guards, and vague-combat merge/clarify coverage; reopen only if a fresh E2E shows another attack synonym splitting those contracts.
- Ordinary managed combat/recon request/reservation truth is now task-scoped in ordinary context and task-specific query focus; reopen only if a fresh E2E still shows opaque waiting despite the landed task-local pipeline fields.
- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Shorthand economy routing is currently test-pinned; do not reopen it unless a fresh live E2E reproduces a current-code failure.
- Start Xi's replacement-style test strategy at slice 0 (`tests/_adjutant_fixtures.py` mock hoist) after the current product slice, before any larger test-governance sweep.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
