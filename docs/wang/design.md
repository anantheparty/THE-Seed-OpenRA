# System Design — Directive-Driven RTS Agent

## 0. 定位与架构

LLM 赋能传统游戏 AI 的副官系统。不做对手 AI（如需控敌，启动另一个副官实例）。

**三级架构：**
```
Kernel（无 LLM，确定性调度）
  ├── Task 1 ─ LLM 大脑 ─┬─ Job A (Expert 小脑, 自主执行)
  │                       ├─ Job B (Expert 小脑, 自主执行)
  │                       └─ Job C (Expert 小脑, 自主执行)
  ├── Task 2 ─ LLM 大脑 ─── Job D
  └── Task 3 ─ LLM 大脑 ─── Job E
```

| 层 | 实现 | 速度 | 职责 |
|---|---|---|---|
| **Kernel** | 规则 | 毫秒 | 任务调度、资源分配、冲突仲裁、事件路由 |
| **Task Agent** | LLM | 秒级 | 理解意图、选 Expert、设参数、协调 Job、监控事件 |
| **Job** | 传统 AI | tick级 | 自主执行（侦察/战斗/生产），直接调 GameAPI |

## 1. 流程

玩家输入分三条路径：

**执行路径**（改变游戏状态）：
```
"探索地图找敌人基地" / "生产坦克" / "包围基地"
  → Kernel 创建 Task → spawn Task Agent (LLM)
  → Task Agent 创建 Job → Expert 自主执行 → GameAPI
```

**查询路径**（不改变游戏状态）：
```
"战况如何？" / "从哪进攻？" / "现在该做什么？"
  → 直接 LLM + WorldModel 上下文 → 回答玩家
  → 不进 Kernel，不创建 Task，不绑资源
```

**主动通知**（系统→玩家，无需玩家输入）：
```
WorldModel 事件触发 → Kernel 预注册规则检查 → 推送通知到看板
例："发现敌人在扩张" / "我方前线空虚" / "经济充裕，可以考虑进攻"
不自动执行动作，只通知玩家。玩家决定是否下令。
```

## 2. 运行时

**单线程 GameLoop，默认 10Hz。**

每 tick：
1. WorldModel.refresh()（分层：actor 位置每 tick，经济 500ms，地图 1s）
2. WorldModel.detect_events() → 分发给 Kernel
3. Kernel 路由事件给相关 Task Agent 和 Job
4. GameLoop tick 到期的 Job（每个 Job 有自己的 tick_interval）
5. 推送看板

| Job 类型 | tick_interval |
|---|---|
| CombatJob | 0.2s |
| ReconJob | 1.0s |
| EconomyJob | 5.0s |

启动顺序：GameAPI → UnitRegistry → WorldModel → Kernel → Dashboard → GameLoop

## 3. 数据模型

### Task
| 字段 | 类型 | 说明 |
|---|---|---|
| task_id | str | |
| raw_text | str | 玩家原始输入 |
| kind | str | instant / managed / background / constraint |
| priority | int | 0-100 |
| status | str | pending / running / waiting / succeeded / partial / failed / aborted |
| autonomy_mode | str | fire_and_forget / supervised |
| created_at | float | |

### Job
| 字段 | 类型 | 说明 |
|---|---|---|
| job_id | str | |
| task_id | str | 所属 Task |
| expert_type | str | ReconExpert, CombatExpert... |
| config | ExpertConfig | 强格式，schema 由 Expert 类型定义 |
| resources | list[str] | 当前持有的资源 |
| status | str | running / waiting / succeeded / failed / aborted |

### Expert Config Schema（每种 Expert 不同）

**ReconJobConfig:**
- search_region: str（northeast / enemy_half / full_map）
- target_type: str（base / army / expansion）
- target_owner: str（enemy）
- retreat_hp_pct: float
- avoid_combat: bool

**CombatJobConfig:**
- target_position: tuple
- engagement_mode: str（assault / harass / hold / surround）
- max_chase_distance: int
- retreat_threshold: float

**MovementJobConfig:**
- target_position: tuple（目标位置）
- actor_ids: list[int]（要移动的单位，可选，默认用 ResourceNeed 分配）
- move_mode: str（move / attack_move / retreat）
- arrival_radius: int（到达判定半径）

**DeployJobConfig:**
- actor_id: int（要部署的 MCV/建筑单位）
- target_position: tuple（部署位置）
- building_type: str（可选，如 "ConstructionYard"）

