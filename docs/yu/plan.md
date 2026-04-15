# Yu Plan

Updated: 2026-04-16 04:15

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Clarify EconomyCapability `idle` semantics separately from `capability_status.phase=idle`

- Problem: recent E2E still shows capability acting or surfacing confusing `待机/待命` truth around autonomous follow-through, while product intent is “副官不是 AI 指挥官”.
- Goal: separate “capability currently has no active jobs” from “capability is allowed to autonomously continue without a fresh player directive”, then harden runtime/prompt/operator truth accordingly.
- Exit criteria:
  - capability no-command behavior is explicitly defined in code, not only by prompt implication
  - no-directive / directive-pending / active-execution truth stays consistent across runtime projection, Adjutant summary, and UI
  - focused regression coverage is added
  - targeted verification is green

## Queue

- Keep economy ownership semantics green: retain capability-owned single-step NLU job coverage while the next E2E round lands.
- Keep voice compatibility green: retain frontend `wav` upload coverage and backend fallback coverage while the next E2E round lands.
- Keep Adjutant attack grounding green: generic enemy-base attack/harass commands should continue to use frozen positions and not regress to visible-target false negatives.
- If prompt hardening still proves too weak in the next E2E, add structured explicit-multi-target directive metadata so Capability does not have to infer batching only from free-form text.
- Propagate `directive_pending` semantics through the remaining operator-facing surfaces if the next E2E shows confusing wording or stale `待命` summaries.
- Fix the task-question cancel/reply UI so buttons only disable after a successful websocket send; current send-failure path can strand the operator locally.
- Add dedupe/cooldown for repeated `BASE_UNDER_ATTACK` player notifications during one sustained attack wave.
- Add task/expert expand-collapse UI follow-up only after the current runtime truth issues are green.
- Add the next live E2E issue chain only after the rerun produces a clean reproducible session.

## Blocked

- None.
