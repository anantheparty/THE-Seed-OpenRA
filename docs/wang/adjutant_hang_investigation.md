# Adjutant LLM Hang Investigation

## Conclusion

The hang is **not** caused by Qwen credentials or DashScope reachability.  
The root cause is that `main.py` runs **Adjutant async LLM calls and GameLoop blocking game I/O on the same asyncio event loop**.

When `GameLoop` is active, it calls synchronous `WorldModel.refresh()` and synchronous job ticks directly inside `async def _tick()`. Those paths use blocking socket I/O and `time.sleep()`. If a game-state query is slow, the event loop is starved, so:

- `await self.adjutant.handle_player_input(...)` does not make progress
- `AsyncOpenAI` / Qwen coroutines do not get scheduled
- even `asyncio.wait_for(..., timeout=...)` in Adjutant does not fire on time, because the loop itself is blocked

So the symptom appears as “Adjutant LLM call never returns in `main.py`”, while standalone `QwenProvider().chat()` works.

## Direct Evidence

### 1. `main.py` puts Adjutant and GameLoop on the same loop

- [main.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/main.py#L444) starts `GameLoop` via `asyncio.create_task(self.game_loop.start())`
- [main.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/main.py#L212) handles WS input by `await self.adjutant.handle_player_input(text)`

That means player-input handling and the tick loop share one asyncio event loop thread.

### 2. Adjutant does have timeouts, but they depend on a healthy event loop

- [adjutant.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/adjutant/adjutant.py#L181) wraps classification with `asyncio.wait_for(...)`
- [adjutant.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/adjutant/adjutant.py#L311) wraps query answering with `asyncio.wait_for(...)`

These timeouts only work if the event loop can keep scheduling tasks. If the loop is blocked in synchronous code, the timeout callback is also delayed.

### 3. `GameLoop` executes blocking work inside `async def _tick()`

- [loop.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/game_loop/loop.py#L196) enters `_tick()`
- [loop.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/game_loop/loop.py#L203) calls `self.world_model.refresh(now=now)` synchronously
- [loop.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/game_loop/loop.py#L220) then calls `_tick_jobs(now)`, which may also run synchronous expert/GameAPI work

There is no `await asyncio.to_thread(...)`, no executor handoff, and no separate thread/process for the game-control side.

### 4. `WorldModel.refresh()` fans out to synchronous `GameAPI` socket calls

- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L201) `fetch_self_actors()`
- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L202) `fetch_enemy_actors()`
- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L216) `fetch_economy()`
- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L217) `fetch_production_queues()`
- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L229) `fetch_map()`

The real source is [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L99):

- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L105) `query_actor(self)`
- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L108) `query_actor(enemy)`
- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L111) `player_base_info_query()`
- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L114) `map_query()`
- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/world_model/core.py#L117) loops over 5 `query_production_queue(...)`

### 5. `GameAPI` itself is blocking and can stall for a long time

- [game_api.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/openra_api/game_api.py#L124) retries up to `MAX_RETRIES = 3`
- [game_api.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/openra_api/game_api.py#L128) each request sets `sock.settimeout(10)`
- [game_api.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/openra_api/game_api.py#L171) retries use `time.sleep(RETRY_DELAY)`

So one slow request can block for roughly `10s * 3 + sleep backoff`.
`WorldModel.refresh()` may issue multiple such requests in one tick, so a bad tick can block the loop for a very long time.

## Reproduction I Ran

### Standalone Adjutant works

Using a fake async LLM that only does `await asyncio.sleep(0.05)`, `Adjutant.handle_player_input("战况如何？")` returns in about `0.10s` when no `GameLoop` is running.

### Same Adjutant hangs once `GameLoop` blocks the loop

With the same fake async LLM, but a `GameLoop` whose `world_model.refresh()` only does `time.sleep(0.3)`, the LLM call stops making progress and never returns in a timely way. The process logs repeated:

`Tick N took ~300ms (budget 100ms)`

This reproduces the exact failure class without involving Qwen at all, which rules out Qwen-specific API behavior as the primary root cause.

### `ApplicationRuntime`-shaped repro

I also ran an `ApplicationRuntime` repro:

- `FastWorldSource` + fake async LLM: `runtime.adjutant.handle_player_input("战况如何？")` returned in about `0.10s`
- `BlockingWorldSource` + same fake async LLM: the runtime got stuck in repeated over-budget ticks and the player-input coroutine did not complete before an external process alarm fired

## Root Cause Statement

The real root cause is:

> `main.py` mixes async LLM/WS coroutines with synchronous game polling and control on the same asyncio event loop.

The symptom is most visible on Adjutant because it awaits a network coroutine (`AsyncOpenAI`) that needs regular event-loop scheduling. But the architecture issue is broader: TaskAgent LLM calls are exposed to the same starvation pattern whenever `GameLoop` is doing slow synchronous world/job work.

## Recommended Fix

### Best fix

Move the entire GameLoop/game-I/O side off the main asyncio loop.

Concretely:

1. Keep WS server, Adjutant, TaskAgent LLM calls, and provider HTTP clients on the main asyncio loop.
2. Run `GameLoop` in a dedicated thread (or dedicated worker loop) that owns:
   - `WorldModel.refresh()`
   - expert/job `do_tick()`
   - synchronous `GameAPI` calls
3. Bridge cross-thread communication with explicit thread-safe queues/callbacks.

This cleanly separates:

- **async dialogue/LLM I/O**
- **blocking RTS control/polling**

### Smaller temporary mitigation

Wrap blocking `GameLoop` work in `asyncio.to_thread(...)`.

This is less attractive because `Kernel`, `WorldModel`, and job controllers are not currently designed around explicit thread ownership/locking, so it is easier to create race conditions unless the whole tick is treated as one worker-owned critical section.

### Hardening follow-up

Even after fixing the main issue, add explicit provider request timeouts at the HTTP client level as defense in depth. That will help real network failures fail fast, but it will **not** solve the current hang by itself.

## Suggested Next Step

If Wang wants the fastest safe path:

1. Introduce a dedicated GameLoop worker thread.
2. Keep `RuntimeBridge.on_command_submit()` and `Adjutant` on the main asyncio loop.
3. Add a regression test that proves:
   - with a blocking world source, Adjutant still returns within timeout
   - over-budget GameLoop ticks no longer starve LLM coroutines
