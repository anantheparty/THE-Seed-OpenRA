# Round 5 scenario audit of `design.md`

## Verdict

**A-G now have zero hard blockers on their mainline paths.**

Round 5 closes G in substance:

- `DeployJobConfig` now exists
- expansion flow now covers have-MCV vs produce-MCV-first
- contested expansion is now handled through the general “plan-breaking conditions” policy

The new remaining blocker class is concentrated in **Scenario H**. I found **1 hard blocker** there.

Current status:

- **A. 生产5辆重型坦克**: zero hard blockers
- **B. 所有部队撤退回基地**: zero hard blockers
- **C. 别追太远**: zero hard blockers
- **D. 包围右边那个基地**: zero hard blockers
- **E. 修理我的坦克，然后继续进攻**: zero hard blockers
- **F. 敌人在攻击我的基地！**: zero hard blockers
- **G. 建造一个新基地在右边矿区**: zero hard blockers
- **H. 连续快速下达 3 条命令**: 1 blocker remains

## Scenario A: “生产5辆重型坦克”

### Mainline trace

1. Kernel receives player command and creates `Task(kind="background")`.
2. Kernel spawns Task Agent A.
3. Task Agent A may query queue availability:
   - `query_world(query_type="production_queues", params={"unit_type":"2tnk"})`
4. Task Agent A starts EconomyJob:
   - `start_job(expert_type="EconomyExpert", config=EconomyJobConfig(...))`
5. EconomyJob runs, emits `progress` per unit, and terminal `task_complete`.
6. Task Agent A finalizes with `complete_task(...)`.

### Blocker status

**No hard blocker found.**

## Scenario B: “所有部队撤退回基地”

### Mainline trace

1. Kernel creates retreat Task.
2. Kernel spawns Task Agent B.
3. Task Agent B queries home position / controlled actors.
4. Task Agent B cancels conflicting combat tasks:
   - `cancel_tasks(filters={"expert_type":"CombatExpert"})`
5. Task Agent B starts MovementJob in `retreat` mode.
6. MovementJob returns units to base.
7. Task Agent B completes the task.

### Blocker status

**No hard blocker found.**

## Scenario C: “别追太远”

### Mainline trace

1. Kernel creates `Task(kind="constraint")`.
2. Kernel spawns Task Agent C.
3. Task Agent C creates the live chase constraint:
   - `create_constraint(kind="do_not_chase", scope="expert_type:CombatExpert", params={"max_chase_distance":20}, enforcement="clamp")`
4. Existing CombatJobs read it from WorldModel each tick.
5. Constraint immediately affects future chase behavior.

### Blocker status

**No hard blocker found.**

## Scenario D: “包围右边那个基地”

### Mainline trace

1. Kernel creates managed/supervised Task.
2. Task Agent resolves target via `query_world`.
3. If necessary, starts recon.
4. On recon completion, starts 2-3 combat jobs in one wake.
5. Later adapts on flank-loss signals.

### Blocker status

**No hard blocker found.**

## Scenario E: “修理我的坦克，然后继续进攻”

### Mainline trace

1. Kernel creates managed Task.
2. Task Agent queries damaged tanks + repair facilities.
3. If facility exists, starts MovementJob to repair site.
4. On repair completion, resumes or recreates attack job.
5. If no facility exists, deterministic fallback now applies:
   - skip repair
   - notify player
   - continue the later attack action

### Blocker status

**No hard blocker found.**

## Scenario F: “敌人在攻击我的基地！”

### Mainline trace

1. WorldModel emits `BASE_UNDER_ATTACK`.
2. Kernel applies pre-registered rule and auto-creates defense Task with priority 80.
3. Defense Task Agent queries threats / available defenders.
4. Defense Task Agent starts defense CombatJobs.
5. Kernel gives defense first claim on resources; attack tasks degrade rather than being force-cancelled.
6. Defense Task Agent completes on stabilization.

### Blocker status

**No hard blocker found.**

## Scenario G: “建造一个新基地在右边矿区”

### Mainline trace

1. Kernel creates managed/supervised expansion Task.
2. Task Agent queries expansion site:
   - `query_world(query_type="resolve_target", params={"raw_text":"右边矿区"})`
3. If site is unknown, starts recon for `target_type="expansion"`.
4. Task Agent queries MCV availability:
   - `query_world(query_type="actors", params={"owner":"self","category":"mcv"})`
5. If MCV exists:
   - start `MovementExpert` to move MCV to expansion site
   - then start `DeployExpert` with `DeployJobConfig(actor_id=mcv_id, target_position=..., building_type="ConstructionYard")`
6. If no MCV exists:
   - Task Agent uses the generic prerequisite policy and starts EconomyJob to produce one
   - then moves/deploys it
7. If the location is contested:
   - Task Agent uses generic prerequisite-breaking policy to either clear first with CombatJobs or reroute

### Blocker status

**No hard blocker found.**

Residual note:

- the “produce / wait / skip / clear” policy is intentionally delegated to the LLM rather than fully rule-specified
- given the architecture, that is acceptable for this class of planning decision

## Scenario H: rapid sequence of 3 player commands

Player inputs in quick succession:

1. “生产坦克”
2. “探索地图”
3. “别追太远”

### Intended trace

1. Kernel receives command 1.
2. Kernel creates Task H1 (`background`) and spawns Task Agent H1.
3. Task Agent H1 starts EconomyJob.

4. Kernel receives command 2 shortly after.
5. Kernel creates Task H2 (`managed`) and spawns Task Agent H2.
6. Task Agent H2 starts ReconJob.

7. Kernel receives command 3 shortly after.
8. Kernel creates Task H3 (`constraint`) and spawns Task Agent H3.
9. Task Agent H3 creates `do_not_chase` constraint.

10. Kernel now manages three concurrent Tasks and their Task Agents.
11. Resource contention is handled by existing priority/resource-allocation rules:
   - EconomyJob holds production queue resources
   - ReconJob requests a fast actor
   - Constraint task adds no actor ownership itself

### What is clear from spec

- Yes, **3 separate Tasks** are created.
- Yes, **3 Task Agents** are spawned.
- Kernel is explicitly responsible for concurrent task creation and resource arbitration.
- The constraint task does not need to wait for other tasks to finish; it creates a live constraint that existing jobs read from WorldModel.

### Remaining blocker

**1 hard blocker remains:** the default scope/targeting rule for a bare constraint command in a multi-task environment is still not specified.

Problem:

- The mapping table shows:
  - `别追太远 | constraint | create_constraint(do_not_chase, global, {max_distance:20}, clamp)`
- But the spec does not state whether that `global` scope is:
  - a rule specific to this exact phrase
  - the general default for all bare constraint commands
  - or just one example

Why this matters in H:

- H specifically tests how the third command interacts with the earlier two already-running tasks.
- If the constraint is truly `global`, then:
  - it should affect any current/future matching CombatJobs system-wide
  - it should not affect EconomyJob
  - it likely will not affect the current ReconJob either, unless recon later spawns combat follow-ups
- If the intended scope is narrower, the interaction changes materially.

So while the runtime machinery is present, the **default scoping rule for naked constraint commands in concurrent multi-task situations is still implicit rather than explicit**.

## Bottom line

Round 5 closes the previous G expansion gaps in substance:

- **A-G now have zero hard blockers on their mainline paths**

The new unresolved area is concurrent command interpretation:

1. declare the default scope rule for bare constraint commands like “别追太远” when multiple tasks already exist

If Wang wants the shortest actionable summary:

- keep the concurrency/runtime model as-is
- add one explicit sentence specifying the default scope resolution policy for natural-language constraint commands in multi-task contexts
