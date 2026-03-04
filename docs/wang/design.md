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

```
玩家: "探索地图，找到敌人基地"
  → Kernel 创建 Task，spawn Task Agent (LLM)
  → Task Agent 理解意图，创建 Job: ReconExpert(search_region="enemy_half", target_type="base")
  → Kernel 为 Job 分配资源（快速单位）
  → Job 自主 tick：查 WorldModel → 选路线 → 调 GameAPI 移动单位
  → Job 发 Signal 给 Task Agent：发现敌方矿车 / 侦察兵受伤 / 找到基地
  → Task Agent 根据 Signal 决策：调整方向 / 继续 / 放弃
  → Job 完成 → Task Agent 判断成功 → Kernel 回收资源
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
| status | str | running / waiting / completed / aborted |

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

**EconomyJobConfig:**
- unit_type: str
- count: int
- queue_type: str
- repeat: bool

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
| scope | str | global / task_id |
| params | dict | |
| active | bool | |

### ExpertSignal（Job → Task Agent）
| 字段 | 类型 | 说明 |
|---|---|---|
| task_id, job_id | str | |
| kind | str | progress / risk_alert / blocked / decision_request / resource_lost / target_found / task_complete |
| summary | str | 人类可读 |
| world_delta | dict | 发生了什么 |
| expert_state | dict | phase, progress_pct, local_confidence |
| decision | dict? | 需要 Brain 决策时：options + default_if_timeout |

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
- 取消：Kernel.cancel(task_id) → Task Agent abort → 回收资源

### Task Agent（LLM 大脑，per-Task 实例）
- 理解玩家意图（接收 raw_text + WorldModel 上下文）
- 通过 tool_use 创建/配置 Job（框架校验 config schema）
- 协调多个 Job 的时序依赖
- 收到 ExpertSignal 时决策：调整参数 / 启动新 Job / 取消 Job
- 判断 Task 整体成败
- 事件驱动，不轮询。收到 Signal 才醒来。
- 有 `default_if_timeout`：Expert 等不到 Brain 回复就用默认策略

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

## 8. 现有代码处置

详见 `code_asset_inventory.md`。
Keep: GameAPI, models, NLU 管线。
Reference: jobs, agents, intel, tactical_core。
Delete: standalone launchers。

## 9. 待定

- [x] Task Agent 框架：raw SDK 自建（~150-250 行），备选 PydanticAI
- [ ] Expert 扩展机制和注册方式
- [ ] 场景推演需要用新架构重写
