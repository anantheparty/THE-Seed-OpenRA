# Audit of `design.md` test checklist T1-T11

## Findings

### 1. T10's proactive-notification triggers are not backed by the declared `Event` model

`design.md` now says proactive notifications come from `WorldModel 事件触发 → Kernel 预注册规则检查 → 推送通知到看板` and gives examples like “发现敌人在扩张” / “我方前线空虚” / “经济充裕，可以考虑进攻” (`design.md` lines 41-45).

But the formal `Event` model only declares:

- `UNIT_DIED`
- `UNIT_DAMAGED`
- `ENEMY_DISCOVERED`
- `BASE_UNDER_ATTACK`
- `PRODUCTION_COMPLETE`

(`design.md` lines 163-170)

Then T10 requires:

- `敌人扩张到新矿区`
- `我方前线空虚`

as concrete detection inputs (`design.md` lines 564-569).

That means the checklist currently depends on event/rule inputs that are not defined anywhere in the typed runtime model.

Why this matters:

- the checklist is no longer fully traceable to the formal data model
- implementers cannot tell whether these are:
  - new `Event.type` values
  - derived heuristics computed inside `WorldModel`
  - or higher-level notification rules over existing raw events

### 2. T10 and T11 require a player-facing output channel that the dashboard I/O spec does not define

The checklist expects the system to:

- push proactive notifications to the player in T10 (`design.md` lines 567-570)
- push a direct LLM answer to the player in T11 (`design.md` lines 573-579)

But the dashboard/WebSocket section only defines outbound messages:

- `world_snapshot`
- `task_update`
- `task_list`
- `log_entry`

(`design.md` lines 296-297)

No outbound message type is defined for:

- proactive player notifications
- direct query responses
- assistant recommendations

This is an actual spec gap for the checklist, because T10/T11 both depend on a delivery path that does not exist in the formal interface section.

### 3. `CommandProcessor` is used in T1 and T11, but no such component or contract is defined in the architecture

T1 begins with:

- `玩家输入 → CommandProcessor 识别为执行指令`

and T11 begins with:

- `玩家输入 → CommandProcessor 识别为查询，非执行`

(`design.md` lines 472 and 574)

But nowhere in the architecture, runtime, or component-responsibility sections is `CommandProcessor` defined as a component, boundary, or contract.

The design does describe three input paths abstractly (`design.md` lines 25-45), but it never says:

- what component performs execute/query classification
- whether that logic lives in the dashboard backend, Kernel ingress, or an LLM router
- what the classification API or fallback behavior is

So the checklist is currently referencing an implementation surface that the rest of the document does not define.

### 4. Several checklist steps are not executable against the document's own “strongly typed config” claim

The design explicitly says Job config is strongly typed and each Expert has its own schema (`design.md` lines 86-114, decision 8 at lines 310-311).

But multiple checklist steps use partial configs or `...` placeholders instead of values that satisfy the declared schema:

- T3 `MovementJobConfig(actor_ids=[...], target_position=base_pos, move_mode="retreat")` omits `arrival_radius` (`design.md` line 501 vs schema lines 105-109)
- T5 `start_job(ReconExpert, ...)` and `CombatJobConfig(..., ...)` are placeholders rather than concrete config values (`design.md` lines 520-523)
- T6 `MovementJobConfig(target_position=repair_pos, move_mode="move")` omits `arrival_radius` (`design.md` line 533)
- T6 `CombatJobConfig(...)` is unresolved (`design.md` line 535)
- T7 `CombatJobConfig(engagement_mode="hold", target_position=base_pos)` omits `max_chase_distance` and `retreat_threshold` (`design.md` line 542 vs schema lines 99-103)
- T8 `start_job(MovementExpert, ..., target=矿区位置)` and `EconomyJobConfig(unit_type="mcv", count=1, ...)` are also shorthand rather than schema-complete calls (`design.md` lines 550-551)

This does not necessarily break the architecture, but it does mean the new “step-by-step expected behavior” checklist is not actually executable verbatim.

### 5. `query_world` is used as if it had a concrete query vocabulary, but that vocabulary is not specified anywhere

The tool contract only says:

- `query_world(query_type, params) -> data`

with a broad summary of `actors / map / economy / threats` (`design.md` line 224).

But the checklist depends on concrete query types such as:

- `my_combat_actors` (`design.md` line 498)
- `enemy_bases` (`design.md` line 519)
- `my_damaged_tanks` (`design.md` line 532)
- `repair_facilities` (`design.md` line 532)

Those names are reasonable, but they are not declared anywhere in the spec.

So the checklist currently assumes a query API surface that the rest of the document does not enumerate or normalize.

### 6. There are still minor naming inconsistencies inside the checklist

These are lower severity than the items above, but they are real mismatches:

- The `Task` data model uses `autonomy_mode` (`design.md` line 77), while T1/T2/T5 use `autonomy=` (`design.md` lines 473, 486, 518)
- The main recon walkthrough earlier uses `world_delta={enemy_base_pos: ...}` (`design.md` line 384), while T1 uses `data={base_pos:(x,y)}` (`design.md` line 479)

These look editorial rather than architectural, but they reduce checklist precision.

## Overall assessment

I do **not** see a return of the earlier architecture blockers from the scenario rounds. The core structure still looks converged.

What this audit does show is that the new test checklist is ahead of the formal interface layer in a few places:

1. notification-trigger inputs are not fully modeled
2. player-facing notification/query response channels are not defined in the dashboard I/O spec
3. command classification is referenced but not owned by any defined component
4. the checklist uses shorthand configs and query names that are not yet normalized into formal contracts

## Shortest path to make the checklist fully self-consistent

1. Define whether proactive-notification rules consume:
   - new typed `Event` values
   - derived `WorldModel` conditions
   - or a separate `NotificationRule` layer over existing data
2. Add explicit outbound interface(s) for:
   - `player_notification`
   - `assistant_reply` or equivalent query-response payload
3. Add one short section naming the ingress classifier component:
   - `CommandProcessor` or rename it away
   - and define execute/query classification ownership
4. Either:
   - make the checklist configs fully concrete
   - or explicitly mark `...` steps as shorthand examples rather than executable test vectors
5. Enumerate or at least categorize the supported `query_world.query_type` vocabulary used by the checklist
