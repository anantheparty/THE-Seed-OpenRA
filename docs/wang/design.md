# System Design — Directive-Driven RTS Agent

## 0. 定位

LLM 赋能传统游戏 AI 的副官系统。不做对手 AI（如需控敌，启动另一个副官实例）。

**核心架构隐喻：大脑 + 小脑**
- **大脑（LLM agent）**：理解用户意图、选择专家、设定参数、监控执行、处理异常
- **小脑（Expert 自主控制器）**：接管后自主执行（侦察、战斗、生产），实时反应，不等 LLM

LLM 不做逐帧操控。LLM 说"侦察右上找基地"然后**交权**给 ReconExpert，Expert 自主跑。LLM 只在 Expert 报告事件（侦察兵死了、发现目标、遇到困难）时介入决策。

**Expert 不是 LLM 的 tool，而是被 LLM 委托后自主运行的控制器。**

## 1. 命令流水线

```
玩家自然语言
  → [CommandProcessor] NLU/LLM + WorldModel → TaskSpec[]
  → [Kernel] spawn Task Agent（LLM 实例）→ 资源分配
  → [Task Agent] 大脑：选择 Expert、设参数、委托执行、监控事件
  → [Expert] 小脑：自主 tick 执行 → Action[] / Signal / Outcome
  → [ActionExecutor] 统一调 GameAPI
```

### CommandProcessor
合并了之前的 Interpreter/Resolver/Decomposer。内部自由组合 NLU 模板 + LLM + WorldModel 查询。
对外只有一个接口：`process(text, world) → TaskSpec[]`。
简单命令走模板，复杂命令走 LLM（LLM 有游戏状态上下文）。

### Task Agent（大脑）
每个 Task 对应一个小型 LLM agent 实例。职责：
- 快速下发第一步（尽快开始执行）
- 选择并配置 Expert（设参数、目标、约束）
- 注册关心的事件（unit_died, target_found, ...）
- 收到事件时介入决策（调整参数、切换策略、取消/重启 Expert）
- Expert 完成后判断是否成功、是否需要后续动作
- Token 开销不是问题：小型 code agent 极便宜，并行 5-10 个可接受

**Task Agent 不做的事：**
- 不逐帧操控单位（那是 Expert 的事）
- 不做实时微操（LLM 响应太慢）
- 不替代专家的领域能力（LLM 不会玩游戏）

### Expert（小脑）
被 Task Agent 委托后**自主运行**的领域控制器。
- 接收参数后自己 tick，不等 LLM
- 向 Task Agent 发信号/事件（发现敌人、受攻击、任务完成、资源耗尽）
- Task Agent 可以中途修改参数（如改变侦察方向、调整进攻目标）
- 内部用传统 AI：FSM/评分/势场/寻路/影响力图

## ⚠️ 待深度调研：大脑-小脑协作模式

当前设计的最大开放问题：**大脑（LLM）和小脑（Expert）如何有机协作？**

### 已知约束
1. LLM 响应慢（0.5-2s），不能做实时控制
2. LLM 不会玩游戏，不能信任它做战术判断
3. 如果 tool 依赖 LLM 主动调用，LLM 可能不调用 → 功能等于不存在
4. 需要**框架主动注入 context** 到 LLM agent，而不是依赖 LLM 主动查询
5. 专家需要完整交权（侦察、战斗），不是被 LLM 逐步指挥

### 需要调研的方向
- 机器人领域的大脑-小脑（cerebrum-cerebellum）架构
- LLM agent 框架中的 context injection 和 event-driven 模式
- 游戏 AI 中高层规划 + 低层执行的通信模式
- 如何设计 Expert → Agent 的信号/事件机制
- 如何设计 Agent → Expert 的参数调整接口
- Fire-and-forget task vs LLM-supervised task 的区分

## 2. 运行时

**单线程 GameLoop，默认 10Hz。** Expert 不拥有线程，由 GameLoop 按各自 tick_interval 调度。

