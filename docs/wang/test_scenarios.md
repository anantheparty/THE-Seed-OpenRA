# 测试场景清单

每个场景逐步描述系统预期行为，包括各组件状态变化。实现后逐条验证。

---

## T1. "探索地图，找到敌人基地"

**输入：** 玩家文本 "探索地图，找到敌人基地"
**预期 Task 类型：** managed, fire_and_forget

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | 玩家输入 | CommandProcessor | 分类为执行指令 | — |
| 2 | | Kernel | 创建 Task | `Task(id=t1, kind=managed, priority=50, status=pending, autonomy_mode=fire_and_forget)` |
| 3 | | Kernel | spawn Task Agent | Task Agent t1 启动，注入 context packet：`{task:{id:t1, raw_text:"探索地图..."}, world_summary:{explored_pct:0.1, economy:{cash:2000}, known_enemy:{base_known:false}}}` |
| 4 | context packet | Task Agent (LLM) | 第 1 次 LLM 调用。理解意图 → tool_use | `start_job(expert_type="ReconExpert", config=ReconJobConfig(search_region="enemy_half", target_type="base", target_owner="enemy", retreat_hp_pct=0.3, avoid_combat=true))` |
| 5 | start_job 返回 job_id | Kernel | 创建 Job + 分配资源 | `Job(id=j1, task_id=t1, expert_type=ReconExpert, status=running, resources=["actor:57"])` WorldModel: `resource_bindings["actor:57"] = "j1"` |
| 6 | | Task Agent | LLM 结束本轮，进入 sleep | Task Agent 状态: sleeping, 等待 Signal |
| 7 | GameLoop tick (1s 间隔) | ReconJob | tick: `query_world(unexplored_regions)` → 对角方向评分最高 → `GameAPI.move_actors([57], (1600,200))` | actor:57 开始移动 |
| 8 | t=15s, WorldModel 快照 diff | WorldModel | detect_events: 新 enemy actor:201 出现 | `Event(type=ENEMY_DISCOVERED, actor_id=201)` |
| 9 | Event 路由 | Kernel | 路由给 ReconJob (资源 actor:57 相关) | — |
| 10 | | ReconJob | 矿车在 (1700,350)，调整方向 → `GameAPI.move_actors([57], (1800,420))` | 发 Signal: `ExpertSignal(kind=progress, summary="发现敌方矿车，调整方向", expert_state={phase:"tracking", progress_pct:40})` |
| 11 | Signal kind=progress | Kernel | 路由给 Task Agent | fire_and_forget 模式, progress 不唤醒 → Task Agent 保持 sleep |
| 12 | t=30s | WorldModel | detect_events: actor:57 HP 100→85 | `Event(type=UNIT_DAMAGED, actor_id=57, data={old_hp:100, new_hp:85})` |
| 13 | | ReconJob | HP 85% > retreat_hp_pct 30% → 继续 → `GameAPI.move_actors([57], (1820,430), attack_move=true)` | — |
| 14 | t=42s | WorldModel | 3 个敌方建筑出现在 (1820,430) | `Event(type=ENEMY_DISCOVERED, ...)` × 3 |
| 15 | | ReconJob | 目标达成 | 发 Signal: `ExpertSignal(kind=task_complete, result=succeeded, summary="找到敌人基地(1820,430)", data={base_pos:(1820,430), structures:3})` |
| 16 | Signal kind=task_complete | Kernel | 路由给 Task Agent → **唤醒** | Task Agent 状态: awake |
| 17 | Signal 注入 | Task Agent (LLM) | 第 2 次 LLM 调用。收到 task_complete → tool_use | `complete_task(result="succeeded", summary="找到敌人基地(1820,430)")` |
| 18 | complete_task | Kernel | 终止 Job j1, status→succeeded。释放 actor:57。Task t1 status→succeeded | WorldModel: `resource_bindings` 删除 "actor:57"。推送看板: `task_update`, `player_notification("找到敌人基地")` |

**预期 LLM 调用次数：2**
**预期 GameAPI 调用：** 3 次 move（初始 + 调整 + attack_move）

