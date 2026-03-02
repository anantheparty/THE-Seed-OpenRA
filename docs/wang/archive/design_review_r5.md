# Round 5 final audit of `design.md`

## Verdict

**Zero blockers.**

The 2 Round 4 blockers are now closed in a way that is concrete enough to implement:

1. Mid-task resource requests now have a stable contract via `ResourceRequester.request()` plus explicit `Expert.on_resource_granted()` / `Expert.on_resource_wait_expired()` callbacks, with Kernel-owned `RUNNING ↔ WAITING` transitions documented.
2. Preemption now has a clean semantic split:
   - single-resource victim job → `abort()` + `Kernel.on_outcome()` standard terminal path
   - multi-resource victim job → `on_resource_lost()` degraded-continue path

The 2 Round 4 minor issues are also addressed in substance:
- `ExecutionJob(...)` example now includes `intent`
- `log_entry` now has an outbound wire envelope

## What I checked

- Resource request protocol and wait-queue callbacks: `docs/wang/design.md:348-399`
- Cancel / preempt semantics: `docs/wang/design.md:515-580`
- Expert interface updates: `docs/wang/design.md:624-650`
- Dashboard outbound schemas including `log_entry`: `docs/wang/design.md:857-949`
- Mainline recon scenario and edge cases: `docs/wang/design.md:985-1087`

## Non-blocking editorial nits

These do not block implementation, but the doc still has 2 small stale walkthrough references:

1. Step 4 still says `expert.start(task, world, [57], resource_callback)` instead of `resource_requester`: `docs/wang/design.md:995`
2. The dead-scout edge-case walkthrough still says `self._resource_callback(...)` instead of `self._resource_requester.request(...)`: `docs/wang/design.md:1054-1058`

## Bottom line

This is ready to implement from the architecture/spec side. I would close the audit loop here and treat any further changes as normal implementation refinements rather than spec blockers.