每 tick：刷新 WorldModel → 检测事件 → tick 到期的 Expert → 收集 Action → 批量执行 → 推送看板。

| Expert 类型 | tick_interval | 理由 |
|---|---|---|
| CombatExpert | 0.2s | 微操快速响应 |
| ReconExpert | 1.0s | 侦察不需高频 |
| EconomyExpert | 5.0s | 生产决策慢 |
| DeployExpert | 即时 | instant task |

启动顺序：GameAPI → UnitRegistry → WorldModel → Resolver → Decomposer → Kernel(含Expert注册) → Interpreter → ActionExecutor → Dashboard → GameLoop

## 3. 数据模型

### Directive（Interpreter 输出）
| 字段 | 类型 | 说明 |
|---|---|---|
| directive_id | str | 唯一ID |
| kind | str | explore, attack, produce, defend, cancel... |
| target | str | **未解析自然文本**: "敌人基地", "左边那群坦克" |
| goal | str? | find, destroy, harass |
| modifiers | dict | {urgent: true, quantity: 5} |
| raw_text | str | 原始输入 |
| ambiguity | float | 0-1, >0.7 反问玩家 |
| timestamp | float | |

### ResolvedTarget（Resolver 输出）
| 字段 | 类型 | 说明 |
|---|---|---|
| owner | str | self / enemy / neutral |
| entity_type | str | base, army, unit, area, resource, map |
| actor_ids | list[int] | 匹配到的actor（可空=搜索目标）|
| position | tuple? | 已知位置 |
| known | bool | True=确认存在, False=搜索目标 |
| confidence | float | 匹配置信度 |
| candidates | list[dict] | 所有候选 |
| raw_text | str | |
| resolve_method | str | keyword / spatial / context / default |

### TaskSpec（Decomposer 输出）
| 字段 | 类型 | 说明 |
|---|---|---|
| task_id | str | |
| kind | str | instant / managed / background / constraint |
| intent | str | recon_find, attack_target, produce_unit... |
| target | ResolvedTarget? | |
| success_condition | SuccessCondition? | 可执行的成功判定 |
| failure_condition | FailureCondition? | 可执行的失败判定 |
| priority | int | 0-100, 用户命令=50, 紧急=80 |
| blocked_by | list[str] | 前置 task_id |
| directive_id | str | 溯源 |
| timeout_s | float? | |

### SuccessCondition / FailureCondition
| 字段 | 类型 | 说明 |
|---|---|---|
| type | str | target_found, target_destroyed, all_units_dead, timeout... |
| params | dict | 判定参数 |
| evaluator | str | world_query / expert_report |

带 `check(world, job) -> bool` 方法，Expert 每 tick 调用。

### ExecutionJob（Kernel 运行单元）
| 字段 | 类型 | 说明 |
|---|---|---|
| job_id | str | |
| task_id | str | |
| directive_id | str | 溯源 |
| status | JobStatus | pending/binding/running/waiting/succeeded/partial/failed/aborted/superseded |
| owner_expert_id | str | expert 实例 ID |
| expert_type | str | ReconExpert |
| intent | str | 从 TaskSpec 复制 |
| resources | list[str] | "actor:57", "queue:Infantry" |
| pending_requests | list[ResourceRequest] | |
| priority | int | |
| task_kind | str | |
| cancel_requested | bool | |
| failure_reason | str? | |
| created_at | float | |
| updated_at | float | |

### Constraint（活跃修饰器）
| 字段 | 类型 | 说明 |
|---|---|---|
| constraint_id | str | |
| kind | str | do_not_chase, economy_first, defend_base |
| scope | str | global / 特定job_id |
| params | dict | {max_chase_distance: 20} |
| enforcement | str | hard（违反=abort）/ soft（建议）|
| source_directive_id | str | |
| priority | int | 约束间优先级 |
| expires_at | float? | |
| active | bool | |
| created_at | float | |