DeployJob：单次动作，调 GameAPI deploy → 成功/失败 → task_complete。

**EconomyJobConfig:**
- unit_type: str
- count: int
- queue_type: str
- repeat: bool

EconomyJob 语义：每生产完一个单位发 `progress` Signal。中途断钱/断电/工厂损毁 → Job 进 waiting（不 fail）。count 全部完成或被 abort → `task_complete`。部分完成 = result=partial。

### ResourceNeed（声明式）
| 字段 | 类型 | 说明 |
|---|---|---|
| job_id | str | |
| kind | str | actor / production_queue |
| count | int | |
| predicates | dict | {category: vehicle, mobility: fast} |

Kernel 持续满足：死了补、新造自动分配、不足降级运行。只有 Task Agent 有权判断"没希望了"取消。

### Constraint
| 字段 | 类型 | 说明 |
|---|---|---|
| constraint_id | str | |
| kind | str | do_not_chase / economy_first / defend_base |
| scope | str | global / expert_type:CombatExpert / task_id:xxx |
| params | dict | {max_chase_distance: 20} |
| enforcement | str | clamp（Job 内部限制）/ escalate（升级给 Brain）|
| active | bool | |

创建：Task Agent 调 `create_constraint` tool。
传播：Job 每 tick 从 WorldModel 读取匹配自己 scope 的活跃 Constraint。
enforcement=clamp：Job 自动遵守。enforcement=escalate：Job 发 decision_request。
**默认 scope 策略：** 玩家裸说的约束命令（如"别追太远"）默认 scope=global，影响所有当前和未来匹配的 Job。Task Agent 可通过 LLM 判断是否需要更窄的 scope。

### ExpertSignal（Job → Task Agent）
| 字段 | 类型 | 说明 |
|---|---|---|
| task_id, job_id | str | |
| kind | str | progress / risk_alert / blocked / decision_request / resource_lost / target_found / task_complete |
| summary | str | 人类可读 |
| world_delta | dict | 发生了什么 |
| expert_state | dict | phase, progress_pct, local_confidence |
| result | str? | task_complete 时：succeeded / failed / aborted |
| data | dict? | task_complete 时的结果数据 |
| decision | dict? | decision_request 时：options + default_if_timeout |

### Event（WorldModel 事件）
| 字段 | 类型 | 说明 |
|---|---|---|
| type | str | UNIT_DIED / UNIT_DAMAGED / ENEMY_DISCOVERED / BASE_UNDER_ATTACK / PRODUCTION_COMPLETE |
| actor_id | int? | |
| position | tuple? | |
| data | dict | |
| timestamp | float | |

### NormalizedActor
| 字段 | 类型 | 说明 |
|---|---|---|
| actor_id | int | |
| name / display_name | str | 2tnk / 重型坦克 |
| owner | str | self / enemy / neutral |
| category | str | infantry / vehicle / building / harvester / mcv |
| position | tuple | |
| hp / hp_max | int | |
| is_alive / is_idle | bool | |
| mobility | str | fast / medium / slow / static |
| combat_value | float | |
| can_attack / can_harvest | bool | |
| weapon_range | int | |

## 4. 组件职责

### Kernel（无 LLM）
- 创建/销毁 Task 和 Task Agent
- 资源分配：按 ResourceNeed + 优先级，持续满足
- 冲突仲裁：多 Task 争资源 → 按优先级
- 抢占：高优先级夺低优先级资源（单资源 Job → abort，多资源 Job → 降级）
- 事件路由：WorldModel Event → 相关 Task Agent 和 Job
- 取消：Kernel.cancel(task_id) 或 Kernel.cancel_tasks(filters) → 批量取消
- **被动事件自动响应**：预注册的事件规则（写死，不需要 LLM），如：
  - BASE_UNDER_ATTACK → 自动创建 Task(kind=managed, raw_text="defend_base", priority=80)
  - 防御 Task priority=80 > 一般进攻 Task priority=50，Kernel 按优先级分配资源
  - 进攻 Task 不会被强制取消，而是降级运行（资源被部分夺走）
  - 防御 Task Agent 决定调多少兵回防，不是全部

