# THE-Seed-OpenRA 系统完整报告

日期：2026-04-05 | 版本：D1-D4 落地后

---

## 1. Adjutant 详细设计

### 1.1 输入处理流程（路由优先级从高到低）

玩家输入经 `handle_player_input()` 按以下顺序逐层尝试，**首个命中即返回**：

| 优先级 | 路由层 | 入口方法 | 是否调用 LLM | 典型输入 |
|---|---|---|---|---|
| 1 | ACK 检测 | `_ACKNOWLEDGMENT_WORDS` frozenset | 否 | "ok"、"好"、"收到"、"知道了" |
| 2 | Deploy 反馈 | `_maybe_handle_deploy_feedback()` | 否 | "部署基地车"（但 MCV 不存在时） |
| 3 | Runtime NLU | `_try_runtime_nlu()` → `RuntimeNLURouter.route()` | 否 | "造3个步兵"、"建造电厂，兵营" |
| 4 | Rule-based | `_try_rule_match()` | 否 | "部署基地车"、"建造矿场"、"探索地图" |
| 5 | LLM 分类 | `_classify_input()` → `CLASSIFICATION_SYSTEM_PROMPT` | **是** | 模糊指令、上下文依赖的回复 |

LLM 分类后再按 `ClassificationResult.input_type` 二次路由：

| input_type | 处理方法 | 说明 |
|---|---|---|
| `cancel` | `_handle_cancel()` | 取消指定 task（"取消任务002"） |
| `reply` | `_handle_reply()` | 回答 pending question |
| `query` | `_handle_query()` | 查询战况/建议（再调一次 LLM + world_summary） |
| `command` | `_handle_command()` | 创建新 Task（交给 Kernel） |

### 1.2 ACK 检测

- **词表**：`_ACKNOWLEDGMENT_WORDS` frozenset，约 20+ 词（ok/好/收到/知道了/嗯/行/明白/了解/好吧/是的/对/懂了...）
- **逻辑**：`text.strip().lower().rstrip(".,！。") in _ACKNOWLEDGMENT_WORDS`
- **例外**：`kernel.list_pending_questions()` 非空时跳过 — ACK 可能是对问题的回复
- **返回**：`{type: "ack", ok: True, response_text: "收到"}`，0 LLM 调用，0 任务创建

### 1.3 NLU 快速通道

**入口**：`_try_runtime_nlu(text)` → `RuntimeNLURouter.route(text)`

**前置拦截**：
- `_QUESTION_RE` 正则：匹配 `为什么|怎么|怎样|吗$|呢$|什么时候|如何|why|how` → 返回 None，不进 NLU

**NLU 内部流程**：
1. `PortableIntentModel.predict_one(text)` → `(intent, confidence)`
2. `CommandRouter.route(text)` → `RouteResult(intent, entities, score)`
3. 安全检查：`route_intent ∈ safe_intents`（config yaml 配置）
4. 置信度门槛：`min_confidence_by_intent[intent]`（默认 0.75），router score ≥ 0.8
5. 若 `route_intent == "composite_sequence"` → 走 `_route_sequence()` 特殊路径

**支持的 direct intent**：

| intent | expert_type | 说明 |
|---|---|---|
| `deploy_mcv` | DeployExpert | 部署基地车 |
| `produce` | EconomyExpert | 生产单位/建造建筑 |
| `explore` | ReconExpert | 侦察/探索 |
| `mine` | `__MINE__`（直接执行） | 采矿车恢复采矿 |
| `stop_attack` | `__STOP_ATTACK__`（直接执行） | 停止攻击 |
| `query_actor` | `__QUERY_ACTOR__`（直接查询） | 查看己方/敌方单位 |
| `composite_sequence` | 多步顺序执行 | "建造电厂，兵营，步兵" |

**置信度阈值**（`nlu_pipeline/configs/runtime_gateway.yaml`）：
- 默认 `min_confidence`: 0.75
- composite 专用 `min_confidence`: 0.9
- router score 门槛: 0.8（composite: 0.9）

