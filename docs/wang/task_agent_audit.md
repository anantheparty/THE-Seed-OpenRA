# Task Agent Audit

Audited target:
- xi commit `d321a3e`
- `task_agent/agent.py`
- `task_agent/context.py`
- `task_agent/tools.py`
- `task_agent/queue.py`
- `tests/test_task_agent.py`

Validation run:
- `python3 tests/test_task_agent.py`
- Result: 11 tests passed

## Findings

### 1. Routed `Event` objects wake the agent but never reach the LLM context
- Severity: blocker
- Files:
  - `task_agent/agent.py:149-167`
  - `task_agent/context.py:28-37`
  - `task_agent/context.py:40-129`
  - `docs/wang/design.md:185-188`
  - `docs/wang/design.md:300-311`

`TaskAgent._wake_cycle()` drains both signals and events, but only signals are propagated into the context packet:

- events are collected at `task_agent/agent.py:152`
- then ignored
- `build_context_packet()` has no `events` field at all

That means GameLoop/Kernel can route a `WorldModel Event` to a Task Agent, but the wake only changes timing, not information. The LLM never sees which event triggered the wake.

This breaks the core design path where routed events are supposed to influence decisions, for example:
- `ENEMY_DISCOVERED`
- `STRUCTURE_LOST`
- `BASE_UNDER_ATTACK`

The current tests only verify that events can sit in and come out of the queue; they do not verify that events are included in the LLM-facing context or affect behavior.

Recommended fix:
- add `recent_events` (or equivalent) to `ContextPacket`
- serialize routed events into the context message
- add at least one end-to-end test where `push_event(...)` wakes the agent and the LLM receives the event payload

### 2. `default_if_timeout` is not actually applied on LLM failure
- Severity: blocker
- Files:
  - `task_agent/agent.py:177-181`
  - `task_agent/agent.py:271-282`
  - `docs/wang/design.md:206`
  - `docs/wang/design.md:487`

The implementation claims support for `default_if_timeout`, but on LLM timeout/failure the code only logs the default:

- `_call_llm()` returns `None`
- `_wake_cycle()` calls `_apply_defaults(open_decisions)`
- `_apply_defaults()` logs the default and explicitly says the real application is deferred to later work

So in the failure path, no decision is actually emitted, stored, or applied. The agent therefore does not “use default_if_timeout to continue”; it only records a log line.

This is not just an integration TODO. It is the exact failure-path behavior the design calls out for the Task Agent loop.

Recommended fix:
- route the selected default into a real callback / handler / synthetic tool result
- or explicitly narrow the Phase 1.4 contract so the feature is not claimed yet
- add a test where the LLM times out with an open decision and the default path produces a concrete side effect

### 3. `create_constraint` does not enforce the required `enforcement` argument from the design contract
- Severity: should fix
- Files:
  - `task_agent/tools.py:120-135`
  - `docs/wang/design.md:218`

The design contract for `create_constraint` is:

`create_constraint(kind, scope, params, enforcement)`

But the tool schema marks only `kind`, `scope`, and `params` as required. `enforcement` is optional in the implementation.

That leaves room for the LLM to emit an underspecified constraint even though the runtime contract treats `clamp` vs `escalate` as semantically important.

Recommended fix:
- add `"enforcement"` to the required list
- add a schema-level regression test for it

## Confirmed Good

- The raw-SDK multi-turn loop shape matches the intended architecture direction
- `review_interval` wake behavior exists and is covered by tests
- Tool execution is cleanly separated via `ToolExecutor`
- The benchmark span around LLM calls and tool execution is present
- The 11 provided tests do pass locally

## Verdict

Not zero blockers.

The main remaining problems are:
- routed events are dropped before reaching the LLM context
- `default_if_timeout` is logged but not actually applied

Those two issues block me from clearing the Task Agent loop as implementation-ready.
