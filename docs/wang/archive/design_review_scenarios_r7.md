# Round 7 scenario audit of `design.md`

## Verdict

**A-I now have zero hard blockers on their mainline paths.**

Round 7 closes the previous Scenario I blocker in substance by making the no-command branch explicit:

- player inputs are now split into `execute / query / proactive notification`
- query requests bypass Kernel and return directly from `LLM + WorldModel`
- the system explicitly does **not** autonomously execute strategic actions
- the only stated exception remains emergency defense (`BASE_UNDER_ATTACK`)

That is enough to remove the architectural ambiguity from I.

Current status:

- **A. 生产5辆重型坦克**: zero hard blockers
- **B. 所有部队撤退回基地**: zero hard blockers
- **C. 别追太远**: zero hard blockers
- **D. 包围右边那个基地**: zero hard blockers
- **E. 修理我的坦克，然后继续进攻**: zero hard blockers
- **F. 敌人在攻击我的基地！**: zero hard blockers
- **G. 建造一个新基地在右边矿区**: zero hard blockers
- **H. 连续快速下达 3 条命令**: zero hard blockers
- **I. 玩家长时间不下命令，但局势出现战略机会**: zero hard blockers

## Scenario A: “生产5辆重型坦克”

### Mainline trace

1. Kernel receives player command and creates `Task(kind="background")`.
2. Task Agent starts `EconomyJob`.
3. EconomyJob owns queue-oriented execution, emits `progress`, and finishes with `task_complete`.
4. Task Agent finalizes with `complete_task(...)`.

### Blocker status

**No hard blocker found.**

## Scenario B: “所有部队撤退回基地”

### Mainline trace

1. Kernel creates retreat Task.
2. Task Agent cancels conflicting combat tasks via `cancel_tasks(...)`.
3. Task Agent starts `MovementJob(move_mode="retreat")`.
4. Kernel reallocates resources as needed.
5. Task completes on successful regroup.

### Blocker status

**No hard blocker found.**

## Scenario C: “别追太远”

### Mainline trace

1. Kernel creates `Task(kind="constraint")`.
2. Task Agent creates `do_not_chase` constraint.
3. Because the spec now explicitly defines the default rule for bare natural-language constraint commands, scope resolution is deterministic.
4. Existing and future matching CombatJobs read the live constraint from WorldModel and clamp chase behavior.

### Blocker status

**No hard blocker found.**

## Scenario D: “包围右边那个基地”

### Mainline trace

1. Kernel creates a managed/supervised Task.
2. Task Agent resolves the target via `query_world`.
3. If needed, it starts recon first.
4. On recon completion, it starts 2-3 CombatJobs in one wake.
5. Later signals drive adaptive coordination.

### Blocker status

**No hard blocker found.**

## Scenario E: “修理我的坦克，然后继续进攻”

### Mainline trace

1. Task Agent queries damaged tanks and repair facilities.
2. If a facility exists, it starts MovementJob to the repair site.
3. On repair completion, it resumes or recreates attack execution.
4. If no facility exists, the explicit prerequisite-missing policy allows skip-and-continue with player notification.

### Blocker status

**No hard blocker found.**

## Scenario F: “敌人在攻击我的基地！”

### Mainline trace

1. WorldModel emits `BASE_UNDER_ATTACK`.
2. Kernel's pre-registered emergency rule auto-creates the defense Task.
3. Defense Task gets higher priority and first claim on resources.
4. Existing attack tasks degrade rather than being force-cancelled wholesale.
5. Defense Task Agent decides the actual defender mix and completes on stabilization.

### Blocker status

**No hard blocker found.**

## Scenario G: “建造一个新基地在右边矿区”

### Mainline trace

1. Task Agent resolves the target site.
2. It checks whether an MCV exists.
3. If yes, it moves and deploys it with `DeployExpert`.
4. If no, it uses the explicit prerequisite policy to produce one first.
5. If the site is contested, it clears first or reroutes under the same policy.

### Blocker status

**No hard blocker found.**

## Scenario H: rapid sequence of 3 player commands

Commands:

1. “生产坦克”
2. “探索地图”
3. “别追太远”

### Mainline trace

1. Kernel creates three separate Tasks.
2. Three Task Agents are spawned independently.
3. Economy and recon proceed concurrently.
4. The chase constraint is created with deterministic default `scope=global`.
5. Kernel handles concurrency and resource arbitration without needing extra ambiguity resolution.

### Blocker status

**No hard blocker found.**

## Scenario I: no new player commands, but the game presents a strategic opportunity

Scenario:

- the player says nothing for a long time
- economy is growing
- scouting discovers the enemy is expanding
- our frontline is empty

### Mainline trace

1. WorldModel state changes trigger Kernel's pre-registered notification rules.
2. The system pushes proactive notifications to the dashboard, for example:
   - “发现敌人在扩张”
   - “我方前线空虚”
   - “经济充裕，可以考虑进攻”
3. The system does **not** auto-create a strategic execution Task from those signals.
4. The player can then choose one of two explicit follow-up paths:
   - issue an execute command, which goes through Kernel → Task Agent → Job
   - issue a query command like “现在该做什么？”, which goes through direct `LLM + WorldModel` analysis without entering Kernel

### Why the previous blocker is now closed

Round 6's blocker was not that the design lacked autonomous strategy execution. The blocker was that the design had not chosen a model at all.

Round 7 now makes that choice explicit:

- no-command strategic opportunities produce **notifications**, not autonomous action
- recommendation-style questions use the separate query path
- only emergency defense stays in the Kernel auto-action bucket

That is a complete enough authority model to implement.

### Blocker status

**No hard blocker found.**

## Residual note

I still see one editorial inconsistency in the old recon walkthrough example:

- the walkthrough text says `cancel_task(task_id="t1")`
- the actual tool table defines `cancel_tasks(filters)`

I do **not** treat this as a hard blocker for the 9-scenario audit. The architecture-level behavior is now converged.

## Bottom line

Round 7 reaches convergence:

- **all 9 scenarios A-I now have zero hard blockers on their mainline paths**

The key closing move was not adding more autonomy. It was explicitly separating:

1. execute commands
2. query-only advice
3. proactive notifications without autonomous strategic action
