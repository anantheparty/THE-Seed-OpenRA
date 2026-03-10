# Regression Audit: xi fix `001feec`

Targeted areas:
- `experts/base.py`
- `game_loop/loop.py`
- `tests/test_game_loop.py`

## Result

Not `zero blockers`.

The original three audit items are mostly addressed, but one new state-machine blocker remains in `BaseJob.pause()/resume()`.

## Closed items

### 1. `on_resource_revoked()` terminal-state protection
Status: fixed

`experts/base.py` now protects terminal states in `on_resource_revoked()` and no longer overwrites `ABORTED / SUCCEEDED / FAILED` to `WAITING` when the last resource is removed.

I rechecked the original failure mode and that specific path is closed.

### 2. GameLoop event double-counting
Status: fixed

`game_loop/loop.py` now uses `WorldModel.refresh()` only to advance state and then reads events once from `detect_events(clear=True)`.

I reran the earlier repro against the current `WorldModel` + `GameLoop` contract and the duplicate routing is gone:

```text
routed 7 ['ENEMY_DISCOVERED', 'UNIT_DAMAGED', 'UNIT_DAMAGED',
          'ENEMY_EXPANSION', 'BASE_UNDER_ATTACK',
          'PRODUCTION_COMPLETE', 'ECONOMY_SURPLUS']
```

That is the expected single copy of the current event set.

### 3. `pause()/resume()` now update `status`
Status: partially fixed

The direct stale-status problem is fixed: `pause()` now sets `WAITING` and `resume()` now sets `RUNNING`.

## Remaining blocker

### 4. `resume()` can resurrect terminal jobs
- File: `experts/base.py`
- Lines: `154-158`
- Severity: blocker

`resume()` now unconditionally sets `status = RUNNING`, even for jobs already in terminal states. So an `ABORTED` job can be revived by a later `resume()` call.

Minimal repro I ran locally:

```python
job.abort()
print(job.status.value)   # aborted
job.resume()
print(job.status.value)   # running
```

This violates the task/job terminal-state model in `design.md`. Once a job is `aborted / succeeded / failed`, `pause()` and `resume()` should not be able to move it back into an active state.

## Test coverage notes

### 5. The new blocker is not covered by tests
- Files: `tests/test_expert_base.py`
- Severity: should-fix

The test suite still does not cover:
- `abort()` followed by `resume()`
- `succeeded/failed` followed by `resume()`

So the regression introduced by the new `pause()/resume()` implementation is currently invisible to the tests.

## Verification run

- `python3 tests/test_expert_base.py` -> all 11 passed
- `python3 tests/test_game_loop.py` -> all 7 passed
- local runtime repro confirms:
  - old `on_resource_revoked()` terminal overwrite bug is fixed
  - GameLoop duplicate event routing is fixed
  - new terminal-state resurrection bug exists in `resume()`

## Verdict

- `game_loop/loop.py`: clear on the requested fix points
- `experts/base.py`: not clear yet

Overall: `1 blocker + 1 should-fix`.