### T1 边缘：侦察兵 t=20s 死亡

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| E1 | WorldModel diff | WorldModel | actor:57 消失 | `Event(type=UNIT_DIED, actor_id=57)` |
| E2 | Event | Kernel | Job j1 资源丢失。按 ResourceNeed 查空闲快速单位 | `Job.resources` 移除 "actor:57" |
| E3a | 有空闲 actor:83 | Kernel | 自动分配 | `Job.resources` 加 "actor:83"。ReconJob.on_resource_granted([83])。Job 继续 running |
| E3b | 无空闲 fast 单位 | Kernel | Job status→waiting | ReconJob 发 Signal: `ExpertSignal(kind=decision_request, decision={options:["wait","use_infantry","abort"], default_if_timeout:"wait", deadline_s:3.0})` |
| E4 | Signal decision_request | Task Agent (LLM) | 第 3 次 LLM 调用。选择选项 | 如 `patch_job(j1, {avoid_combat:false})` + 等待分配。或 `abort_job(j1)` + `complete_task(result="failed")` |
| E5 | 3s 超时无回复 | ReconJob | 执行 default: wait | Job 保持 waiting，等新单位造出来 |

---

## T2. "生产5辆重型坦克"

**输入：** "生产5辆重型坦克"
**预期 Task 类型：** background, fire_and_forget

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | 玩家输入 | CommandProcessor | 分类为执行指令 | — |
| 2 | | Kernel | 创建 Task | `Task(id=t2, kind=background, priority=40, status=pending, autonomy_mode=fire_and_forget)` |
| 3 | | Kernel | spawn Task Agent | 注入 context: `{economy:{cash:5000, power:normal}, production_queues:{Vehicle:{building:"war_factory", busy:false}}}` |
| 4 | context | Task Agent (LLM) | 第 1 次 LLM → tool_use | `start_job(expert_type="EconomyExpert", config=EconomyJobConfig(unit_type="2tnk", count=5, queue_type="Vehicle", repeat=false))` |
| 5 | start_job | Kernel | 创建 Job + 分配资源 | `Job(id=j2, status=running, resources=["queue:Vehicle"])` |
| 6 | | Task Agent | sleep | — |
| 7 | tick (5s 间隔) | EconomyJob | `GameAPI.produce("Vehicle", "2tnk")` | 生产排队 |
| 8 | WorldModel Event | WorldModel | PRODUCTION_COMPLETE | `Event(type=PRODUCTION_COMPLETE, data={unit_type:"2tnk", queue:"Vehicle"})` |
| 9 | | EconomyJob | completed=1, remaining=4 | Signal: `ExpertSignal(kind=progress, expert_state={completed:1, remaining:4})` |
| 10 | 重复 7-9 | EconomyJob | 每完成一个发 progress Signal | fire_and_forget: Task Agent 不唤醒 |
| 11 | 中途 cash=0 | EconomyJob | 无法 produce → Job status→waiting | 不 fail，等钱恢复后自动继续 |
| 12 | cash 恢复 | EconomyJob | 继续 produce → status→running | — |
| 13 | 5/5 完成 | EconomyJob | Signal: `ExpertSignal(kind=task_complete, result=succeeded, data={produced:5})` | — |
| 14 | task_complete | Task Agent (LLM) | 第 2 次 LLM → tool_use | `complete_task(result="succeeded", summary="已生产5辆重型坦克")` |
| 15 | | Kernel | 释放 queue:Vehicle，Task t2 → succeeded | 推送看板 |

**预期 LLM 调用次数：2**

### T2 边缘：只造了 3 辆，工厂被摧毁

| 步骤 | 触发 | 组件 | 行为 |
|---|---|---|---|
| E1 | STRUCTURE_LOST | Kernel | 工厂没了 → EconomyJob 资源 "queue:Vehicle" 丢失 |
| E2 | | EconomyJob | Signal: `ExpertSignal(kind=task_complete, result=failed, data={produced:3, remaining:2, reason:"factory_destroyed"})` |
| E3 | | Task Agent | LLM 判断：`complete_task(result="partial", summary="生产了3/5辆，工厂被摧毁")` |

---

## T3. "所有部队撤退回基地"

