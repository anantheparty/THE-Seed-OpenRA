# Yu Plan

Updated: 2026-04-16 02:27

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Capability Goal-Completion / Clear Conditions

- Problem: the sticky `active_directive` slice removed the worst idle-early failure mode, but completion/clear semantics are still underspecified. Concrete build goals can now persist long enough to continue, yet there is no explicit contract for when a goal like `建造电厂` / `兵营扩到4个` should be considered satisfied and dropped instead of lingering until TTL expiry.
- Goal: add bounded clear/complete rules for the most common economy directives so persistence improves follow-through without creating stale overbuild pressure.
- Exit criteria:
  - the current sticky-goal behavior is audited against the main concrete economy directive classes
  - at least one explicit clear condition is implemented and verified for common direct build / build-count goals
  - remaining unresolved directive classes are queued explicitly

## Queue

- Keep economy ownership semantics green: retain capability-owned single-step NLU job coverage while the next E2E round lands.
- Keep voice compatibility green: retain frontend `wav` upload coverage and backend fallback coverage while the next E2E round lands.
- Fix the task-question cancel/reply UI so buttons only disable after a successful websocket send; current send-failure path can strand the operator locally.
- Add dedupe/cooldown for repeated `BASE_UNDER_ATTACK` player notifications during one sustained attack wave.
- Add task/expert expand-collapse UI follow-up only after the current runtime truth issues are green.
- Add the next live E2E issue chain only after the rerun produces a clean reproducible session.

## Blocked

- None.
