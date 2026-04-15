# Yu Plan

Updated: 2026-04-16 01:32

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Voice HTTP / CORS Reachability

- Problem: browser voice input/output is still vulnerable to Vite-dev cross-origin and preflight failures, which surface as `Failed to fetch` or non-JSON `404` before the backend handlers run.
- Goal: make `/api/asr` and `/api/tts` reachable from the dev frontend through explicit CORS/preflight support and verify the handlers through targeted tests plus a live HTTP probe.
- Exit criteria:
  - browser-facing ASR/TTS endpoints answer `OPTIONS` with the expected CORS headers
  - the backend handlers preserve those headers on normal responses
  - focused voice tests and a live preflight probe both pass

## Queue

- Return to Capability goal-completion / clear conditions after the direct-route contract is green.
- Fix the task-question cancel/reply UI so buttons only disable after a successful websocket send; current send-failure path can strand the operator locally.
- Add dedupe/cooldown for repeated `BASE_UNDER_ATTACK` player notifications during one sustained attack wave.
- Add task/expert expand-collapse UI follow-up only after the current runtime truth issues are green.
- Add the next live E2E issue chain only after the rerun produces a clean reproducible session.

## Blocked

- None.
