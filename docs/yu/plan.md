# Yu Plan

Updated: 2026-04-17 01:10

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Re-run operator-wide pickup dry run and close the remaining force-package chain

- Problem: the kernel-side operator-wide pickup chain now has both startup gating and late idle pickup recovery. The next step is not more architecture work; it is a clean dry run to confirm the live complaint is actually gone and to capture any residuals before reopening adjacent slices.
- Goal: validate `全员出击` / `现有单位也移动过去` style commands against the latest runtime and remove this chain from the active mainline if it stays green.
- Exit criteria:
  - either confirm operator-wide pickup is stable in the next dry run and delete this chain from `Current`
  - or record the exact residual with task/log evidence before taking another code slice
  - do not reopen generic combat planning, Adjutant routing, or unrelated E2E issues until that validation is done

## Queue

- Revisit mixed economy/combat routing only if a fresh E2E still shows economy-first half execution after the managed-workflow handoff.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
