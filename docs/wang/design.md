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

所有玩家输入先经 **Adjutant（副官）**，由 Adjutant 判断路由：

**新命令** → Kernel 创建 Task → Task Agent → Job → GameAPI
**回复某个 Task 的提问** → 路由回对应 Task Agent
**查询** → Adjutant 直接 LLM+WorldModel 回答，不进 Kernel

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
| kind | str | instant / managed |
| priority | int | 0-100 |
| status | str | pending / running / waiting / succeeded / partial / failed / aborted |
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
| type | str | UNIT_DIED / UNIT_DAMAGED / ENEMY_DISCOVERED / STRUCTURE_LOST / BASE_UNDER_ATTACK / PRODUCTION_COMPLETE / ENEMY_EXPANSION / FRONTLINE_WEAK / ECONOMY_SURPLUS |
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
- 事件路由：WorldModel Event → 相关 Task Agent 和 Job。路由规则：
  - UNIT_DIED/UNIT_DAMAGED：actor_id 匹配 Job.resources → 路由给该 Job
  - ENEMY_DISCOVERED/STRUCTURE_LOST：广播给所有活跃 Task Agent
  - BASE_UNDER_ATTACK：触发预注册规则（见下）+ 广播
  - ENEMY_EXPANSION/FRONTLINE_WEAK/ECONOMY_SURPLUS：推送 player_notification，不路由给 Task
  - Kernel 无法匹配时丢弃（不是所有事件都需要消费者）
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
| query_world | query_type, params | data | 查询 WorldModel（Information Expert 产出）|
| query_planner | planner_type, params | proposal | 调用 Planner Expert 获取建议（如侦察路线、进攻方案）|
| cancel_tasks | filters | count | 批量取消匹配的其他 Task |

`complete_task` 和 `abort_job` 分离：abort_job 终止单个 Job，complete_task 终结整个 Task 并设最终状态。

### Expert 三种类型

Expert 有三种类型，不可合并：

**Information Expert（信息型）：** 只读，持续分析 WorldModel，产出派生信息/评估/告警。不绑定资源，不执行动作。
- 威胁评估（ThreatAssessor）
- 经济分析（EconomyAnalyzer）
- 地图语义（MapSemantics：chokepoint/资源区/区域控制度）
- 事件检测（EventDetector：快照 diff → Event[]）
- **WorldModel 就是一个 Information Expert**

**Planner Expert（规划型）：** 给出候选方案/建议，不绑定资源，不直接执行。Task Agent 可以调用 Planner 辅助决策。
- 侦察路线规划（ReconRoutePlanner）
- 进攻路线评估（AttackRoutePlanner）
- 生产优先级建议（ProductionAdvisor）
- 传统 AI 实现（评分/搜索/规则），不用 LLM

**Execution Expert（执行型）：** 绑资源，自主 tick 执行，产出 Job 实例。这是之前设计里的"小脑"。
- ReconExpert / CombatExpert / EconomyExpert / MovementExpert / DeployExpert

### Job（Execution Expert 的运行时实例，小脑）
- 自主 tick 执行，不等 LLM
- **直接调 GameAPI**（无中间层，Macro Actions = GameAPI 工具封装）
- 向 Task Agent 发 ExpertSignal（稀疏、面向决策，不是逐 tick 遥测）
- 接受 Task Agent 中途调参（patch）
- 内部用传统 AI：FSM / 评分 / 势场 / 寻路
- 可以调用 Information Expert 和 Planner Expert 获取分析和建议

Task Agent → Job 控制面：`start(config) / patch(params) / pause / resume / abort`
Job → Task Agent 通信：`ExpertSignal`

### WorldModel（= 一个 Information Expert + 共享状态）
- **WorldModel 就是一个 Information Expert**，不是纯基础设施
- 游戏状态查询（actors / structures / economy / map）
- 派生分析（威胁评估、区域控制、经济趋势）— Information Expert 产出
- 资源匹配（find_actors by predicates, idle_only）
- 事件检测（快照 diff → Event[]）— Information Expert 产出
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

