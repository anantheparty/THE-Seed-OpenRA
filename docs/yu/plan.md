# Yu Plan

Updated: 2026-04-16 00:55

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. EconomyCapability Persistent-Intent Follow-Up

- Problem: after the routing/prompt-truth slice, the next remaining E2E hotspot is EconomyCapability’s lazy/passive behavior: broad directives like `爆兵` or `造点载具` still degrade into one-shot batches and then idle, instead of maintaining a durable recovery/production intent until a visible milestone is reached or a blocker is surfaced.
- Goal: trace the current capability policy/runtime path for persistent economy directives, pin the exact stop condition from logs and code, and land the first bounded fix without reopening the routing/truth chain that just turned green.
- Exit criteria:
  - the `爆兵` / broad-economy idle-early behavior is reduced to a concrete root cause in code and logs
  - one bounded fix is implemented and verified with focused tests or log repro
  - any remaining medium-risk audit follow-ups from the previous slice are queued explicitly instead of left implicit

## Queue

- Fix the task-question cancel/reply UI so buttons only disable after a successful websocket send; current send-failure path can strand the operator locally.
- Add dedupe/cooldown for repeated `BASE_UNDER_ATTACK` player notifications during one sustained attack wave.
- Add task/expert expand-collapse UI follow-up only after the current runtime truth issues are green.
- Add the next live E2E issue chain only after the rerun produces a clean reproducible session.

## Blocked

- None.
