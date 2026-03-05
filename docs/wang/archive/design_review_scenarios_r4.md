# Round 4 scenario audit of `design.md`

## Verdict

**A-F now have zero hard blockers on their mainline paths.**

Round 4 closes the remaining E/F gaps in substance:

- E now has a deterministic no-repair-facility fallback: skip repair, notify player, continue
- F now has deterministic passive defense-task creation and a default attack-vs-defense resource policy

The new remaining blocker class is concentrated in **Scenario G**. I found **3 hard blockers** there.

Current status:

- **A. 生产5辆重型坦克**: zero hard blockers
- **B. 所有部队撤退回基地**: zero hard blockers
- **C. 别追太远**: zero hard blockers
- **D. 包围右边那个基地**: zero hard blockers
- **E. 修理我的坦克，然后继续进攻**: zero hard blockers
- **F. 敌人在攻击我的基地！**: zero hard blockers
- **G. 建造一个新基地在右边矿区**: 3 blockers remain

## Scenario A: “生产5辆重型坦克”

### Mainline trace

1. Kernel creates `Task(kind="background")`.
2. Task Agent wakes with context packet.
3. Task Agent optionally checks queues:
   - `query_world(query_type="production_queues", params={"unit_type":"2tnk"})`
4. Task Agent starts economy job:
   - `start_job(expert_type="EconomyExpert", config=EconomyJobConfig(unit_type="2tnk", count=5, queue_type="vehicle_factory", repeat=False))`
5. Kernel binds `ResourceNeed(kind="production_queue")`.
6. EconomyJob produces units.
7. Each completion emits:
   - `ExpertSignal(kind="progress", ...)`
8. Terminal signal:
   - `ExpertSignal(kind="task_complete", result="succeeded" | "partial" | "aborted", data=...)`
9. Task Agent finalizes:
   - `complete_task(...)`

### Blocker status

**No hard blocker found.**

## Scenario B: “所有部队撤退回基地”

### Mainline trace

1. Kernel creates retreat Task.
2. Task Agent wakes.
3. Resolve home position and affected actors:
   - `query_world(query_type="base_position", params={"owner":"self"})`
   - `query_world(query_type="actors", params={"owner":"self","category":["infantry","vehicle"]})`
4. Free conflicting combat ownership:
   - `cancel_tasks(filters={"expert_type":"CombatExpert"})`
5. Start retreat movement:
   - `start_job(expert_type="MovementExpert", config=MovementJobConfig(target_position=base_pos, actor_ids=[...], move_mode="retreat", arrival_radius=10))`
6. MovementJob returns units home.
7. Terminal signal:
   - `ExpertSignal(kind="task_complete", result="succeeded", data={"returned_actor_ids":[...]})`
8. Task Agent:
   - `complete_task(result="succeeded", summary="部队已撤回基地")`

### Blocker status

**No hard blocker found.**

## Scenario C: “别追太远”

### Mainline trace

1. Kernel creates `Task(kind="constraint")`.
2. Task Agent wakes.
3. Creates live constraint:
   - `create_constraint(kind="do_not_chase", scope="expert_type:CombatExpert", params={"max_chase_distance":20}, enforcement="clamp")`
4. CombatJobs read matching constraints each tick from WorldModel.
5. They either clamp locally or emit `decision_request` if `escalate`.
6. Task Agent may patch/remove as needed.

### Blocker status

**No hard blocker found.**

## Scenario D: “包围右边那个基地”

### Mainline trace

1. Kernel creates managed/supervised Task.
2. Task Agent wakes.
3. Resolve target:
   - `query_world(query_type="resolve_target", params={"raw_text":"右边那个基地"})`
4. If uncertain, start recon:
   - `start_job(expert_type="ReconExpert", config=ReconJobConfig(...))`
5. Recon completes with base position.
6. Task Agent wakes and starts 2-3 combat jobs in one wake:
   - `query_world(query_type="available_forces", params={...})`
   - `start_job(... CombatExpert flank 1 ...)`
   - `start_job(... CombatExpert flank 2 ...)`
   - optional flank 3
7. Later losses or opportunities wake Task Agent again for adaptation.

### Blocker status

**No hard blocker found.**

## Scenario E: “修理我的坦克，然后继续进攻”

### Mainline trace

1. Kernel creates managed Task.
2. Task Agent wakes.
3. Query damaged tanks + repair facilities:
   - `query_world(query_type="damaged_actors", params={...})`
   - `query_world(query_type="repair_facilities", params={...})`