### 升级阈值（per-Expert）
Job 在以下情况升级给 Brain：
1. 目标语义变化
2. 多条路线有本质不同的机会成本
3. 本地置信度长时间低于阈值
4. 资源损失超过重要性阈值
5. 需要和其他 Job 协调
6. 动作会违反约束或不可逆

### Task Agent 实现
在 raw SDK 上自建 agentic loop（~200-300 行）。

**Task Agent 运行模式：事件驱动 + 定时轮询**
- 收到 ExpertSignal / WorldModel Event → 唤醒
- 定时 review_interval（per-Task，默认 10s）→ 周期性唤醒检查进度
- 唤醒后进入 **multi-turn tool use 循环**：

```
唤醒（Signal / Event / 定时）
  → inject context packet
  → LLM 调用（带所有 tools）
  → LLM 返回 tool_use → 执行 tool → 结果回传给 LLM
  → LLM 可能继续调 tool（如 query_world → 判断 → start_job → patch_job）
  → LLM 返回纯文本（无 tool_use）→ 本轮结束
  → sleep，等下一次唤醒
  → max_turns 限制（默认 10）：超过则强制结束本轮
```

这是标准的 agentic tool use loop，不是"一次 tool_use"。一次唤醒中 LLM 可以多轮调用多个 tool，直到它认为当前处理完毕。

备选薄封装：PydanticAI。（详见 archive/agent_framework_research.md）
实现注意：system prompt 固定（利用 prompt caching）。
LLM 模型：待测试选型，暂定 Qwen3.5。

## 6. 玩家交互层：Adjutant（副官）

### 问题
- 多个 Task Agent 同时想和玩家说话 → 信息混乱
- 玩家回复可能是给某个 Task 的（"继续"→回复 TaskA 的提问），但系统会当新命令处理
- Task 内部 context 很大，全转发给前端 LLM 浪费 tokens

### 方案：Adjutant（副官表面层）

```
玩家 ↔ Adjutant (轻量 LLM) ↔ Kernel / Task Agents
```

Adjutant 是玩家和系统之间的**唯一对话窗口**。

**Adjutant 职责：**
- 接收所有玩家输入 → 分类：新命令 / 回复某个 Task / 查询
- 接收所有 Task-to-player 消息 → 统一格式化后呈现给玩家
- 维护对话状态 → 知道哪个 Task 在等玩家回复
- 同时服务文本输出模式和看板模式

**Adjutant 不做的事：**
- 不做战术决策
- 不持有 Task 内部状态

**查询处理：** Adjutant 识别出查询后，发起一次独立的 LLM 调用（带 WorldModel 上下文），生成回答。这不是 Adjutant "自己懂游戏"，而是一次独立的 query LLM 调用，Adjutant 只负责触发和转发结果。

### Task → 玩家（通过 Adjutant）

Task Agent 不直接给玩家发消息。通过结构化 API：

| 消息类型 | 用途 | 示例 |
|---|---|---|
| task_info | 通知，不需回复 | "已找到敌人基地 (1820,430)" |
| task_warning | 警告 | "侦察兵血量低" |
| task_question | 需要玩家回复 | "兵力不足，继续进攻还是放弃？" options=["继续","放弃"] |
| task_complete_report | 任务完成 | "包围成功，敌人基地已摧毁" |

**TaskMessage schema（Task → Adjutant）：**

| 字段 | 类型 | 说明 |
|---|---|---|
| message_id | str | 唯一 ID |
| task_id | str | 来源 Task |
| type | str | task_info / task_warning / task_question / task_complete_report |
| content | str | 消息内容 |
| options | list[str]? | task_question 时的选项 |
| timeout_s | float? | task_question 的超时 |
| default_option | str? | 超时时的默认选项 |
| priority | int | 展示优先级 |

**PlayerResponse schema（Adjutant → Task Agent）：**

