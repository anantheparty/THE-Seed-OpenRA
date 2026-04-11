# Live Test Round 7

Date: 2026-04-01

## Scope

Round 7 was run as live/manual validation against the real backend and real game state, not mock e2e.

Primary command chain:

1. `建造兵营`
2. `生产3个步兵`
3. `探索地图`
4. `战况如何`

## Preceding Fixes Included In This Round

1. `3c9787d` `fix: clear stale runtime on game reset`
   - Fixed the stale-runtime bug after external game restart.
   - Before this fix, backend state from the previous match could leak into the new live session.

2. `4e94f81` `fix: short-circuit deploy commands without mcv`
   - Fixed `部署基地车` under non-pristine live states.
   - No more false task creation when there is no MCV, or when a yard already exists.

3. `5455fc8` `fix: keep simple rule-routed tasks monitor-only`
   - Fixed drift where simple rule-routed tasks could still re-enter unnecessary planning behavior.
   - Simple commands now stay attached to the already-started rule job and only monitor/close.

## Round 7 Results

### 1. 建造兵营

- Adjutant reply: `收到指令，已直接执行并创建任务 t_c604d34f`
- Final task status: `succeeded`
- GameAPI verification:
  - Barracks count changed from `1 -> 2`
  - New actor observed: `203 兵营`

Conclusion: passed.

### 2. 生产3个步兵

- Adjutant reply: `收到指令，已直接执行并创建任务 t_25e17cb2`
- Final task status: `succeeded`
- GameAPI verification:
  - Infantry count changed from `0 -> 3`
  - New actors observed: `204/205/206 步兵`

Conclusion: passed.

### 3. 探索地图

- Adjutant reply: `收到指令，已直接执行并创建任务 t_7b5a4a93`
- Task status within the 40s observation window: still `running`
- GameAPI verification:
  - Recon execution did start
  - Scout actor `204 步兵` moved from `(110,20) -> (99,40)`

Conclusion: start path works, but closure condition is not robust enough. This is the only Round 7 command that did not cleanly close during the observation window.

### 4. 战况如何

- Adjutant reply: returned a full battlefield summary
- Task status: `no_task` (pure query path)
- GameAPI verification:
  - Self actor count and the reported early-exploration state were broadly consistent with live world state

Conclusion: passed.

## Current System Assessment

The core chain is now meaningfully healthier than before Round 6:

- Simple build and production commands are no longer the main problem.
- Query response routing is working.
- External game reset no longer poisons the next session with stale runtime state.

The main remaining workflow weakness exposed by Round 7 is no longer “simple commands fail”, but:

- long-running recon tasks can start correctly and visibly move units
- but they may not converge to a terminal outcome in a reasonable live window

So the system has moved from “baseline chain broken” to “persistent-task closure policy needs tightening”.

## Git State

This report was added after the Round 7 live/manual run and is intended to be committed separately from the follow-up recon closure fix.