4. If facility exists:
   - `start_job(expert_type="MovementExpert", config=MovementJobConfig(target_position=repair_facility_pos, actor_ids=[tank_id], move_mode="move", arrival_radius=6))`
5. MovementExpert reaches facility and triggers repair through GameAPI.
6. On repair completion:
   - `ExpertSignal(kind="task_complete", result="succeeded", data={"repaired_actor_ids":[tank_id]})`
7. Task Agent resumes or recreates attack job.

### No-repair-facility branch

- `query_world(...)` finds no facility
- deterministic fallback now exists:
  - skip repair
  - continue subsequent attack action
  - notify player

### Blocker status

**No hard blocker found.**

Residual note:

- “notify player” is described behaviorally, not with a dedicated runtime primitive, but that is not a blocker for the task flow itself.

## Scenario F: “敌人在攻击我的基地！”

### Mainline trace

1. WorldModel emits:
   - `Event(type="BASE_UNDER_ATTACK", ...)`
2. Kernel applies pre-registered deterministic event rule:
   - auto-create `Task(kind="managed", raw_text="defend_base", priority=80)`
3. Defense Task Agent wakes with context packet.
4. Defense Task Agent queries threats and defenders:
   - `query_world(query_type="threats", params={"scope":"base"})`
   - `query_world(query_type="available_forces", params={"scope":"home"})`
5. Defense Task Agent starts defense jobs:
   - `start_job(expert_type="CombatExpert", config=CombatJobConfig(target_position=base_pos, engagement_mode="hold", ...))`
6. Kernel priority policy applies:
   - defense priority 80 > attack priority 50
   - defense gets first claim on contested resources
   - attack tasks degrade rather than being force-cancelled
7. Defense completes and Task Agent finalizes via `complete_task(...)`.

### Blocker status

**No hard blocker found.**

## Scenario G: “建造一个新基地在右边矿区”

### Intended trace

1. Kernel creates managed/supervised Task.
2. Task Agent wakes.
3. Resolve target expansion site:
   - `query_world(query_type="resolve_target", params={"raw_text":"右边矿区"})`
4. If location is unknown or insufficiently known:
   - `start_job(expert_type="ReconExpert", config=ReconJobConfig(search_region="northeast", target_type="expansion", ...))`
5. Query whether we have an MCV / deployable base unit:
   - `query_world(query_type="actors", params={"owner":"self","category":"mcv"})`
6. Move MCV to target area:
   - `start_job(expert_type="MovementExpert", config=MovementJobConfig(target_position=ore_pos, actor_ids=[mcv_id], move_mode="move", arrival_radius=8))`
7. Deploy / build the new base.
8. If contested, adapt by escorting / delaying / choosing a safer expansion.

### Remaining blockers

#### G1. `DeployExpert` exists only in the mapping table, but has no schema or runtime contract

Relevant lines:
- mapping row: `docs/wang/design.md:431`
- expert config schemas section has Recon / Combat / Movement / Economy only: `docs/wang/design.md:78-105`

Problem:

- The spec references `DeployExpert`, but there is no `DeployJobConfig` or any runtime description of how deployment works.
- Missing:
  - required parameters
  - whether it consumes an `mcv` actor resource
  - placement validation inputs
  - what terminal signals it emits on success/failure

Impact:

- Scenario G cannot complete the actual “build/deploy new base” step from spec alone.

#### G2. No prerequisite policy for “no MCV available”

Problem:

- The scenario explicitly depends on a physical prerequisite: an MCV or equivalent deployable base unit.
- The spec does not define the standard behavior if no MCV is currently available:
  - produce one via EconomyExpert?
  - fail immediately?
  - wait?
  - downgrade to “expand later”?

Impact:

- Scenario G is blocked on the branch where the obvious prerequisite is missing.

#### G3. Contested-location branch is not specified

Problem:

- The scenario asks what happens if the right-side ore field is contested.
- The spec has enough generic machinery to imagine several valid responses:
  - escort MCV with CombatJobs
  - clear area first
  - choose a different expansion
  - abort the expansion
- But it does not define the standard branching policy or the signals that should mark placement as unsafe / contested.

Impact:

- Scenario G remains underspecified in exactly the branch the prompt asked us to test.

## Bottom line

Round 4 fully closes the previous E/F gaps in substance:

- **A-F now have zero hard blockers on their mainline paths**

The new unresolved area is expansion/base-building:

1. define `DeployJobConfig` / `DeployExpert` contract
2. define what to do if no MCV is available
3. define default contested-expansion policy (clear / escort / reroute / abort)