**输入：** "所有部队撤退回基地"
**预期 Task 类型：** managed

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | 玩家输入 | CommandProcessor | 分类为执行指令 | — |
| 2 | | Kernel | 创建 Task | `Task(id=t3, kind=managed, priority=70)` |
| 3 | context | Task Agent (LLM) | 第 1 次 LLM → 多个 tool_use | `query_world("my_combat_actors")` → 返回 [actor:57, actor:58, actor:59, ...] |
| 4 | | Task Agent | 继续 tool_use | `cancel_tasks(filters={expert_type:"CombatExpert"})` → Kernel abort 所有 CombatJob |
| 5 | cancel_tasks | Kernel | 遍历匹配的 Task → abort 所有 CombatJob → 释放资源 | 被取消的 Task status→aborted |
| 6 | | Task Agent | 继续 tool_use | `query_world("map_info", {query:"base_position"})` → (200, 600) |
| 7 | | Task Agent | 继续 tool_use | `start_job(expert_type="MovementExpert", config=MovementJobConfig(actor_ids=[57,58,59,...], target_position=(200,600), move_mode="retreat", arrival_radius=10))` |
| 8 | | Kernel | 创建 Job + 分配资源 | `Job(id=j3, resources=["actor:57","actor:58","actor:59",...])` |
| 9 | | Task Agent | sleep | — |
| 10 | tick (1s) | MovementJob | 为每个 actor 调 `GameAPI.move_actors` → 向基地移动 | — |
| 11 | 全部 actor 到达 base 附近 | MovementJob | Signal: `ExpertSignal(kind=task_complete, result=succeeded)` | — |
| 12 | | Task Agent (LLM) | 第 2 次 LLM | `complete_task(result="succeeded", summary="所有部队已撤回基地")` |

**预期 LLM 调用次数：2**（第 1 次包含 4 个 tool_use）

---

## T4. "别追太远"

**输入：** "别追太远"
**预期 Task 类型：** constraint

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | 玩家输入 | CommandProcessor | 分类为执行（constraint） | — |
| 2 | | Kernel | 创建 Task | `Task(id=t4, kind=constraint, priority=50)` |
| 3 | context | Task Agent (LLM) | 第 1 次 LLM → tool_use | `create_constraint(kind="do_not_chase", scope="global", params={max_distance:20}, enforcement="clamp")` |
| 4 | create_constraint | Kernel | 创建 Constraint 存入 WorldModel | `Constraint(id=c1, kind=do_not_chase, scope=global, params={max_distance:20}, enforcement=clamp, active=true)` |
| 5 | | Task Agent | 继续 tool_use | `complete_task(result="succeeded", summary="已设置：不追击超过20格")` |
| 6 | | Kernel | Task t4 → succeeded | 推送看板 + 通知玩家 |
| 7 | 下一次 CombatJob tick | CombatJob | `WorldModel.get_active_constraints()` → 发现 c1 → clamp 追击距离 ≤ 20 | CombatJob 内部参数调整 |

**预期 LLM 调用次数：1**

---

## T5. "包围右边那个基地"

**输入：** "包围右边那个基地"
**预期 Task 类型：** managed, supervised

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | 玩家输入 | CommandProcessor | 分类为执行指令 | — |
| 2 | | Kernel | 创建 Task | `Task(id=t5, kind=managed, priority=60, autonomy_mode=supervised)` |
| 3 | context | Task Agent (LLM) | 第 1 次 LLM → tool_use | `query_world("enemy_bases")` → 返回 [{pos:(1820,430), structures:3}, {pos:(500,800), structures:2}] |
| 4 | | Task Agent | LLM 判断"右边"= (1820,430) → tool_use | `start_job("CombatExpert", CombatJobConfig(target_position=(1820,430), engagement_mode="surround", max_chase_distance=15, retreat_threshold=0.4))` |
| 5 | | Task Agent | 同一 wake 继续 tool_use | `start_job("CombatExpert", CombatJobConfig(target_position=(1820,430), engagement_mode="surround", max_chase_distance=15, retreat_threshold=0.4))` ← 第 2 路 |
| 6 | | Kernel | 创建 Job j5a + j5b，分配资源 | j5a: resources=[actor:58,59,60] j5b: resources=[actor:61,62,63] |
| 7 | | Task Agent | sleep | — |
| 8 | tick | CombatJob j5a, j5b | 各自自主执行：集结 → 接近 → 攻击 | GameAPI 调用 |
| 9 | j5a 被打退 | CombatJob j5a | Signal: `ExpertSignal(kind=resource_lost, summary="侧翼A损失过半")` | supervised 模式 → 唤醒 Task Agent |
| 10 | Signal | Task Agent (LLM) | 第 2 次 LLM → 决策 | 选择：`patch_job(j5b, {engagement_mode:"assault"})` 改为正面强攻。或 `abort_job(j5a)` + `start_job(...)` 重组 |
| 11 | 包围成功 | CombatJob | Signal: task_complete | — |
| 12 | | Task Agent (LLM) | 第 3 次 LLM | `complete_task(result="succeeded")` |

**预期 LLM 调用次数：3**（启动 + 适应 + 完成）

---

## T6. "修理我的坦克，然后继续进攻"

