# Regression Audit of xi `1.5 Task tools + 1.7 timestamp` fix (`99a9291`)

## Scope
- `task_agent/handlers.py`
- `tests/test_tool_handlers.py`

## Verification
- Ran `python3 tests/test_tool_handlers.py` and confirmed all `9` tests pass.
- Re-ran a live integration repro with the current `Kernel` + `WorldModel` + `TaskToolHandlers` to verify real `create_constraint` / `remove_constraint` side effects against runtime state.

## Findings

### Cleared — constraint handlers now mutate real state
- File: `task_agent/handlers.py:120-136`
- `handle_create_constraint()` now calls `self.world_model.set_constraint(constraint)`.
- `handle_remove_constraint()` now calls `self.world_model.remove_constraint(constraint_id)`.
- Live repro result:
  - after `create_constraint`, `world.query("constraints")` contains the new constraint
  - after `remove_constraint`, `world.query("constraints")` returns an empty list

### Cleared — WorldModel-side constraint contract is now explicit
- File: `task_agent/handlers.py:42-47`
- `WorldModelLike` now declares `set_constraint()` and `remove_constraint()`, so the handler surface matches the live dependency it actually uses.

### Cleared — tests now verify side effects, not only timestamps
- File: `tests/test_tool_handlers.py:259-297`
- Added `test_constraint_handlers_side_effects()` covering create → stored in `MockWorldModel.constraints` → remove → deleted.
- The suite total increased from `8` to `9` tests and passes locally.

## Passes Confirmed
- `register_all()` still wires all 11 tools.
- `start_job()` still uses `EXPERT_CONFIG_REGISTRY` to build config objects.
- `query_world()` still maps supported query types into `WorldModel.query()`.
- All handler responses still include `timestamp`, satisfying the `1.7` requirement.

## Conclusion
`Zero blockers` for the previously reported `1 blocker + 1 should-fix`. This closes my audit findings on xi's `1.5 Task tools + 1.7 timestamp`.