### 1.4 Rule-based 路径

`_try_rule_match()` 按顺序尝试四种匹配器：

1. **`_match_deploy()`** — 含"基地车" + deploy 关键词 → `DeployExpert(actor_id, target_position)`
2. **`_match_build()`** — startswith("建造/修建/造") → `EconomyExpert(unit_type, queue_type=Building)`
3. **`_match_production()`** — 含"生产/造/训练/补" → `EconomyExpert(unit_type, count, queue_type)`
4. **`_match_recon()`** — 含"探索地图/找敌人/找基地" → `ReconExpert`

**跳过条件**：
- 含查询词（？/如何/怎么/战况/建议/多少...）→ `_looks_like_query()` 返回 True
- 含连词（然后/之后/并且/同时/别/不要/如果/优先）→ `_looks_like_complex_command()` 返回 True

### 1.5 LLM 分类路径

**触发**：以上所有快速路径都未命中时进入。

**`CLASSIFICATION_SYSTEM_PROMPT` 注入的上下文**：
```json
{
  "active_tasks": [...],           // 当前运行任务列表
  "pending_questions": [...],      // 未回答的 DECISION_REQUEST 问题
  "recent_dialogue": [...],        // 最近 10 条对话 (D4: 从 5 扩到 10)
  "recent_completed_tasks": [...], // 最近 5 条任务完成结果
  "player_input": "..."           // 当前玩家输入
}
```

**分类提示词要点**：
- reply / command / query / cancel 四选一
- 对话上下文感知：检查 `recent_completed_tasks` 判断模糊输入意图
- 失败任务后的跟进输入倾向分类为 command
- 超时 10s 后 fallback 到 `_rule_based_classify()`

### 1.6 composite_sequence 顺序执行（D1）

**旧行为**：所有步骤同时创建任务并行执行。

**新行为**（D1 改造后）：
1. NLU 识别 `composite_sequence`（如"建造电厂，兵营，步兵"→ 3 步）
2. `_handle_runtime_nlu()` 只创建**第 1 步**任务
3. 剩余步骤存入 `_pending_sequence: list[DirectNLUStep]`
4. 记录 `_sequence_task_id` = 当前步骤的 task_id

**推进机制**：
- `notify_task_completed(task_id=...)` 检测 `_sequence_task_id` 匹配
- 调用 `_advance_sequence(result)`：
  - `result ∈ {succeeded, partial}` → pop 下一步，`_resolve_runtime_nlu_step()` + `_start_direct_job()` → 更新 `_sequence_task_id`
  - `result ∈ {failed, aborted}` → 取消剩余步骤，写 dialogue "序列步骤失败，已取消剩余 N 步"

**状态清理**：`clear_dialogue_history()` 同时清空 `_pending_sequence` 和 `_sequence_task_id`。

### 1.7 对话历史管理

| 数据 | 容器 | 上限 | 写入时机 |
|---|---|---|---|
| 玩家/adjutant/system 发言 | `_dialogue_history` | 20 条（写入时裁剪） | 每次 `handle_player_input` 结束 |
| 任务完成结果 | `_recent_completed` | 5 条 | `notify_task_completed()` |
| TASK_WARNING / TASK_INFO | `_dialogue_history`（speaker=system） | 同上 | `notify_task_message()` (D4) |
| 序列推进状态 | `_dialogue_history`（speaker=system） | 同上 | `_advance_sequence()` (D1) |

**不记录**：LLM 原始响应、NLU decision log、bootstrap 内部事件、tool 调用详情。

---

## 2. 三级架构运行流程

### 2.1 整体架构