### Task Agent（LLM 大脑，per-Task 实例）
- 理解玩家意图（接收 raw_text + WorldModel 上下文）
- 通过 tool_use 创建/配置 Job（框架校验 config schema）
- 协调多个 Job 的时序依赖（LLM 自然记忆创建了哪些 Job 和收到了什么 Signal）
- 收到 ExpertSignal 时决策：调整参数 / 启动新 Job / 取消 Job
- 判断 Task 整体成败
- 事件驱动，不轮询。收到 Signal 才醒来。
- 一次 wake 可以发多个 tool_use（如同时创建 3 个 CombatJob）
- 有 `default_if_timeout`：Expert 等不到 Brain 回复就用默认策略

#### Task Agent Tools

| tool | 参数 | 返回 | 说明 |
|---|---|---|---|
| start_job | expert_type, config | job_id | 创建并启动 Job |
| patch_job | job_id, params | ok | 中途调整 Job 参数 |
| pause_job | job_id | ok | 暂停 |
| resume_job | job_id | ok | 恢复 |
| abort_job | job_id | ok | 终止 Job |
| complete_task | result, summary | ok | 标记 Task 成功/失败/部分完成 |
| create_constraint | kind, scope, params, enforcement | constraint_id | 创建约束（enforcement=clamp\|escalate）|
| remove_constraint | constraint_id | ok | 移除约束 |
| query_world | query_type, params | data | 查询 WorldModel（actors/map/economy/threats）|
| cancel_tasks | filters | count | 批量取消匹配的其他 Task（如"所有战斗任务"）|

`complete_task` 和 `abort_job` 分离：abort_job 终止单个 Job，complete_task 终结整个 Task 并设最终状态。

### Job（传统 AI 小脑，per-Job 实例）
- 自主 tick 执行，不等 LLM
- **直接调 GameAPI**（无中间层，Macro Actions = GameAPI 工具封装）
- 向 Task Agent 发 ExpertSignal（稀疏、面向决策，不是逐 tick 遥测）
- 接受 Task Agent 中途调参（patch）
- 内部用传统 AI：FSM / 评分 / 势场 / 寻路

Task Agent → Job 控制面：`start(config) / patch(params) / pause / resume / abort`
Job → Task Agent 通信：`ExpertSignal`

### WorldModel
- 游戏状态查询（actors / structures / economy / map）
- 资源匹配（find_actors by predicates, idle_only）
- 事件检测（快照 diff → Event[]）
- 分层刷新
- 运行时状态（active tasks/jobs, resource bindings, constraints）

### GameAPI
- 底层 Socket RPC，不改
- Macro Actions = 工具形式封装，不是架构层

## 5. 大脑-小脑协作

### 核心原则
1. Brain 是事件驱动的监督者，不是逐帧控制器
2. Job 保持自主反射，Brain 慢/不响应也继续运行
3. 上下文由框架推送给 Brain（context packet），不靠 LLM 主动查询
4. Signal 是稀疏的、面向决策的，不是逐 tick 遥测

### Context Packet（框架 → Brain）
```
task: {task_id, raw_text, priority}
jobs: [{job_id, expert_type, phase, resources, config}]
world_summary: {economy, military, map, known_enemy}
recent_signals: [...]
open_decisions: [...]
```
注入时机：task 启动全量 / Signal 触发 delta / 长时间无事件压缩摘要

### 自治模式
| 模式 | 适用 | Brain 介入 |
|---|---|---|
| fire_and_forget | 常规/可逆 | 仅异常 |
| supervised | 不可逆/多 Job 协调 | 丰富更新+审批 |

### 升级阈值（per-Expert）
Job 在以下情况升级给 Brain：
1. 目标语义变化
2. 多条路线有本质不同的机会成本
3. 本地置信度长时间低于阈值
4. 资源损失超过重要性阈值
5. 需要和其他 Job 协调
6. 动作会违反约束或不可逆

### Task Agent 实现
在 raw SDK 上自建轻量事件驱动循环（~150-250 行）。
模式：`event → inject context → 一次 tool_use → sleep`，不需要 workflow 引擎。
备选薄封装：PydanticAI。（详见 archive/agent_framework_research.md）
实现注意：system prompt 固定（利用 prompt caching）、max_turns 限制防循环。
LLM 模型：待测试选型，暂定 Qwen3.5（便宜快速）。

## 6. 看板 + 日志

**技术栈：** Vue 3
**双模式：** 用户面板 / 调试面板
**三区：** Operations / Tasks / Diagnostics

