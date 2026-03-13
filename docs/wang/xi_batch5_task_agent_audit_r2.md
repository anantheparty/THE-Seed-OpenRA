# Regression Audit — xi `1.3f` final fix (`72c3c21`)

## Scope

- `task_agent/agent.py`
- `tests/test_task_agent.py`

## Verdict

Still not `zero blockers`.

## Findings

### Blocker 1 — `RISK_ALERT` is emitted only into the Task Agent's own queue, so no player-visible notification is produced

Files:
- `task_agent/agent.py:345-366`
- `kernel/core.py:351-364`
- `kernel/core.py:424-440`
- `docs/wang/design.md:350-377`
- `docs/wang/design.md:487-490`

The previous bogus `complete_task({})` warning path is gone, which is an improvement. But the replacement still does not satisfy the design contract.

`_notify_player_llm_failure()` now constructs:

- `ExpertSignal(kind=RISK_ALERT, ...)`

and then does:

- `self.queue.push(signal)`

That only feeds the signal back into the same Task Agent's inbound queue. On the next wake it becomes `recent_signals` inside the context packet, but there is still no outward path from Task Agent to Kernel / Adjutant / dashboard:

- `Kernel.route_signal()` is one-way `Kernel -> TaskAgent`
- Task Agent has no signal callback / message callback back into Kernel
- no `register_task_message(...)`
- no `push_player_notification(...)`

So the repeated-failure warning is still not player-visible. It is only internal context for the same agent.

I verified this with a live `Kernel + TaskAgent + WorldModel` repro using an always-failing LLM:

- task reaches `failed`
- `kernel.list_task_messages(task_id)` remains empty
- `kernel.player_notifications` remains empty

That means the required "连续失败 -> 通知玩家" behavior is still not implemented on the Task-Agent side.

## Closed from previous round

### Closed — the `error_isolation` test now targets the correct failure class

Files:
- `tests/test_task_agent.py:588-634`
- `task_agent/agent.py:150-166`

The test now raises from `world_summary_provider`, which is outside `_call_llm()` and therefore actually exercises `_safe_wake_cycle()`'s generic exception isolation path. This matches the implementation claim.

I also re-ran a live repro and confirmed the behavior:

- first wake crashes in `world_summary_provider`
- `_safe_wake_cycle()` catches it
- next wake succeeds
- agent later completes normally

## Verification

- `python3 tests/test_task_agent.py` -> `16 passed`
- `python3 -m py_compile task_agent/agent.py tests/test_task_agent.py`
- Live repro: `Kernel + TaskAgent + WorldModel` with always-failing LLM
  - final task status: `failed`
  - `kernel.list_task_messages(task_id)` -> `[]`
  - `kernel.player_notifications` -> `[]`

## Conclusion

This fix closes the test-accuracy issue, but not the functional blocker. The Task Agent now emits the right *kind* of signal, but still emits it only to itself. Until that warning is routed into a real Kernel-managed outward channel, `1.3f` is not ready to close.