```
┌──────────┐     text      ┌──────────┐    create_task    ┌────────┐   start_job   ┌──────────┐
│  Player  │ ──────────── ▶│ Adjutant │ ────────────────▶ │ Kernel │ ─────────────▶│  Expert  │
│ (前端WS) │               │  (路由层) │                   │(编排层) │               │ (Job AI) │
└──────────┘               └──────────┘                   └────────┘               └──────────┘
                                                               │                        │
                                                               │   TaskAgent (LLM)      │
                                                               │   ┌──────────────┐     │
                                                               └──▶│ wake→context  │◀────┘
                                                                   │ →LLM→tools    │  signals
                                                                   │ →sleep        │
                                                                   └──────────────┘
```

### 2.2 Task 生命周期

```
Kernel.create_task(raw_text, kind, priority, info_subscriptions)
  │
  ├─ 生成 task_id (UUID[:8]) + label (序号 "001", "002")
  ├─ 创建 Task 对象 (status=RUNNING)
  ├─ 创建 ToolExecutor + TaskToolHandlers
  ├─ task_agent_factory() → TaskAgent 实例
  ├─ wire runtime_facts_provider + active_tasks_provider
  ├─ _sync_world_runtime() → WorldModel 同步
  └─ _maybe_start_agent() → asyncio.create_task(agent.run())
```

**终态**：`SUCCEEDED | FAILED | PARTIAL | ABORTED`

- `complete_task(result)` — TaskAgent 主动结束（通过 LLM tool call 或 bootstrap 自动闭环）
- `cancel_task()` — 玩家取消（Adjutant 路由 cancel 指令）

### 2.3 Job 生命周期

```
Kernel.start_job(task_id, expert_type, config)
  │
  ├─ validate_job_config(expert_type, config)
  ├─ _make_job_controller() → Expert.create_job(job_id, config, signal_callback)
  ├─ _rebalance_resources() → 资源分配
  └─ Job 在 Expert 的 tick 循环中运行
        │
        ├─ signal_callback(ExpertSignal) → Kernel → TaskAgent.push_signal()
        │     类型: PROGRESS, TASK_COMPLETE, DECISION_REQUEST, WARNING, etc.
        │
        └─ 终态: SUCCEEDED | FAILED | ABORTED
```

**Expert → Kernel → TaskAgent signal 流转**：
```
Expert.tick()
  → signal_callback(ExpertSignal{kind, summary, data, ...})
    → Kernel._on_signal()
      → TaskAgent.push_signal(signal)
        → AgentQueue.push()
          → (next wake) queue.drain() → recent_signals
```

### 2.4 TaskAgent 运行循环

```python
async def run():
    _safe_wake_cycle(trigger="init")           # 首次 wake
    while running and not task_completed:
        woken = await queue.wait_for_wake(timeout=10s)
        _safe_wake_cycle(trigger="event_or_review" | "timer")
```

**`_wake_cycle()` 详细流程**：

```
1. drain() → signals[], events[]

2. _maybe_finalize_bootstrap_task()      ← 如果 bootstrap job 已终止，直接 complete_task 并 return
   │  查 _jobs_provider → bootstrap_job.status
   │  SUCCEEDED → complete_task("succeeded")
   │  FAILED/ABORTED → complete_task("failed")

3. _maybe_attach_existing_rule_job()     ← 单 intent 命令 + 已有1个匹配 job → 挂为 bootstrap（monitor only）
4. _maybe_bootstrap_structure_build()    ← "建造矿场/电厂/兵营" → 直接创建 EconomyExpert job
5. _maybe_bootstrap_simple_production()  ← "生产3个步兵" → 直接创建 EconomyExpert job

6. Smart Wake 检查:
   │  if (无 signals) and (无 events) and (有 jobs) and (job snapshot 未变):
   │      → skip LLM, return
   │  else:
   │      → 记录 new snapshot, 继续

7. build_context_packet():
   │  task, jobs, world_summary, runtime_facts, other_active_tasks,
   │  recent_signals, recent_events, open_decisions

8. Multi-turn LLM loop (max_turns=10):
   │  for each turn:
   │    response = LLM.chat(messages, tools=TOOL_DEFINITIONS)
   │    if tool_calls:
   │      execute all tools → append results → inject fresh context → continue
   │      if complete_task called → break
   │    else (text only):
   │      append text → break
```

