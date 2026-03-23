# Live Test Round 5

## Scope
- Wang live E2E follow-up on production naming, production queue behavior, WS payload size, and reconnect stability.
- Additional live issue reported by user: power plants kept finishing in the building queue without being placed.

## Results

### Issue 8 — production name mismatch
- Root cause: live runtime can receive LLM-produced CamelCase names like `PowerPlant`, while the game-side alias table accepts lowercase English aliases / Chinese names / internal codes.
- Fix: added shared production-name normalization and alias expansion from `OpenCodeAlert/mods/common/Copilot.yaml`, then reused it in both `GameAPI` and `EconomyJob`.
- Verified:
  - `python3 tests/test_game_api.py`
  - `python3 tests/test_economy_expert.py`
  - live direct GameAPI check: `GameAPI.can_produce("PowerPlant") == True`
  - live direct GameAPI check: `GameAPI.produce("PowerPlant", 1, True)` returns a real wait id

### Building queue blockage / missing placement
- Root cause 1: `EconomyJob` issued building production with default `auto_place_building=False`.
- Root cause 2: `EconomyJob` treated only `in_progress/waiting` queue items as meaningful state, so a `Building` queue with `has_ready_item=True` was neither auto-cleared nor surfaced as blocked.
- Fix:
  - `EconomyJob` now calls `produce(..., auto_place_building=True)` for `queue_type="Building"`.
  - If the building queue contains a matching ready item, `EconomyJob` now calls `place_building("Building")`.
  - If the building queue is jammed by some other ready item, `EconomyJob` now emits a blocked reason: `queue_ready_item_pending`.
- Live verification:
  - Before direct live repro: `query_production_queue("Building")` returned one ready `powr` plus queued followers.
  - I instantiated a live `EconomyJob(unit_type="PowerPlant", queue_type="Building")` against the current `GameAPI + WorldModel`.
  - After one `tick()`, the ready item was placed and the queue changed from `has_ready_item=True` to `has_ready_item=False`.
  - Follow-up `query_production_queue("Building")` returned an unblocked queue, then later an empty queue.

### Issue 9 — `query_production_queue("Building")`
- Current code no longer reproduces the failure.
- Live verification:
  - `GameAPI.query_production_queue("Building")` returned a valid dict with queue contents.

### Issue 10 — WS frame size
- Current live `world_snapshot` is far below 1 MB.
- Live verification:
  - via `aiohttp` WS client after `sync_request`, first `world_snapshot` frame size was `863` bytes.
- Conclusion:
  - browser WS isn't currently hitting a payload-size blocker in this environment.
  - Python probe scripts can still pass a larger `max_size` defensively, but there is no current live evidence that frontend traffic needs a payload-size fix.

### Issue 11 — reconnect / restart stability
- Current backend restarts no longer reproduce the previous stale-connection symptom.
- Live verification:
  - restarted `main.py` multiple times during this round
  - post-restart `GameAPI.query_production_queue("Building")` and `WorldModel.refresh()` succeeded
  - no fresh `COMMAND_EXECUTION_ERROR` loop appeared after restart

## Commits
- pending local commit(s) at time of writing for this round's fixes