WebSocket 入站：command_submit, command_cancel, mode_switch
WebSocket 出站：world_snapshot(1Hz), task_update(变更时), task_list(1Hz), log_entry(实时)

## 7. 决策记录

| # | 决策 | 日期 |
|---|---|---|
| 1 | 三级架构：Kernel(无LLM) / Task(LLM) / Job(传统AI) | 03-29 |
| 2 | 全面重写 | 03-29 |
| 3 | GameAPI 不改，Macro = 工具封装 | 03-29 |
| 4 | 对手 AI 不纳入 | 03-29 |
| 5 | 4种Task: Instant/Managed/Background/Constraint | 03-29 |
| 6 | 单线程 GameLoop 10Hz | 03-30 |
| 7 | Job 直接调 GameAPI，无中间层 | 03-30 |
| 8 | Job config 强格式，每种 Expert 定义自己的 schema | 03-30 |
| 9 | 声明式资源模型，Kernel 持续满足 | 03-30 |
| 10 | 大脑-小脑模式：Brain 监督 + Job 自主执行 | 03-30 |
| 11 | 看板 Vue 3 | 03-29 |
| 12 | Task Agent 框架：raw SDK 自建 ~200 行，LLM 暂定 Qwen3.5 | 03-30 |
| 13 | Expert 写死在代码中，扩展 = 收集数据+开发代码 | 03-30 |
| 14 | 被动事件(BASE_UNDER_ATTACK)由 Kernel 预注册规则自动创建 Task | 03-30 |
| 15 | 系统不自主执行战略动作，只通知玩家。紧急防御除外（决策14）| 03-30 |
| 16 | 查询指令（战况/建议）走 LLM+WorldModel 直接回答，不进 Kernel | 03-30 |

## 8. 场景推演："探索地图，找到敌人基地"

### 玩家输入
```
"探索地图，找到敌人基地"
```

### Kernel 创建 Task，spawn Task Agent
```
Task(task_id="t1", raw_text="探索地图，找到敌人基地",
     kind="managed", priority=50, status="running",
     autonomy_mode="fire_and_forget")
```
Kernel 注入 context packet 给 Task Agent（LLM），包含 WorldModel 摘要。

### Task Agent 第一次 LLM 调用
Task Agent 收到 context packet（经济、兵力、地图探索率、已知敌情）。

LLM tool_use 响应：
```
call: start_job(
    expert_type="ReconExpert",
    config=ReconJobConfig(
        search_region="enemy_half",
        target_type="base",
        target_owner="enemy",
        retreat_hp_pct=0.3,
        avoid_combat=True
    )
)
```
→ Kernel 创建 Job，按 ResourceNeed（1 个 fast unit）分配 actor:57
→ ReconExpert Job 开始自主 tick

Task Agent 进入 sleep，等待 Signal。

### Job 自主执行

```
t=0s    ReconJob tick: 查 WorldModel 未探索区域
        评分: 对角方向 > 中部（RTS 常识）
        调 GameAPI: move actor:57 → (1600, 200)

t=15s   WorldModel Event: ENEMY_DISCOVERED actor:201 (矿车)
        ReconJob: 矿车附近可能有基地，调整方向
        调 GameAPI: move actor:57 → (1800, 420)
        发 Signal 给 Task Agent:
          ExpertSignal(kind="progress",
            summary="发现敌方矿车，调整侦察方向",
            expert_state={phase: "tracking", progress_pct: 40})

t=15s   Task Agent 收到 Signal（fire_and_forget 模式）
        progress 类型，无 decision_request → 不需要 LLM 介入
        继续 sleep

t=30s   WorldModel Event: UNIT_DAMAGED actor:57 (HP 100→85)
        ReconJob: HP 85% > retreat_hp_pct 30%，继续
        调 GameAPI: attack_move actor:57 → (1820, 430)

t=42s   WorldModel: 发现 3 个敌方建筑 at (1820, 430)
        ReconJob: 目标达成
        发 Signal:
          ExpertSignal(kind="task_complete",
            summary="找到敌人基地 (1820,430)，3个建筑",
            world_delta={enemy_base_pos: (1820,430), structures: 3})

t=42s   Task Agent 收到 task_complete Signal → LLM 判断任务成功
        LLM tool_use: complete_task(result="succeeded", summary="找到敌人基地(1820,430)")
        → Kernel 终止所有 Job，回收资源，Task status → succeeded
        Kernel 释放 actor:57，通知看板
```