### 2.5 Bootstrap 机制（BUG-A 修复后）

**核心原则**：常见中文指令（"建造矿场"、"生产步兵"）不需要 LLM 推理，直接 bootstrap 正确的 Expert job，然后 monitor-only 等待终止。

**三种 bootstrap**：

| bootstrap | 触发条件 | 创建的 job |
|---|---|---|
| `_maybe_bootstrap_structure_build` | 文本 startswith "建造/修建/造" + 矿场/电厂/兵营 | EconomyExpert(powr/proc/barr, Building) |
| `_maybe_bootstrap_simple_production` | 含 "生产/造/训练/补" + 步兵/火箭兵/工程师 | EconomyExpert(e1/e3/e6, Infantry) |
| `_maybe_attach_existing_rule_job` | 单 intent + 已有 1 个匹配 job（由 Adjutant 规则路由创建） | 不创建，挂载已有 job |

**自动闭环（BUG-A 修复要点）**：
- `_maybe_finalize_bootstrap_task()` 每次 wake 都执行
- 不依赖 signal（signal 可能被 LLM wake 消费后不再出现）
- 直接查 `_jobs_provider(task_id)` 获取 bootstrap job status
- `SUCCEEDED/FAILED/ABORTED` → 调用 `complete_task` 关闭，不经过 LLM

### 2.6 Expert-as-tool

TaskAgent LLM 可调用的 5 个 Expert tool：

| tool name | Expert | Config 类型 | 关键参数 |
|---|---|---|---|
| `deploy_mcv` | DeployExpert | DeployJobConfig | actor_id, target_position? |
| `scout_map` | ReconExpert | ReconJobConfig | search_region, target_type, retreat_hp_pct, avoid_combat |
| `produce_units` | EconomyExpert | EconomyJobConfig | unit_type, count, queue_type, repeat |
| `move_units` | MovementExpert | MoveJobConfig | target_position, actor_ids, mode |
| `attack` | CombatExpert | CombatJobConfig | mode(assault/harass/hold/surround), target_position, actor_ids |

其他 tool（非 Expert）：

| tool name | 说明 |
|---|---|
| `query_world` | 查询 WorldModel（my_actors, enemy_actors, map_info...） |
| `patch_job` | 修改运行中 job 的参数 |
| `abort_job` | 终止 job |
| `complete_task` | 结束 task (succeeded/failed/partial) |
| `send_task_message` | 发送消息给玩家 (info/warning/question/complete_report) |
| `create_constraint` | 创建行为约束 |
| `query_planner` | 生产规划查询 |

### 2.7 信息注入链路

```
WorldModel.compute_runtime_facts(task_id)
  ├─ 遍历 actors → 计数建筑/单位/MCV/harvester
  ├─ 计算 tech_level, can_afford_*, feasibility (D3)
  ├─ 合并 info_experts (BaseStateExpert, ThreatAssessor)
  └─ 返回 facts dict

build_context_packet()
  ├─ 接收 runtime_facts
  ├─ 按 task.info_subscriptions 过滤 info_experts keys
  │     "threat" → threat_level, enemy_count, threat_direction, base_under_attack, enemy_composition_summary
  │     "base_state" → base_established, base_health_summary, has_production
  │     "production" → (placeholder, 无数据)
  └─ 返回 ContextPacket

context_to_message()
  └─ JSON 序列化为 "[CONTEXT UPDATE]\n{context_packet: {...}}"
```

**info_subscriptions 映射**（`_EXPERT_SUBSCRIPTIONS`，在 Adjutant 创建 task 时注入）：

