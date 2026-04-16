# Yu Plan

Updated: 2026-04-17 18:05

## Mainline Rules

- `Adjutant` remains the current top-level coordinator. Do not start a near-term `Commander` implementation track.
- `EconomyCapability` remains the only active Capability mainline. New work should strengthen it rather than spawn parallel capability families.
- `TaskAgent` is a bounded managed-task reasoner, not the default brain. Ordinary tasks must not bypass Capability by self-building prerequisites.
- Work one theme chain at a time: product slice first, then focused tests/gates, then audit, then move to the next slice.
- Completed checkpoints belong in [`docs/yu/progress.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/yu/progress.md), not here.
- Live/mock-integration coverage is an exit criterion for product slices, not a standalone backlog theme.

## Current

### 1. Remove remaining ordinary-task combat prompt/tool drift after unowned-force rejection

- Scope: continue the managed combat/recon hardening chain after blocking `query_world(my_actors)` without owned units.
- Goal: make ordinary managed combat/recon tasks fail closed into `request_units / wait / clarify` instead of drifting through permissive tool descriptions or coarse global context after an ownership-bound action is rejected.
- Exit criteria:
  - task-agent tool/prompt surfaces no longer imply generic combat fallback for ordinary tasks
  - the next change is pinned by focused tests only, without reopening broad prompt-string mega-specs
  - clean head for this theme is based on focused owner suites, not unrelated prompt-fragment assertions

## Queue

- Normalize shorthand economy composites like `兵营3步兵` onto the capability path is done; if parity with explicit runtime-NLU composite sequence is ever needed, treat that as a separate enhancement rather than re-opening this stable fallback.
- After the prompt/tool surface is tightened, re-audit operator-wide combat/retreat E2E with attention to task preemption, partial-group startup, and retreat completion semantics.
- Keep voice/UI/debug polish and non-truth-facing cleanup out of the mainline unless it blocks the next E2E.

## Blocked

- None.