### 边缘：t=20s 侦察兵死了

```
WorldModel Event: UNIT_DIED actor:57
  → Kernel: 从 Job 移除 actor:57，按 ResourceNeed 尝试补充
  → 空闲池有 actor:83 (吉普车, fast) → 自动分配给 Job
  → ReconJob.on_resource_granted([83]) → 继续执行

  如果空闲池没有 fast 单位：
  → Job 降级 waiting，发 Signal:
    ExpertSignal(kind="resource_lost",
      summary="侦察兵阵亡，无可用替补",
      decision={
        options: ["wait_for_production", "use_infantry", "abort"],
        default_if_timeout: "wait_for_production",
        deadline_s: 3.0
      })
  → Task Agent 醒来，LLM 选择一个选项（或 3s 超时用 default）
  → 如果选 "use_infantry": patch_job(config={avoid_combat:False, ...})
  → 如果等待: 新单位造出来 → Kernel 自动分配 → Job 恢复
```

### 边缘：用户说"取消探索"

```
新 Task(kind="instant", raw_text="取消探索")
  → Task Agent (LLM): 理解意图是取消
  → LLM tool_use: cancel_tasks(filters={task_id: "t1"})
  → Kernel: abort Job → 释放资源 → Task status=aborted
```

### 边缘：用户说"用侦察车去攻击矿车"（抢占）

```
新 Task(kind="managed", raw_text="用侦察车去攻击矿车", priority=60)
  → Task Agent (LLM): 需要 actor:57，创建 CombatJob
  → Kernel: actor:57 被 ReconJob(priority=50) 占用
  → 60 > 50，从 ReconJob 夺取 actor:57
  → ReconJob 只有一个资源 → abort → Signal(task_complete, result=aborted)
  → 原 Task Agent 收到 abort signal → 任务结束
  → actor:57 分配给新 CombatJob
```

### 此场景 LLM 被调用了几次？

| 时刻 | LLM 调用 | 原因 |
|---|---|---|
| t=0s | 1次 | 理解意图 + start_job |
| t=15s | 0次 | progress signal, fire_and_forget 不唤醒 |
| t=42s | 1次 | task_complete, 判断成功 |
| **总计** | **2次** | 其余全是 Job 自主执行 |

侦察兵死亡且无替补时多 1 次（decision_request）。正常流程只要 2 次 LLM 调用。

## 9. 常见指令映射

| 玩家指令 | Task kind | Task Agent 行为 |
|---|---|---|
| 探索地图找敌人基地 | managed | start_job(ReconExpert) |
| 生产5辆坦克 | background | start_job(EconomyExpert) |
| 包围右边基地 | managed/supervised | query_world → start_job(Recon) → 完成后 start_job(Combat×2-3) |
| 所有部队撤退 | managed | cancel_tasks({expert_type:Combat}) → start_job(Movement, move_mode=retreat) |
| 别追太远 | constraint | create_constraint(do_not_chase, global, {max_distance:20}, clamp) |
| 修理坦克然后进攻 | managed | start_job(Movement, target=repair_facility) → 到达后 patch 或新建 CombatJob |
| 部署基地车 | instant | start_job(DeployExpert) → 立即 complete_task |
| 建新基地在右边矿区 | managed/supervised | query_world(有MCV?) → 有:Movement到位+Deploy / 无:Economy生产MCV → 到位后Deploy。地点有敌人:先Combat清理或换地点 |
| 战况如何？ | 查询（不进Kernel） | LLM + WorldModel 上下文 → 直接回答玩家 |
| 从哪进攻？ | 查询（不进Kernel） | LLM + WorldModel 上下文 → 分析建议，不执行 |
| 现在该做什么？ | 查询（不进Kernel） | LLM + WorldModel 上下文 → 战略建议 |

修理 = MovementExpert（移动到维修设施）+ GameAPI repair 命令。
无维修设施 → Task Agent 通过 query_world 发现 → 跳过修理，直接执行后续动作（继续进攻）。通知玩家"无维修设施，跳过修理"。

**通用前置条件缺失策略：** Task Agent 遇到前置条件不满足时（无 MCV、无维修设施、目标区域有敌人），由 LLM 自行判断：生产/等待/跳过/先清理再继续。这是 Task Agent（大脑）的核心价值——处理计划外情况。

