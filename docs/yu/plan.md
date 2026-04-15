# Yu Plan

Updated: 2026-04-16 16:45

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Fix browser voice recording regression to malformed webm uploads

- Problem: browser ASR is regressing back to backend ffmpeg fallback, and the uploaded `webm` blob is sometimes malformed/truncated (`invalid as first byte of an EBML number`), which means the preferred frontend WAV path is not reliably reached.
- Goal: harden frontend recording flush/validation so browser uploads are either valid enough for frontend WAV conversion or fail locally with a clear message instead of shipping broken `webm` to `/api/asr`.
- Exit criteria:
  - malformed/empty browser recordings are blocked before upload
  - `MediaRecorder` stop path explicitly flushes final data
  - targeted frontend verification passes

## Queue

- Return to Capability goal-completion / clear conditions after the voice slice is green.
- Keep economy ownership semantics green: retain capability-owned single-step NLU job coverage while the next E2E round lands.
- Keep voice compatibility green: retain frontend `wav` upload coverage and backend fallback coverage while the next E2E round lands.
- Fix the task-question cancel/reply UI so buttons only disable after a successful websocket send; current send-failure path can strand the operator locally.
- Add dedupe/cooldown for repeated `BASE_UNDER_ATTACK` player notifications during one sustained attack wave.
- Add task/expert expand-collapse UI follow-up only after the current runtime truth issues are green.
- Add the next live E2E issue chain only after the rerun produces a clean reproducible session.

## Blocked

- None.