### Outcome（任务终态）
| 字段 | 类型 | 说明 |
|---|---|---|
| job_id | str | |
| task_id | str | |
| directive_id | str | |
| result | str | succeeded/partial/failed/aborted/superseded（与JobStatus终态一致）|
| reason | str | enemy_base_found, scout_killed, user_cancel |
| data | dict | 结果数据 |
| resources_released | list[str] | |
| recoverable | bool | |
| followup_suggestions | list[str] | |
| timestamp | float | |

### Action（Expert → ActionExecutor）
| 字段 | 类型 | 说明 |
|---|---|---|
| action_id | str | |
| job_id | str | |
| resource_key | str | "actor:57" / "queue:Infantry" / "global" |
| command | str | move, attack_move, attack_target, produce, deploy, stop |
| target_pos | tuple? | |
| target_actor_id | int? | |
| params | dict | |
| priority | int | 同resource_key多action取最高 |
| expires_at | float? | |

### ActionResult
| 字段 | 类型 | 说明 |
|---|---|---|
| action_id | str | |
| success | bool | |
| error | str? | actor_dead, target_unreachable, api_timeout |
| resource_key | str | |
| command | str | |

### ResourceRequest（Expert → Kernel）
| 字段 | 类型 | 说明 |
|---|---|---|
| request_id | str | |
| job_id | str | |
| kind | str | actor / production_queue |
| count | int | |
| predicates | dict | {mobility: fast, category: vehicle} |
| mandatory | bool | 必须满足才能运行 |
| allow_wait | bool | 可排队等待 |
| allow_substitute | bool | 允许替代品 |
| allow_preempt | bool | 允许抢占低优先级 |
| wait_timeout_s | float | 等待超时 |

### CancelSelector
| 字段 | 类型 | 说明 |
|---|---|---|
| directive_id | str? | 按原始指令取消 |
| intent_match | str? | 正则匹配intent: "recon\|explore" |
| job_id | str? | 按具体Job取消 |

### Event（WorldModel 事件检测）
| 字段 | 类型 | 说明 |
|---|---|---|
| event_id | str | |
| type | str | UNIT_DIED, UNIT_DAMAGED, ENEMY_DISCOVERED, BASE_UNDER_ATTACK, STRUCTURE_LOST, PRODUCTION_COMPLETE |
| actor_id | int? | |
| position | tuple? | |
| data | dict | |
| timestamp | float | |

### NormalizedActor（WorldModel 中的标准化单位）
| 字段 | 类型 | 说明 |
|---|---|---|
| actor_id | int | |
| name | str | 2tnk, e1, harv |
| display_name | str | 重型坦克 |
| owner | str | self/enemy/neutral |
| category | str | infantry/vehicle/building/harvester/mcv |
| position | tuple | |
| hp / hp_max | int | |
| is_alive / is_idle | bool | |
| mobility | str | fast/medium/slow/static |
| combat_value | float | |
| can_attack / can_harvest | bool | |
| weapon_range | int | |
| last_seen | float | |

## 4. 核心组件职责

### Kernel（被动仲裁器，无循环）
- submit_directive → Resolver → Decomposer → 创建 Job
- on_event → 事件路由（单位死亡通知Expert，基地被攻击触发防御Task）
- on_outcome → 释放资源、更新状态、通知看板、解除阻塞的后续Task
- on_resource_request → 分配/抢占/排队
- cancel(CancelSelector) → 匹配Job → expert.abort() → on_outcome（标准路径）
- check_wait_queue → 满足等待请求 或 超时通知Expert

### Expert（每Job一个实例，由Kernel实例化并销毁）
- handled_intents() → 声明能处理的intent
- bind(task, world) → ResourceRequest[] 声明需要的资源
- start(task, world, assigned, resource_requester) → 开始执行
- tick(world) → Action[] 或 Outcome
- on_resource_lost(actor_id, world) → 资源被夺/死亡
- on_resource_granted(request_id, actor_ids) → 等待的资源到了
- on_resource_wait_expired(request_id) → 等待超时
- abort(reason) → Outcome（幂等）

### ActionExecutor
- Expert 永远不直接调 GameAPI
- 按 resource_key 分组去重，同 key 取最高优先级
- 统一调 GameAPI，返回 ActionResult

