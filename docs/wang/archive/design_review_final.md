# Final audit of `design.md` + `test_scenarios.md`

## Verdict

The previous **6 checklist-consistency gaps are mostly closed**.

Current status:

1. **Proactive-notification triggers modeled in the runtime event layer**: closed
2. **Player-facing output channels for notifications and query replies**: closed
3. **`CommandProcessor` ownership / ingress classifier defined**: closed
4. **`query_world` vocabulary used by tests is formally specified**: closed
5. **Previous naming inconsistencies (`autonomy_mode`, query reply channel, etc.)**: closed in substance
6. **Checklist/test steps fully aligned with the document's strong-typed contract claim**: **not fully closed**

So this round does **not** show the older architecture blockers coming back. The remaining issues are narrower: a few test vectors still use shorthand or undeclared values rather than schema-complete, fully reproducible inputs.

## What is now closed

### 1. Proactive-notification triggers are now formally modeled

This was previously missing.

Now `design.md` declares the expanded `Event.type` set:

- `STRUCTURE_LOST`
- `ENEMY_EXPANSION`
- `FRONTLINE_WEAK`
- `ECONOMY_SURPLUS`

alongside the earlier event types.

That is enough to support T10 and the factory-destroyed edge case in T2. `test_scenarios.md` now uses those typed events consistently:

- T2 edge uses `STRUCTURE_LOST`
- T10 uses `ENEMY_EXPANSION` and `FRONTLINE_WEAK`

### 2. Player-facing outbound channels are now defined

This was previously missing from the dashboard/WebSocket section.

Now `design.md` explicitly defines outbound messages:

- `player_notification`
- `query_response`

and `test_scenarios.md` uses those channels consistently:

- T1 / T10 use `player_notification(...)`
- T11 uses `query_response(...)`

### 3. `CommandProcessor` is now explicitly part of the design

This was previously just referenced inside tests.

Now `design.md` defines:

- `入口分类器（CommandProcessor）`
- responsibility: classify player input using `NLU 模板 + LLM fallback`
- route selection: execute / query / proactive notification model

That is sufficient to support T1-T11's ingress expectations.

### 4. `query_world` vocabulary is now explicit

This was previously generic.

Now `design.md` enumerates the supported `query_type` values, including the ones the tests actually use:

- `my_actors`
- `my_combat_actors`
- `my_damaged_units`
- `enemy_bases`
- `enemy_threats_near`
- `repair_facilities`
- `unexplored_regions`
- `map_info`
- `active_tasks`

`test_scenarios.md` is now aligned with that vocabulary.

### 5. The earlier naming-level inconsistencies are mostly gone

The earlier checklist issues around `autonomy` vs `autonomy_mode` and missing output-channel names are largely cleaned up:

- tests now use `autonomy_mode`
- query replies now use `query_response`
- T10's notifications now map to a declared message type

I do not see a meaningful cross-file inconsistency in those areas anymore.

## Remaining issues

### 6. The “strongly typed test vector” gap is only partially closed

This is the one previous gap that is **not fully closed**.

The design still states that Job config is strongly typed and each Expert has its own schema. But several test steps in `test_scenarios.md` still use incomplete configs, placeholders, or undeclared symbolic values.

#### A. T6 mainline still uses an undeclared `last_attack_target` and an incomplete `CombatJobConfig`

T6 step 9 says:

- `start_job("CombatExpert", CombatJobConfig(target_position=last_attack_target, engagement_mode="assault"))`

Problems:

- `last_attack_target` is not introduced anywhere in T6 state
- `CombatJobConfig` schema in `design.md` also requires:
  - `max_chase_distance`
  - `retreat_threshold`

So this step is still not a schema-complete, self-contained test vector.

#### B. T7 mainline still omits one required `CombatJobConfig` field

T7 step 5 says:

- `CombatJobConfig(target_position=base_pos, engagement_mode="hold", retreat_threshold=0.2)`

But the schema also requires:

- `max_chase_distance`

So the test step is still shorthand rather than fully typed.

#### C. T8 edge case still omits a required `EconomyJobConfig` field

T8 edge step 4b says:

- `EconomyJobConfig(unit_type="mcv", count=1, queue_type="Vehicle")`

But the schema also lists:

- `repeat: bool`

Unless Wang intends `repeat` to be optional with a default, the test vector is still incomplete.

#### D. T9 still uses placeholders instead of executable vectors

T9 still contains shorthand:

- `start_job("EconomyExpert", ...)`
- `start_job("ReconExpert", ...)`
- `create_constraint(do_not_chase, global, ...)`

That is fine for an illustrative concurrency story, but not for a strict executable test script. So if `test_scenarios.md` is intended as a literal validation checklist, T9 remains under-specified.

## Cross-file consistency check

Aside from the remaining strong-typed test-vector issue above, I do not see a major contradiction between the two files.

The two documents now align on the previously missing structural points:

- event model
- command classification
- query vocabulary
- dashboard output channels
- no-command notification behavior

## Bottom line

If the question is:

- “Did the 6 previous gaps close?”

my answer is:

- **5 closed**
- **1 partially closed**

The only remaining gap is the narrow one: several test rows still are not fully schema-complete despite the design's strong-typed config claim.

## Shortest final cleanup

To fully close the last gap, Wang only needs to do one of these:

1. make the remaining test rows fully concrete by filling in every required config field and declaring intermediate values like `last_attack_target`
2. or explicitly state that some rows in `test_scenarios.md` are shorthand behavioral examples rather than literal schema-valid test vectors
