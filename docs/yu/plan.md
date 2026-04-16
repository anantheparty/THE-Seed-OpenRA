# Yu Plan

Updated: 2026-04-17 20:55

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Normalize `袭击` across attack routing and owned-force contracts

- Scope: fix the latest concrete E2E leak where `全军袭击敌方` bypassed direct attack routing, became a generic managed task, and then re-opened global-force reasoning through `query_world(my_actors)`.
- Goal: make `袭击` behave consistently across `Adjutant` attack detection, operator-wide attack routing, ordinary managed attack workflow detection, and `TaskAgent` owned-force guards.
- Exit criteria:
  - `全军袭击敌方` no longer falls through to generic managed-task creation
  - ordinary attack tasks phrased with `袭击` cannot query global `my_actors` before owning units
  - focused tests pin the lexical contract without growing a mega-spec

## Queue

- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- Ordinary movement/retreat tasks should expose explicit-group progress more truthfully if the next E2E still shows long `resource_lost` stalls after the landed completion-truth fix.
- Ordinary managed combat/recon tasks should expose their own request/reservation truth more directly if the next E2E still shows “waiting without knowing why”.
- Shorthand economy routing is currently test-pinned; do not reopen it unless a fresh live E2E reproduces a current-code failure.
- Start Xi's replacement-style test strategy at slice 0 (`tests/_adjutant_fixtures.py` mock hoist) after the current product slice, before any larger test-governance sweep.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