### WorldModel
- 游戏状态查询（actors/structures/economy/map）
- 空间查询（unexplored regions, threat near pos）
- 运行时状态（active jobs, resource bindings, constraints）
- 资源匹配（find_actors by predicates, idle_only）
- 事件检测（对比前后快照 → Event[]）
- 分层刷新（actor位置每tick, 经济500ms, 地图1s, 生产队列2s）
- version + last_refresh_at 用于新鲜度判断

## 5. 取消与抢占

**取消（用户说"取消探索"）：**
Directive(kind=cancel) → CancelSelector(intent_match="recon|explore") → Kernel.cancel() → expert.abort() → on_outcome()（标准路径）

**抢占（高优先级要低优先级的资源）：**
- 目标Job只有一个资源 → abort + on_outcome（终止）
- 目标Job有多个资源 → on_resource_lost（降级继续）

**Mid-task资源补充（侦察兵死后）：**
Expert 通过 resource_requester.request() 发起 → 同步返回 或 进入等待队列 → on_resource_granted / on_resource_wait_expired 回调

## 6. 看板 + 日志

**技术栈：** Vue 3
**双模式：** 用户面板 / 调试面板
**三区：** Operations（服务+画面）/ Tasks（任务看板）/ Diagnostics（日志+状态）

**WebSocket 入站：** command_submit, command_cancel, clarification_response, mode_switch
**WebSocket 出站：** world_snapshot(1Hz), task_update(变更时), task_list(1Hz), log_entry(实时), action_executed(调试)

**结构化日志字段：** event, ts, level, layer, event_type, task_id, job_id, expert, actor_ids, world_version, directive_id, message, data

## 7. 决策记录

| # | 决策 | 日期 |
|---|---|---|
| 1 | Kernel 无循环，被动仲裁 | 03-29 |
| 2 | 全面重写 | 03-29 |
| 3 | GameAPI 不改 | 03-29 |
| 4 | 对手 AI 不纳入 | 03-29 |
| 5 | 4种Task: Instant/Managed/Background/Constraint | 03-29 |
| 6 | 单线程GameLoop 10Hz, per-expert tick_interval | 03-30 |
| 7 | Expert不直接调GameAPI, 全走Action→ActionExecutor | 03-30 |
| 8 | Expert实例per-Job | 03-30 |
| 9 | 看板 Vue 3 | 03-29 |

## 8. 场景推演：探索地图，找到敌人基地

**Interpreter:** `Directive(kind=explore, target="敌人基地", goal=find, ambiguity=0.1)`

**Resolver:** "敌人"→owner=enemy, "基地"→entity_type=base, WorldModel查无已知 → `ResolvedTarget(known=False)`

**Decomposer:** 模式"explore to find X" → `TaskSpec(kind=managed, intent=recon_find, success=target_found{base,enemy})`

**Kernel:** 选ReconExpert → bind()请求1个fast actor → 分配actor:57 → start()

**执行：**
- t=0s: 查WorldModel未探索区域 → 对角方向评分最高 → move actor:57
- t=15s: 发现敌方矿车 → 调整方向跟踪矿车
- t=30s: 被攻击HP降 → 判断继续(距目标近) → attack_move
- t=42s: 发现3个敌方建筑 → success_condition满足 → Outcome(succeeded)
- Kernel释放actor:57, 通知玩家

**边缘：侦察兵t=20s死亡** → UNIT_DIED事件 → on_resource_lost → resource_requester请求补充(wait_timeout=30s) → 等待/超时失败

**边缘：用户"取消探索"** → CancelSelector(intent_match="recon|explore") → abort → Outcome(aborted)

**边缘：新命令抢占actor:57** → 高优先级Job → abort旧Job → 资源重分配

## 9. 现有代码处置

详见 `code_asset_inventory.md`。Keep: GameAPI, models, NLU管线。Reference: jobs, agents, intel, tactical_core。Delete: standalone launchers。