**输入：** "修理我的坦克，然后继续进攻"
**预期 Task 类型：** managed

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | 玩家输入 | CommandProcessor | 分类为执行指令 | — |
| 2 | | Kernel | 创建 Task(id=t6, kind=managed, priority=50) | — |
| 3 | context | Task Agent (LLM) | 第 1 次 LLM → 多个 tool_use | `query_world("my_damaged_units")` → [{actor_id:58, hp:30, hp_max:100}] |
| 4 | | Task Agent | 继续 tool_use | `query_world("repair_facilities")` → [{pos:(220,610), type:"service_depot"}] |
| 5 | | Task Agent | 有维修设施 → tool_use | `start_job("MovementExpert", MovementJobConfig(actor_ids=[58], target_position=(220,610), move_mode="move", arrival_radius=3))` |
| 6 | | Kernel | 创建 Job j6a | resources=["actor:58"] |
| 7 | | Task Agent | sleep | — |
| 8 | actor:58 到达 | MovementJob | Signal: task_complete, result=succeeded | — |
| 9 | Signal | Task Agent (LLM) | 第 2 次 LLM。坦克到位 → 开始进攻 | `start_job("CombatExpert", CombatJobConfig(target_position=last_attack_target, engagement_mode="assault"))` |
| 10 | | Task Agent | sleep | — |
| 11 | 战斗结束 | CombatJob | Signal: task_complete | — |
| 12 | | Task Agent (LLM) | 第 3 次 LLM | `complete_task(result="succeeded")` |

**预期 LLM 调用次数：3**

### T6 边缘：无维修设施

| 步骤 | 组件 | 行为 |
|---|---|---|
| 4b | Task Agent | `query_world("repair_facilities")` → [] (空) |
| 5b | Task Agent | 跳过修理 → 直接 `start_job("CombatExpert", ...)` + 通知玩家"无维修设施，直接进攻" |

---

## T7. 被动事件：敌人攻击基地

**触发：** WorldModel 检测 BASE_UNDER_ATTACK（非玩家输入）

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | WorldModel diff | WorldModel | 敌方战斗单位在基地 30 格内 | `Event(type=BASE_UNDER_ATTACK, actor_id=enemy:201, position=(230,580))` |
| 2 | Event | Kernel | 预注册规则匹配 → 自动创建 Task | `Task(id=t7, kind=managed, priority=80, raw_text="defend_base", autonomy_mode=supervised)` |
| 3 | | Kernel | spawn Task Agent | 注入 context: `{threat:{enemy_actors_near_base:[...], estimated_value:1200}}` |
| 4 | context | Task Agent (LLM) | 第 1 次 LLM → tool_use | `query_world("enemy_threats_near", {position:base_pos, radius:30})` → 评估威胁 |
| 5 | | Task Agent | tool_use | `start_job("CombatExpert", CombatJobConfig(target_position=base_pos, engagement_mode="hold", retreat_threshold=0.2))` |
| 6 | start_job | Kernel | 创建 Job, 分配资源。priority=80 > 进攻 Task=50 | 从低优先级 Task 夺取部分资源。被夺 Task 的 Job → on_resource_lost → 降级继续 |
| 7 | tick | CombatJob | 自主防御 | — |
| 8 | 威胁消除 | CombatJob | Signal: task_complete, result=succeeded | — |
| 9 | | Task Agent (LLM) | 第 2 次 LLM | `complete_task(result="succeeded", summary="基地防御成功")` |
| 10 | | Kernel | 释放资源 → 自动归还给之前被夺资源的低优先级 Task | 推送通知玩家"基地防御成功" |

**预期 LLM 调用次数：2**

---

## T8. "建造一个新基地在右边矿区"

**输入：** "建造一个新基地在右边矿区"
**预期 Task 类型：** managed, supervised

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | 玩家输入 | CommandProcessor | 执行指令 | — |
| 2 | | Kernel | 创建 Task(id=t8, kind=managed, priority=50, autonomy_mode=supervised) | — |
| 3 | context | Task Agent (LLM) | 第 1 次 LLM → 多个 tool_use | `query_world("my_actors", {category:"mcv"})` → [{actor_id:99}] 有 MCV |
| 4 | | Task Agent | tool_use | `query_world("map_info", {query:"ore_fields"})` → 确定右边矿区位置 (1500,400) |
| 5 | | Task Agent | tool_use | `start_job("MovementExpert", MovementJobConfig(actor_ids=[99], target_position=(1500,400), move_mode="move", arrival_radius=5))` |
| 6 | | Kernel | 创建 Job j8a, 分配 actor:99 | — |
| 7 | | Task Agent | sleep | — |
| 8 | MCV 到达 | MovementJob | Signal: task_complete | — |
| 9 | Signal | Task Agent (LLM) | 第 2 次 LLM → tool_use | `start_job("DeployExpert", DeployJobConfig(actor_id=99, target_position=(1500,400)))` |
| 10 | | DeployJob | 调 GameAPI deploy → 成功 | Signal: task_complete, result=succeeded |
| 11 | | Task Agent (LLM) | 第 3 次 LLM | `complete_task(result="succeeded", summary="新基地已建在右边矿区")` |

