# Yu Plan

Updated: 2026-04-16 17:10

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Return to Capability goal-completion / clear conditions

- Problem: the latest Adjutant routing regressions are now green again, but Capability still behaves too serially on explicit multi-produce player commands, and goal completion / clear semantics still need tightening so capability-owned execution stops cleanly instead of hanging or idling too early.
- Goal: tighten Capability prompt semantics so explicit independent multi-produce commands may issue multiple safe tool calls in one wake, without reopening autonomous expansion drift.
- Exit criteria:
  - Capability prompt clearly distinguishes broad-goal minimal progression from explicit multi-item commands
  - explicit independent multi-produce commands are allowed to emit multiple safe tool calls in one wake
  - focused fixes preserve capability-owned single-step NLU routing semantics
  - verification covers prompt contract and targeted regression tests

## Queue

- Clarify EconomyCapability `idle` semantics separately from `capability_status.phase=idle`; decide whether no-new-command should suppress all autonomous follow-through or only planner wake-ups.
- Keep economy ownership semantics green: retain capability-owned single-step NLU job coverage while the next E2E round lands.
- Keep voice compatibility green: retain frontend `wav` upload coverage and backend fallback coverage while the next E2E round lands.
- Keep Adjutant attack grounding green: generic enemy-base attack/harass commands should continue to use frozen positions and not regress to visible-target false negatives.
- Fix the task-question cancel/reply UI so buttons only disable after a successful websocket send; current send-failure path can strand the operator locally.
- Add dedupe/cooldown for repeated `BASE_UNDER_ATTACK` player notifications during one sustained attack wave.
- Add task/expert expand-collapse UI follow-up only after the current runtime truth issues are green.
- Add the next live E2E issue chain only after the rerun produces a clean reproducible session.

## Blocked

- None.
