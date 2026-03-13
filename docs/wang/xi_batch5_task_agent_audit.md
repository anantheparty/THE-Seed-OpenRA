# Audit — xi `1.3f` Task Agent error recovery (`2dc268f`)

## Scope

- `task_agent/agent.py`
- `tests/test_task_agent.py`

## Verdict

Not `zero blockers`.

## Findings

### Blocker 1 — repeated LLM failure does not reach any real player-notification channel

Files:
- `task_agent/agent.py:345-361`
- `task_agent/handlers.py:114-116`
- `docs/wang/design.md:350-377`
- `docs/wang/design.md:487-490`

`design.md` makes this a real contract: repeated Task-Agent LLM failure must notify the player, and Task-originated player messages must go through structured `TaskMessage` output (`task_warning`, `task_info`, `task_question`, `task_complete_report`).

The current implementation does not do that. `_notify_player_llm_failure()` calls:

- tool name: `complete_task`
- arguments: empty string / `{}`

This is not a warning path. In the live handler stack, `complete_task` requires `result` and `summary`; `TaskToolHandlers.handle_complete_task()` reads `args["result"]` and `args["summary"]`. So this "notification" call becomes a handler error and produces no `TaskMessage`, no player-facing warning, and no Kernel-side state change. The function then silently falls back to logging:

> `LLM repeated failure — player should be notified ...`

That means the required recovery behavior is still missing on the Task-Agent side. The system only logs diagnostics; it does not actually notify the player.

### Should-fix 1 — the new `error_isolation` test does not exercise `_safe_wake_cycle()`'s intended failure class

Files:
- `task_agent/agent.py:150-166`
- `task_agent/agent.py:278-302`
- `tests/test_task_agent.py:588-625`

`test_single_agent_error_isolation()` raises inside `llm.chat()`. That path is already caught by `_call_llm()` and converted into a normal `None` response, so the test never passes through `_safe_wake_cycle()`'s generic `except Exception` path.

So the implementation claim and the test name do not line up:

- implemented path: `_safe_wake_cycle()` should isolate unexpected non-LLM exceptions
- tested path: `_call_llm()` retry/failure handling

I did a live repro with a flaky `world_summary_provider` that throws once and then recovers; `_safe_wake_cycle()` did isolate the exception and the agent later completed successfully. So this is a coverage gap, not a blocker, but the test should be corrected to match the requirement.

## Verification

- `python3 tests/test_task_agent.py` → `16 passed`
- `python3 -m py_compile task_agent/agent.py tests/test_task_agent.py`
- Live repro: repeated LLM failure path produced only two bogus `complete_task({})` attempts plus the final auto-fail `complete_task(result="failed", ...)`
- Live repro: `ToolExecutor.execute("complete_task", "")` with real `TaskToolHandlers` returns error `'result'`
- Live repro: a one-shot `world_summary_provider` exception is isolated by `_safe_wake_cycle()`, and the agent recovers on the next wake

## Conclusion

`1.3f` Task Agent error recovery is close, but not done. The failure counter, reset-on-success, auto-terminate, and unexpected-exception isolation are substantively there. The remaining blocker is that "连续失败 → 通知玩家" is still only a log message, not a real structured player-notification path.