| Expert | 订阅 |
|---|---|
| CombatExpert | `["threat"]` |
| ReconExpert | `["threat"]` |
| MovementExpert | `["threat"]` |
| EconomyExpert | `["base_state", "production"]` |
| DeployExpert | `["base_state"]` |

---

## 3. 信息一览表

### 3.1 Task Agent 每轮 wake 可见信息（ContextPacket 完整字段）

```yaml
context_packet:
  task:
    task_id: str             # "t_a1b2c3d4"
    raw_text: str            # 玩家原始指令
    kind: str                # "managed"
    priority: int            # 默认 50
    status: str              # "running"
    created_at: float        # Unix epoch
    timestamp: float

  jobs[]:                    # 本 task 的所有 job
    - job_id: str
      expert_type: str       # "EconomyExpert" / "CombatExpert" / ...
      status: str            # "running" / "succeeded" / "failed" / "aborted" / "waiting"
      status_zh: str         # "运行中" / "已成功完成" / ...
      config: dict           # job config 详情
      resources: list[str]
      timestamp: float

  world_summary:
    economy: {total_credits, resources, power, power_drained, power_provided}
    military: {infantry_count, vehicle_count, ...}
    map: {fog_percentage, explored_percentage, ...}
    known_enemy: {count, buildings, units, ...}
    timestamp: float

  runtime_facts:
    # 基地状态
    has_construction_yard: bool
    power_plant_count: int         # D2: 建筑实例计数
    barracks_count: int
    refinery_count: int
    war_factory_count: int
    radar_count: int
    tech_level: int                # 0=无基地, 1=yard only, 2=有生产, 3=有雷达

    # 单位
    mcv_count: int
    mcv_idle: bool
    harvester_count: int
    combat_unit_count: int         # D3: INFANTRY + VEHICLE

    # 资源
    can_afford_power_plant: bool   # credits >= 300
    can_afford_barracks: bool      # credits >= 300
    can_afford_refinery: bool      # credits >= 2000

    # 任务/Job 统计
    active_task_count: int
    this_task_jobs: list[dict]     # 本 task 的 job 快照
    failed_job_count: int
    same_expert_retry_count: int

    # D3: 可行性预判
    feasibility:
      deploy_mcv: bool             # mcv_count > 0
      scout_map: bool              # combat_unit_count > 0
      produce_units: bool          # 有生产设施 + 资金 >= 300
      attack: bool                 # combat_unit_count > 0
      move_units: bool             # 任何可移动单位 > 0

    # 订阅过滤后的 info_experts
    info_experts:
      # "threat" 订阅:
      threat_level: str            # "low" / "medium" / "high"
      threat_direction: str|null   # 方位
      enemy_count: int
      enemy_composition_summary: str
      base_under_attack: bool
      # "base_state" 订阅:
      base_established: bool       # CY + power + refinery
      base_health_summary: str     # "critical" / "degraded" / "developing" / "economy-only" / "established"
      has_production: bool         # barracks 或 war_factory

  other_active_tasks[]:            # 其他并行运行的任务
    - label: str                   # "001", "002"
      raw_text: str
      status: str

  recent_signals[]:                # 上轮 wake 以来的 Expert 信号
    - task_id: str
      job_id: str
      kind: str                    # "progress" / "task_complete" / "warning" / ...
      summary: str
      world_delta: dict?
      expert_state: dict?
      result: str?
      data: dict?
      timestamp: float

  recent_events[]:                 # WorldModel 事件
    - type: str                    # "base_under_attack" / "unit_lost" / ...
      timestamp: float
      actor_id: int?
      position: [x, y]?
      data: dict?

  open_decisions[]:                # 待 LLM 回答的 DECISION_REQUEST
    - task_id: str
      job_id: str
      kind: "decision_request"
      summary: str
      decision: {question, options, default_if_timeout, deadline}
      timestamp: float

  timestamp: float                 # packet 生成时间
```

### 3.2 Adjutant 分类时可见信息

