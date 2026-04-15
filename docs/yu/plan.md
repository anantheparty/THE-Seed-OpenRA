# Yu Plan

Updated: 2026-04-16 03:38

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Return to Capability goal-completion / clear conditions

- Problem: the NLU overreach slice is green again, but Capability goal completion / clear semantics still need tightening so capability-owned execution stops cleanly instead of hanging or idling too early.
- Goal: resume the capability completion chain after the runtime NLU fix, keeping current ownership semantics intact.
- Exit criteria:
  - capability goal completion / clear conditions are traced to concrete runtime fields and close paths
  - focused fixes preserve capability-owned single-step NLU routing semantics
  - verification covers both direct runtime behavior and targeted regression tests

## Queue

- Clarify EconomyCapability `idle` semantics separately from `capability_status.phase=idle`; decide whether no-new-command should suppress all autonomous follow-through or only planner wake-ups.
- Keep economy ownership semantics green: retain capability-owned single-step NLU job coverage while the next E2E round lands.
- Keep voice compatibility green: retain frontend `wav` upload coverage and backend fallback coverage while the next E2E round lands.
- Fix the task-question cancel/reply UI so buttons only disable after a successful websocket send; current send-failure path can strand the operator locally.
- Add dedupe/cooldown for repeated `BASE_UNDER_ATTACK` player notifications during one sustained attack wave.
- Add task/expert expand-collapse UI follow-up only after the current runtime truth issues are green.
- Add the next live E2E issue chain only after the rerun produces a clean reproducible session.

## Blocked

- None.