| 字段 | 类型 | 说明 |
|---|---|---|
| message_id | str | 回复的 question message_id |
| task_id | str | 目标 Task |
| answer | str | 玩家选择或自由文本 |

Adjutant 收到 TaskMessage 后格式化呈现：
- 文本模式："[进攻任务] 兵力不足，继续进攻还是放弃？"
- 看板模式：Task 卡片上显示问题 + 按钮

### 玩家 → 系统（通过 Adjutant）

```
玩家输入
  → Adjutant LLM 判断:
    1. 有 pending_question？且输入像是回复？→ 路由给对应 Task Agent
    2. 明确的新命令？→ 交给 Kernel 创建新 Task
    3. 查询？→ 直接 LLM+WorldModel 回答
```

### Adjutant 的 context（极小）

```json
{
  "active_tasks": [
    {"task_id": "t1", "raw_text": "包围右边基地", "status": "running"},
    {"task_id": "t2", "raw_text": "生产坦克", "status": "running"}
  ],
  "pending_questions": [
    {"message_id": "msg_101", "task_id": "t1", "question": "兵力不足，继续还是放弃？",
     "options": ["继续", "放弃"], "default_option": "放弃", "priority": 60,
     "asked_at": 1774812000, "timeout_s": 30}
  ],
  "recent_dialogue": [
    {"from": "task:t1", "content": "侧翼A损失过半"},
    {"from": "task:t1", "content": "兵力不足，继续还是放弃？"},
  ],
  "player_input": "继续"
}
```

~500-1000 tokens。不需要 Task 的完整 Job/Expert 内部状态。

### 对话路由示例

**场景：TaskA 提问 → 玩家回复**
```
1. CombatJob 发 Signal(kind=decision_request) → Task Agent A
2. Task Agent A 决定问玩家 → task_question("兵力不足，继续还是放弃？", options=["继续","放弃"], timeout_s=30)
3. Kernel 转发给 Adjutant → Adjutant 记录 pending_question(task_id=t1)
4. Adjutant 呈现给玩家："[进攻任务] 兵力不足，继续进攻还是放弃？"
5. 玩家说："继续"
6. Adjutant LLM：检查 pending_questions → t1 在等回复 → "继续"是回复 t1
7. Adjutant 路由给 Task Agent A：player_response(message_id, answer="继续")
8. Task Agent A 收到回复 → patch_job(j1, {engagement_mode:"assault"})
```

### Pending Question 超时机制

**所有权：** Kernel 持有定时器（因为 Kernel 是确定性的，不依赖 LLM）。
**流程：**
1. Task Agent 发 task_question → Kernel 记录 pending_question(message_id, task_id, timeout_s, default_option)
2. Kernel 每 tick 检查超时
3. 超时 → Kernel 自动向 Task Agent 发送 PlayerResponse(answer=default_option)
4. Task Agent 当作玩家回复处理
5. 迟到的玩家回复 → Adjutant 检查 message_id 已超时 → 告知玩家"已按默认处理，如需更改请重新下令"

**场景：玩家在 TaskA 提问时发了新命令**
```
1. TaskA pending_question: "继续还是放弃？"
2. 玩家说："生产5辆坦克"
3. Adjutant LLM：这不像是回复 → 识别为新命令
4. 路由给 Kernel → 创建新 Task
5. pending_question 超时 → TaskA 用 default_if_timeout
```

**场景：两个 Task 同时提问**
```
1. TaskA(priority=60): "继续进攻还是放弃？"
2. TaskB(priority=40): "侦察发现第二个基地，要不要改变目标？"
3. Adjutant 按 priority 排序呈现，高优先级在前
4. 玩家说："放弃进攻，改目标"
5. Adjutant LLM 尝试拆分回复 → 路由给 TaskA("放弃") 和 TaskB("改变目标")
6. 如果 LLM 无法确定拆分 → 只路由给最高优先级 TaskA，其余保持 pending
7. 如果玩家只回答了一个 → 另一个保持 pending 直到超时
```