```yaml
# 传入 CLASSIFICATION_SYSTEM_PROMPT 的 JSON
active_tasks[]:
  - label: str                     # "001"
    raw_text: str
    status: str
    kind: str
    priority: int

pending_questions[]:
  - message_id: str
    task_id: str
    content: str                   # 问题文本
    options: list[str]
    priority: int

recent_dialogue[-10:]:             # D4: 从 5 条扩到 10 条
  - from: str                      # "player" / "adjutant" / "system"
    content: str
    timestamp: float

recent_completed_tasks[-5:]:
  - label: str
    raw_text: str
    result: str                    # "succeeded" / "failed" / "partial"
    summary: str

player_input: str                  # 当前输入
```

### 3.3 前端 WebSocket 消息类型

**Server → Frontend（出方向）**：

| 消息类型 | 方法 | 内容 |
|---|---|---|
| `world_snapshot` | `send_world_snapshot()` | actors, economy, map, queues 全量快照 |
| `task_update` | `send_task_update()` | 单个 task 状态变化 |
| `task_list` | `send_task_list()` | 所有 task 完整列表 |
| `task_message` | `send_task_message()` | info/warning/question/complete_report |
| `log_entry` | `send_log_entry()` | 系统日志（INFO/WARN/ERROR） |
| `player_notification` | `send_player_notification()` | 玩家通知（base attack 等） |
| `query_response` | `send_query_response()` | Adjutant 对查询的回复文本 |
| `benchmark` | `send_benchmark()` | 性能数据（span 计时） |
| `session_cleared` | `send_session_cleared()` | 会话清理通知 |

**Frontend → Server（入方向）**：

| 消息类型 | 说明 |
|---|---|
| `player_input` | 玩家文本输入 → Adjutant.handle_player_input() |
| `player_response` | 对 question 的回答 → Kernel.submit_player_response() |

---

## 4. 当前已知限制 / 未解决问题

### 4.1 测试

| 问题 | 文件 | 状态 |
|---|---|---|
| `test_consecutive_failures_auto_terminate` 断言失败 | test_task_agent.py | pre-existing，测试逻辑与实现不对应 |
| `test_bootstrap_simple_production_completes_with_llm_running` 断言失败 | test_task_agent.py | pre-existing |
| `test_tool_handlers.py` 全部 TypeError | test_tool_handlers.py | pre-existing，`TaskToolHandlers.__init__` 签名已变 |

### 4.2 架构限制

| 限制 | 说明 |
|---|---|
| **composite_sequence 特殊步骤不可序列化** | `__QUERY_ACTOR__` / `__MINE__` / `__STOP_ATTACK__` 在序列中遇到时直接返回，不参与序列推进 |
| **序列状态不持久** | `_pending_sequence` / `_sequence_task_id` 在进程重启后丢失 |
| **info_subscriptions "production" 无数据** | `_SUBSCRIPTION_KEYS["production"]` = `frozenset()`，无对应 InfoExpert 实现 |
| **bootstrap 覆盖范围有限** | 结构建筑仅覆盖 powr/proc/barr；步兵仅覆盖 e1/e3/e6；坦克/车辆/高级建筑不走 bootstrap |
| **NLU 与规则路由可能冲突** | NLU 优先于规则执行，但两者可能对同一输入都命中；NLU 关闭时（enabled=false）退化到规则 |

### 4.3 功能缺失

| 缺失项 | 影响 |
|---|---|
| **Task 优先级抢占** | 多 task 并行时无优先级调度，先到先服务 |
| **Job 资源竞争可视化** | 玩家看不到 resource rebalance 过程 |
| **LLM 分类 fallback 延迟** | 10s 超时后才 fallback 到规则分类，高延迟场景体验差 |
| **WorldModel 刷新失败静默** | `refresh_health().stale` 只在 deploy 路径检查，其他路径不感知 |
| **前端无序列进度展示** | composite_sequence 的"第 N 步/共 M 步"仅在 Adjutant response_text 中，无专门 UI |