## 10. 测试清单

每个场景列出玩家输入和**每一步预期系统行为**。实现后逐条验证。

### T1. "探索地图，找到敌人基地"
1. 玩家输入 → CommandProcessor 识别为执行指令
2. Kernel 创建 Task(kind=managed, priority=50, autonomy=fire_and_forget)
3. Kernel spawn Task Agent (LLM)，注入 context packet（WorldModel 摘要）
4. Task Agent 第一次 LLM 调用 → tool_use: `start_job(ReconExpert, ReconJobConfig(search_region="enemy_half", target_type="base", target_owner="enemy", retreat_hp_pct=0.3, avoid_combat=True))`
5. Kernel 为 Job 分配 ResourceNeed(kind=actor, count=1, predicates={mobility:fast}) → 分配 actor
6. ReconJob 自主 tick：查 WorldModel 未探索区域 → 评分 → 调 GameAPI move
7. 发现敌方单位 → ReconJob 发 Signal(kind=progress, summary="发现敌方矿车") → Task Agent 不唤醒（fire_and_forget，progress 不唤醒）
8. 发现敌方建筑群 → ReconJob 发 Signal(kind=task_complete, result=succeeded, data={base_pos:(x,y)})
9. Task Agent 唤醒 → LLM tool_use: `complete_task(result="succeeded", summary="找到敌人基地(x,y)")`
10. Kernel 终止 Job，回收资源，推送看板 + 通知玩家
11. **预期 LLM 调用次数：2**

### T2. "生产5辆重型坦克"
1. 玩家输入 → 执行指令
2. Kernel 创建 Task(kind=background, priority=40, autonomy=fire_and_forget)
3. Task Agent → `start_job(EconomyExpert, EconomyJobConfig(unit_type="2tnk", count=5, queue_type="Vehicle", repeat=False))`
4. Kernel 分配 ResourceNeed(kind=production_queue)
5. EconomyJob tick：调 GameAPI produce → 每完成一个单位发 Signal(kind=progress, expert_state={completed:1, remaining:4})
6. 中途断钱 → EconomyJob 进 waiting 状态（不 fail），恢复后继续
7. 5 个全部完成 → Signal(kind=task_complete, result=succeeded)
8. Task Agent → `complete_task(result="succeeded")`
9. **预期 LLM 调用次数：2**（启动 + 完成）

### T3. "所有部队撤退回基地"
1. 玩家输入 → 执行指令
2. Kernel 创建 Task(kind=managed, priority=70)
3. Task Agent → `query_world(query_type="my_combat_actors")` 获取所有战斗单位
4. Task Agent → `cancel_tasks(filters={expert_type:"CombatExpert"})` 取消所有战斗任务
5. Kernel abort 所有匹配的 CombatJob → 释放资源
6. Task Agent → `start_job(MovementExpert, MovementJobConfig(actor_ids=[...], target_position=base_pos, move_mode="retreat"))`
7. MovementJob tick：调 GameAPI move → 单位向基地移动
8. 全部到达 → Signal(kind=task_complete, result=succeeded)
9. Task Agent → `complete_task(result="succeeded")`
10. **预期 LLM 调用次数：2-3**

### T4. "别追太远"
1. 玩家输入 → 执行指令（constraint 类）
2. Kernel 创建 Task(kind=constraint)
3. Task Agent → `create_constraint(kind="do_not_chase", scope="global", params={max_distance:20}, enforcement="clamp")`
4. Kernel 创建 Constraint，存入 WorldModel
5. Task Agent → `complete_task(result="succeeded", summary="已设置：别追太远")`
6. 所有活跃 CombatJob 下一次 tick 时从 WorldModel 读到新 Constraint → 自动 clamp 追击距离
7. **预期 LLM 调用次数：1**

### T5. "包围右边那个基地"
1. 玩家输入 → 执行指令
2. Kernel 创建 Task(kind=managed, priority=60, autonomy=supervised)
3. Task Agent → `query_world(query_type="enemy_bases")` → 找到右边的基地位置
4. Task Agent → `start_job(ReconExpert, ...)` 侦察基地周围地形（如需要）
5. 侦察完成 Signal → Task Agent 唤醒
6. Task Agent 一次 wake 发多个 tool_use：
   - `start_job(CombatExpert, CombatJobConfig(target_position=..., engagement_mode="surround", ...))` × 2-3 个侧翼
