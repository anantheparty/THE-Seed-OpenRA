# Yu Plan

Updated: 2026-04-17 23:05

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Audit the next E2E blocker in attack-force acquisition and owned-force reuse

- Scope: the next high-impact product question is why some managed/combat attack commands still request fresh force packages or sit idle even when the player believes enough units are already on the field.
- Goal: trace one concrete latest sample end to end and decide whether the fault is in Adjutant routing, ordinary task ownership truth, Kernel allocation, or combat-task prompt/tool policy before changing code.
- Exit criteria:
  - one concrete latest task/session sample is pinned in `progress.md`
  - root cause is narrowed to one boundary instead of a broad “combat feels weak” statement
  - the next code slice can be kept within one theme chain

## Queue

- `袭击` is now aligned across direct attack routing, ordinary workflow classification, owned-force guards, and vague-combat merge/clarify coverage; reopen only if a fresh E2E shows another attack synonym splitting those contracts.
- Ordinary managed combat/recon request/reservation truth is now task-scoped in ordinary context and task-specific query focus; reopen only if a fresh E2E still shows opaque waiting despite the landed task-local pipeline fields.
- Live/replay `status_line` now carries shared compact unit-pipeline progress; reopen only if a fresh E2E still feels opaque despite the landed `assigned/produced/status/start` surface.
- Explicit-group movement/retreat stall visibility is now surfaced both in triage (`group=bound/requested`) and through selective task-message mirroring of `MovementExpert` `resource_lost` / `progress` / `risk_alert`; reopen only if a fresh E2E still shows silent retreat stalls.
- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Shorthand economy routing is currently test-pinned; do not reopen it unless a fresh live E2E reproduces a current-code failure.
- Start Xi's replacement-style test strategy at slice 0 (`tests/_adjutant_fixtures.py` mock hoist) after the current product slice, before any larger test-governance sweep.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