**预期 LLM 调用次数：3**

### T8 边缘：无 MCV

| 步骤 | 组件 | 行为 |
|---|---|---|
| 3b | Task Agent | `query_world("my_actors", {category:"mcv"})` → [] 无 MCV |
| 4b | Task Agent | `start_job("EconomyExpert", EconomyJobConfig(unit_type="mcv", count=1, queue_type="Vehicle"))` |
| 5b | MCV 生产完成 Signal → Task Agent 唤醒 → 继续步骤 5 |

---

## T9. 连续快速下达："生产坦克" "探索地图" "别追太远"

**输入：** 三条命令快速依次到达

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | "生产坦克" | Kernel | 创建 Task t9a(kind=background, priority=40) | spawn Task Agent A |
| 2 | "探索地图" | Kernel | 创建 Task t9b(kind=managed, priority=50) | spawn Task Agent B |
| 3 | "别追太远" | Kernel | 创建 Task t9c(kind=constraint, priority=50) | spawn Task Agent C |
| 4 | 各 Agent 独立 | Task Agent A | `start_job("EconomyExpert", ...)` | EconomyJob 开始生产 |
| 5 | | Task Agent B | `start_job("ReconExpert", ...)` | ReconJob 开始侦察 |
| 6 | | Task Agent C | `create_constraint(do_not_chase, global, ...)` + `complete_task` | Constraint 生效，Task t9c 立即 succeeded |
| 7 | 并行运行 | EconomyJob + ReconJob | 各自 tick，不冲突 | EconomyJob 用 queue:Vehicle, ReconJob 用 actor |
| 8 | 资源竞争时 | Kernel | ReconJob(priority=50) > EconomyJob(priority=40) | ReconJob 优先获得 actor |
| 9 | Constraint 影响 | 未来 CombatJob | 读到 do_not_chase → clamp | ReconJob 不受影响（非战斗） |

**预期 LLM 调用次数：3 个 Task Agent 各 1-2 次 = 总计 3-5 次**

---

## T10. 玩家不说话（系统空闲行为）

**触发：** 无玩家输入，游戏持续进行

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | 每 tick | GameLoop | WorldModel.refresh() + detect_events() | — |
| 2 | 发现敌人扩张 | WorldModel | `Event(type=ENEMY_EXPANSION, data={pos:(1200,300)})` | — |
| 3 | Event | Kernel | 预注册通知规则匹配 → 推送通知 | WebSocket: `player_notification({type:"info", message:"发现敌人在(1200,300)扩张"})` |
| 4 | 前线空虚 | WorldModel | `Event(type=FRONTLINE_WEAK, data={...})` | — |
| 5 | Event | Kernel | 推送通知 | WebSocket: `player_notification({type:"warning", message:"我方前线空虚"})` |
| 6 | — | — | **系统不创建 Task，不执行动作** | 等玩家下令 |
| 7 | 例外：BASE_UNDER_ATTACK | Kernel | 自动创建防御 Task（见 T7） | 唯一的自动行动 |

**预期 LLM 调用次数：0**

---

## T11. "战况如何？"（查询指令）

**输入：** "战况如何？"

| 步骤 | 触发 | 组件 | 行为 | 系统状态变化 |
|---|---|---|---|---|
| 1 | 玩家输入 | CommandProcessor | 分类为**查询**，非执行 | — |
| 2 | | CommandProcessor | 构建 WorldModel 上下文：经济/兵力/地图/敌情/活跃任务 | 不创建 Task，不进 Kernel |
| 3 | | LLM | 第 1 次调用：system=game_advisor + user=context+问题 | — |
| 4 | | LLM | 生成回答："当前经济良好(cash:5000)，兵力优势(我方2400 vs 敌方1800)，地图探索45%，建议从东北方向发起进攻" | — |
| 5 | | Dashboard | 推送给玩家 | WebSocket: `query_response({question:"战况如何？", answer:"..."})` |

**预期 LLM 调用次数：1**
**预期副作用：无**（不改变游戏状态、不创建 Task/Job/Constraint）