7. Kernel 为每个 CombatJob 分配资源
8. CombatJob 自主执行：各路集结 → 同时压上
9. 一路被打退 → Signal(kind=resource_lost) → Task Agent 唤醒 → 决定补兵/改两路/取消
10. 包围成功 → Signal(kind=task_complete) → Task Agent → `complete_task`
11. **预期 LLM 调用次数：3-5**

### T6. "修理我的坦克，然后继续进攻"
1. 玩家输入 → 执行指令
2. Task Agent → `query_world(query_type="my_damaged_tanks")` + `query_world(query_type="repair_facilities")`
3. 有维修设施 → `start_job(MovementExpert, MovementJobConfig(target_position=repair_pos, move_mode="move"))`
4. 到达维修点 → Signal(task_complete) → Task Agent 唤醒
5. Task Agent → `start_job(CombatExpert, CombatJobConfig(...))`（继续进攻）
6. 无维修设施 → Task Agent 跳过修理，直接 start_job(CombatExpert)，通知玩家"无维修设施"
7. **预期 LLM 调用次数：2-3**

### T7. 被动事件：敌人攻击基地
1. WorldModel 检测 BASE_UNDER_ATTACK 事件
2. Kernel 预注册规则触发 → 自动创建 Task(kind=managed, priority=80, raw_text="defend_base")
3. Task Agent → `query_world` 评估威胁 → `start_job(CombatExpert, CombatJobConfig(engagement_mode="hold", target_position=base_pos))`
4. Kernel 按 priority=80 > 进攻 Task=50 分配资源 → 进攻 Task 降级（资源被部分夺走）
5. 威胁消除 → Signal(task_complete) → Task Agent → `complete_task`
6. 被夺资源释放 → Kernel 自动归还给低优先级 Task
7. **预期 LLM 调用次数：2**

### T8. "建造一个新基地在右边矿区"
1. Task Agent → `query_world` 查 MCV 和目标位置
2. 有 MCV → `start_job(MovementExpert, ..., target=矿区位置)` → 到达后 `start_job(DeployExpert, DeployJobConfig(actor_id=mcv, target_position=...))`
3. 无 MCV → `start_job(EconomyExpert, EconomyJobConfig(unit_type="mcv", count=1, ...))` → 生产完成后继续步骤 2
4. 目标位置有敌人 → Task Agent 决定：先清理(`start_job(CombatExpert)`) 或换位置
5. **预期 LLM 调用次数：3-5**

### T9. 连续快速下达："生产坦克" "探索地图" "别追太远"
1. 三条命令依次到达 Kernel
2. Kernel 创建 3 个 Task：background + managed + constraint
3. 三个 Task Agent 分别 spawn，各自独立处理
4. EconomyJob 和 ReconJob 并行运行，不冲突
5. Constraint 创建后，ReconJob（非战斗）不受影响，未来 CombatJob 受影响
6. 资源竞争：如果 ReconJob 和 EconomyJob 争 actor → Kernel 按优先级分配
7. **预期 LLM 调用次数：每个 Task 1-2 次 = 总计 3-5 次**

### T10. 玩家不说话（系统空闲行为）
1. 无玩家输入
2. GameLoop 持续 tick：WorldModel 刷新 → 事件检测
3. WorldModel 检测到"敌人扩张到新矿区" → Kernel 预注册通知规则触发 → 推送看板通知玩家
4. WorldModel 检测到"我方前线空虚" → 推送通知
5. **系统不自主创建 Task，不自主执行动作**（紧急防御除外）
6. 玩家看到通知后决定是否下令
7. **预期 LLM 调用次数：0**

### T11. "战况如何？"（查询指令）
1. 玩家输入 → CommandProcessor 识别为**查询**，非执行
2. 不进 Kernel，不创建 Task
3. 直接 LLM 调用 + WorldModel 上下文注入（经济/兵力/地图/敌情/活跃任务摘要）
4. LLM 生成战况分析回答 → 推送给玩家
5. **预期 LLM 调用次数：1**
6. **预期副作用：无**（不改变游戏状态、不创建 Task/Job）

## 11. 现有代码处置

详见 `code_asset_inventory.md`。
Keep: GameAPI, models, NLU 管线。
Reference: jobs, agents, intel, tactical_core。
Delete: standalone launchers。
