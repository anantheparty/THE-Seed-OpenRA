# Yu Plan

Updated: 2026-04-13 14:02

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then Xi audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Adjutant Multi-Request Awareness

- Problem: `OpsPanel` can now show multiple in-flight request previews, but `Adjutant` still mostly reasons from a single `unit_pipeline_focus`.
- Goal: let the coordinator consume the compact multi-request preview so its summaries and replies stop lagging behind runtime/operator truth under concurrency.
- Exit criteria:
  - coordinator snapshot can see 2-3 concurrent request/reservation previews
  - coordinator summaries remain compact and do not duplicate the entire operator payload
  - focused `Adjutant` tests pin this multi-request awareness directly

## Queue

### 2. Live E2E: Owned-Unit Continuation

- Problem: the runtime truth and UI surfaces now describe task-owned units correctly, but there is still no live chain proving that a task can receive units and then continue controlling that same group.
- Goal: add one narrow live/mock-integration pin for “got units -> continue same task/group” so the recent truth-surface work is backed by a real control path.
- Exit criteria:
  - one representative live flow proves post-fulfillment continuation on the same task/group
  - the test stays narrow and does not reopen broad E2E mega-spec behavior
  - failure output is concrete enough to debug routing vs ownership vs execution separately

### 3. Docs / Knowledge Hygiene Follow-Through

- Problem: the main de-backlog cleanup is done, but future yu-owned docs still need to stay split cleanly between active backlog and durable facts.
- Goal: keep `plan.md` as the only active backlog and avoid reintroducing “next slice / remaining gap” language into knowledge docs.
- Exit criteria:
  - new yu docs follow the same separation
  - no new hidden backlog accumulates in knowledge/reference docs

## Blocked

- None.