**多问题确定性规则：**
- 优先级高的问题先展示
- 玩家回复模糊时，只匹配最高优先级的 pending question
- 未被回复的 question 继续等待直到 timeout → 走 default_option

### Adjutant 与 CommandProcessor 的关系

之前 CommandProcessor 负责分类（执行/查询）。现在 **Adjutant 取代 CommandProcessor**：
- Adjutant = CommandProcessor + 对话管理 + 输出格式化
- 所有玩家输入先经 Adjutant，Adjutant 决定路由

### 两种输出模式

| | 文本模式 | 看板模式 |
|---|---|---|
| Task 通知 | "[侦察] 发现敌方矿车" | Task 卡片状态更新 |
| Task 提问 | "[进攻] 继续还是放弃？" | Task 卡片 + 问题弹窗/按钮 |
| 任务完成 | "侦察完成，敌人基地在右上" | Task 卡片 → succeeded |
| 系统通知 | "⚠ 敌人在扩张" | 告警条 |
| 查询回答 | 自然语言回答 | 聊天面板 |

## 错误恢复

| 故障 | 策略 |
|---|---|
| LLM API 超时/失败 | Task Agent 用 default_if_timeout 继续；重试 1 次；连续失败 → 通知玩家 |
| GameAPI 断连 | Job pause，GameLoop 重连；恢复后 Job resume |
| WorldModel 刷新失败 | 用上次快照 + 标记 stale + 告警日志；连续失败 → 通知玩家 |
| Job 未处理异常 | 捕获 → Signal(task_complete, result=failed) → Task Agent 决定重试/放弃 |

## 7. 看板 + 日志

**技术栈：** Vue 3
**双模式：** 用户面板 / 调试面板
**三区：** Operations / Tasks / Diagnostics

WebSocket 入站：command_submit, command_cancel, mode_switch
WebSocket 出站：world_snapshot(1Hz), task_update(变更时), task_list(1Hz), log_entry(实时), player_notification(事件触发), query_response(查询回复)

**时效性标注：** 系统**所有信息**（不只是对玩家的，对 LLM 的也一样）必须附带 `timestamp`。LLM 收到的 context packet、ExpertSignal、Event 都带时间，让 LLM 能判断信息新鲜度。前端展示为 "Xs ago" 格式。

**前端布局：** 正中间是类似网页 AI 的**对话界面**（Adjutant 聊天），这是主交互面。其他面板（Tasks/Ops/Diag）作为侧栏或可切换。

**语音支持：** 基础框架支持 ASR（语音→文本）+ TTS（文本→语音），或直接用多模态模型替换 Adjutant。模型与框架分离，可轻松替换。

## 8. 决策记录

| # | 决策 | 日期 |
|---|---|---|
| 1 | 三级架构：Kernel(无LLM) / Task(LLM) / Job(传统AI) | 03-29 |
| 2 | 全面重写 | 03-29 |
| 3 | GameAPI 不改，Macro = 工具封装 | 03-29 |
| 4 | 对手 AI 不纳入 | 03-29 |
| 5 | ~~4种Task~~ → 2种: Instant / Managed | 03-30 |
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
| 17 | Adjutant 副官层：玩家唯一对话窗口，负责路由/格式化/对话状态 | 03-30 |
| 18 | Task 不直接和玩家说话，通过结构化消息 API 经 Adjutant 转发 | 03-30 |
| 19 | Adjutant 取代 CommandProcessor，统一处理输入分类+对话管理 | 03-30 |
| 20 | Expert 实现必须调研真实 RTS AI，使用 BT/FSM/ST + 数据驱动配置 | 03-30 |
| 21 | OpenCodeAlert 可按需修改，无约束 | 03-30 |
| 22 | 移除 the-seed 子库（NLU 规则迁出后删除） | 03-30 |
| 23 | 时效性标注覆盖所有信息（含 LLM context），不只是前端展示 | 03-30 |
| 24 | 前端主界面 = 对话界面（Adjutant chat），其他为辅 | 03-30 |
| 25 | 所有用模型的地方做模型-框架分离，可轻松替换模型 | 03-30 |
| 26 | 支持语音 ASR+TTS 基础框架，可替换为多模态模型 | 03-30 |
| 27 | 全流程 log + benchmark 框架：每步耗时记录，可查询，为优化准备 | 03-30 |
| 28 | 删除 autonomy_mode (supervised/fire_and_forget)，所有 Signal 发给 Task Agent，Agent 自行判断 | 03-30 |
| 29 | Task 类型简化为 Instant + Managed。Background=Managed, Constraint=Instant | 03-30 |
| 30 | 开发阶段 mock GameAPI，集成测试阶段 live | 03-30 |
| 31 | Expert 三种类型必须保留：Information / Planner / Execution | 03-30 |
| 32 | WorldModel = 一个 Information Expert + 共享状态，不是纯基础设施 | 03-30 |

