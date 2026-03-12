# Audit of xi `1.6 WS backend + 1.8 review_interval` (`2728463`)

## Scope
- `ws_server/server.py`
- `game_loop/loop.py`
- `tests/test_ws_and_review.py`

## Verification
- Ran `python3 tests/test_ws_and_review.py` and confirmed all `7` tests pass.
- Re-ran `python3 tests/test_game_loop.py` and confirmed all `7` existing GameLoop tests still pass.
- Ran `python3 -m py_compile ws_server/server.py game_loop/loop.py tests/test_ws_and_review.py`.
- Ran two focused repros against the live code:
  - real `Kernel + GameLoop` repro for pending-question timeout
  - `AgentQueue.wait_for_wake()` repro for pre-set review wakes

## Findings

### 1. Blocker — `GameLoop` never calls `Kernel.tick()`, so Batch 4 pending-question timeouts do not run in the live main loop
- File: `game_loop/loop.py:34-38`, `game_loop/loop.py:191-199`
- `Kernel` now owns deterministic pending-question timeout handling via `register_task_message()`, `submit_player_response()`, and especially `tick()` in `kernel/core.py:407-498`.
- But `GameLoop`'s `KernelInterface` still exposes only `route_events(...)`, and `_tick()` never calls `kernel.tick(now=...)`.
- Impact: the new Batch 4 timeout path is dead in the live loop. Questions remain pending forever unless some other caller manually invokes `Kernel.tick()`.
- Live repro result:
  - created a real `Kernel`, registered a `task_question` with `timeout_s=0.05`
  - ran the real `GameLoop` for ~120ms
  - after stop, `kernel.list_pending_questions()` still contained the expired question
  - the Task Agent had received no default `PlayerResponse`

### 2. Blocker — review wakes can be dropped because `GameLoop` pokes `AgentQueue._wake_event` directly, but `wait_for_wake()` clears pre-existing state before waiting
- File: `game_loop/loop.py:218-229`, `task_agent/queue.py:41-55`
- `GameLoop._check_agent_reviews()` triggers review by calling `reg.agent_queue._wake_event.set()` directly.
- `AgentQueue.wait_for_wake()` starts by calling `self._wake_event.clear()` before awaiting.
- That means a review wake that arrives just before the agent re-enters `wait_for_wake()` is lost, and the agent can sleep for another full `review_interval`.
- Minimal repro on the live queue:
  - pre-set `_wake_event`
  - immediately call `await wait_for_wake(timeout=0.05)`
  - result is `False`, not `True`
- The current tests do not catch this because they exercise `AgentQueue` directly rather than the real `TaskAgent.wait_for_wake()` timing edge.

## Passes Confirmed
- `ws_server/server.py` does implement the requested inbound trio (`command_submit`, `command_cancel`, `mode_switch`) and the six outbound message types.
- WS outbound envelopes do include top-level `timestamp`.
- Multi-client broadcast works in the tested happy path.
- `review_interval` registration / unregistration APIs exist and their basic smoke tests pass.

## Conclusion
Not `zero-gap`. I see `2 blockers`, both on the `1.8 review_interval` integration path.
