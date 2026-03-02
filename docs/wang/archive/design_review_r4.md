# Round 4 final audit of `design.md`

## Verdict

Mainline recon flow is now implementable, and the 4 Round 3 gaps are addressed in substance. But this is **not yet zero blockers**. I still see **2 implementation-blocking gaps**, plus **2 non-blocking document inconsistencies**.

## Remaining blockers

### 1. Mid-task resource request / wait-queue contract is still internally inconsistent

Relevant lines:
- `PendingRequest` + wait queue: `docs/wang/design.md:353-375`
- `Expert.start(..., resource_callback)`: `docs/wang/design.md:585-592`
- timeout scenario: `docs/wang/design.md:1008-1016`

Problem:
- `Expert.start()` defines `resource_callback` as a synchronous API: `ResourceRequest -> list[int]`.
- But `PendingRequest.expert_callback` is described as an asynchronous callback used later when resources become available.
- `Kernel.check_wait_queue()` calls that callback with `assigned` actor IDs, not with a `ResourceRequest`.
- Timeout path only removes the pending request; it does not notify the Expert of failure.
- The scenario says "30s 后仍无可用 → wait_timeout_s 过期 → Kernel 通知 Expert 请求失败", but no API for that notification is specified.
- Related state ownership is also still undefined: who flips the job `RUNNING -> WAITING -> RUNNING/FAILED`, and who maintains `job.pending_requests`.

Why this still blocks implementation:
- Kernel and Expert cannot implement one stable contract for enqueue, fulfillment, timeout, and status transitions.
- Any implementation here still has to invent missing protocol details.

### 2. Preempt path is still not routed through one explicit standard outcome path

Relevant lines:
- arbitration entry: `docs/wang/design.md:473-490`
- preempt narrative: `docs/wang/design.md:529-537`
- preempt edge case: `docs/wang/design.md:1032-1040`
- standard terminal path: `docs/wang/design.md:465-471`, `docs/wang/design.md:492-497`

Problem:
- Cancel path is now clean: `Kernel.cancel()` gets `Outcome` from `expert.abort()` and routes it through `on_outcome()`.
- Preempt path is still only described as:
  - abort old expert
  - remove resource from old job
  - reassign resource to new job
- It never explicitly says whether the preempted job's `Outcome` must also go through `Kernel.on_outcome()`.
- That leaves dashboard update, blocked-task release, terminal status writeback, and resource cleanup partially implicit again.
- It also leaves one important branch unspecified: if a multi-resource job loses one resource to preemption, is that always terminal abort, or does it go through `on_resource_lost()` and continue degraded?

Why this still blocks implementation:
- Preemption is part of the main arbitration path in `on_resource_request()`.
- Without a single required path, implementation still has to guess between "terminal abort" and "partial resource loss" semantics.

## Non-blocking inconsistencies

### 1. Scenario `ExecutionJob(...)` example still omits required `intent`

Relevant lines:
- `ExecutionJob.intent` field exists: `docs/wang/design.md:215-231`
- Step 4 example omits it: `docs/wang/design.md:951-957`

This does not block the architecture anymore, but the walkthrough should include `intent="recon_find"` because cancel matching now depends on it.

### 2. `log_entry` is listed as an outbound event, but its example is not wrapped like the other events

Relevant lines:
- outbound event table: `docs/wang/design.md:814-820`
- log schema: `docs/wang/design.md:887-904`

`world_snapshot`, `task_update`, `task_list`, and `action_executed` all show an event envelope. `log_entry` currently only shows the payload body. That is probably easy to infer, but the doc should still make the wire shape explicit.

## Bottom line

Round 4 is a strong revision. The mainline recon design is coherent, and the original blocker class is gone. But I would **not** call this "zero blockers" yet. The remaining blocker count is **2**:

1. mid-task resource request / wait-queue protocol
2. preempt path terminal semantics