## 9. 场景推演："探索地图，找到敌人基地"

### 玩家输入
```
"探索地图，找到敌人基地"
```

### Kernel 创建 Task，spawn Task Agent
```
Task(task_id="t1", raw_text="探索地图，找到敌人基地",
     kind="managed", priority=50, status="running",
     )
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

t=15s   Task Agent 收到 Signal（progress 类型）
        Task Agent 判断不需要介入 → 继续 sleep

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
| t=15s | 0次 | progress signal, Task Agent 判断不介入 |
| t=42s | 1次 | task_complete, 判断成功 |
| **总计** | **2次** | 其余全是 Job 自主执行 |

侦察兵死亡且无替补时多 1 次（decision_request）。正常流程只要 2 次 LLM 调用。

## 10. 常见指令映射

| 玩家指令 | Task kind | Task Agent 行为 |
|---|---|---|
| 探索地图找敌人基地 | managed | start_job(ReconExpert) |
| 生产5辆坦克 | background | start_job(EconomyExpert) |
| 包围右边基地 | managed | query_world → start_job(Recon) → 完成后 start_job(Combat×2-3) |
| 所有部队撤退 | managed | cancel_tasks({expert_type:Combat}) → start_job(Movement, move_mode=retreat) |
| 别追太远 | constraint | create_constraint(do_not_chase, global, {max_distance:20}, clamp) |
| 修理坦克然后进攻 | managed | start_job(Movement, target=repair_facility) → 到达后 patch 或新建 CombatJob |
| 部署基地车 | instant | start_job(DeployExpert) → 立即 complete_task |
| 建新基地在右边矿区 | managed | query_world(有MCV?) → 有:Movement到位+Deploy / 无:Economy生产MCV → 到位后Deploy。地点有敌人:先Combat清理或换地点 |
| 战况如何？ | 查询（不进Kernel） | LLM + WorldModel 上下文 → 直接回答玩家 |
| 从哪进攻？ | 查询（不进Kernel） | LLM + WorldModel 上下文 → 分析建议，不执行 |
| 现在该做什么？ | 查询（不进Kernel） | LLM + WorldModel 上下文 → 战略建议 |

修理 = MovementExpert（移动到维修设施）+ GameAPI repair 命令。
无维修设施 → Task Agent 通过 query_world 发现 → 跳过修理，直接执行后续动作（继续进攻）。通知玩家"无维修设施，跳过修理"。

**通用前置条件缺失策略：** Task Agent 遇到前置条件不满足时（无 MCV、无维修设施、目标区域有敌人），由 LLM 自行判断：生产/等待/跳过/先清理再继续。这是 Task Agent（大脑）的核心价值——处理计划外情况。

## 11. 现有代码处置

详见 `code_asset_inventory.md`。
Keep: GameAPI, models, NLU 管线。
Reference: jobs, agents, intel, tactical_core。
Delete: standalone launchers。
