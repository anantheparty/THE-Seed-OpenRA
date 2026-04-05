# Target Architecture — THE-Seed OpenRA Intelligent Adjutant

**作者**: Wang | **日期**: 2026-04-06 | **状态**: 远期目标愿景（非当前施工蓝图）

> **重要说明**：本文档是远期架构愿景，不是当前执行计划。
> 当前执行主线见 Yu 纠偏报告 `docs/yu/wang_design_v_next_correction_20260406.md`：
> - Adjutant = 前门 + top-level coordinator（已承担 Commander 职责，不另建 Commander）
> - EconomyCapability 做实（当前唯一 Capability 主线）
> - TaskAgent 降级为复杂 managed task 的局部推理器
> - Information Plane 继续做厚
>
> 本文档中的 Commander 独立层、多 Capability 家族等属于远期目标，不作为近期实施依据。

---

## 0. 本文档的定位

这不是一份从头开始的设计，而是对当前系统演化方向的远期愿景记录。

当前系统处于混合过渡态——正确的器官已经在长，但旧的"task = 独立 LLM 脑"假设还没有完全消解。本文档记录的是：

1. 远期目标架构方向
2. 哪些是目标形态、哪些是过渡态、哪些已废弃
3. 可能的收敛路径（实际执行节奏以 Yu 纠偏报告为准）

**产品定位**：一个可长期维护、可解释、在 OpenRA Red Alert 中能稳定"理解指令 → 执行动作 → 结构化反馈"的智能副官系统。不是全自动竞技 AI，而是强人类副官 / 半自动指挥系统。

---

## 1. 五层架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Layer 1: Front Door                       │
│         Adjutant + RuntimeNLU + Rule Routing                │
│    （玩家唯一入口，语言理解 + 路由，不做深规划）                │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                    Layer 2: Commander                        │
│              （唯一高层战略脑，单一 LLM）                      │
│    全局战略意图 / 阶段判断 / task 创建与处置 /                │
│    capability 调用 / 优先级仲裁                               │
└──────┬──────────────┬──────────────┬────────────────────────┘
       │              │              │
┌──────▼──────┐┌──────▼──────┐┌──────▼──────┐
│  Economy    ││   Recon     ││  Combat     │  Layer 3:
│ Capability  ││ Capability  ││ Capability  │  Capability Managers
│             ││             ││             │  （持久领域控制器）
└──────┬──────┘└──────┬──────┘└──────┬──────┘
       │              │              │
┌──────▼──────────────▼──────────────▼────────────────────────┐
│                  Layer 4: Execution                          │
│    DeployExpert / EconomyExpert / ReconExpert / CombatExpert │
│    MovementExpert — 每个 Expert 实例化为 Job，自主 tick 执行   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                  Layer 5: Information Plane                  │
│  WorldModel + RuntimeFacts + InfoExperts + Knowledge + Logs  │
│  （"让系统知道自己在什么状态"，所有上层共享消费）              │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Layer 1 — Adjutant / Front Door

### 2.1 职责

| 功能 | 描述 |
|------|------|
| 语言入口 | 接收所有玩家文本/语音输入 |
| 分类 | ACK / query / cancel / command / reply |
| NLU 前置 | RuntimeNLURouter 处理 shorthand、安全复合序列、query_actor |
| Rule 路由 | 简单安全命令直接执行（deploy_mcv、produce、explore 等） |
| LLM 分类 | 只有 NLU/rule 都不命中时，才调 LLM 做 disposition 判断 |
| 输出统一 | 所有 task→玩家消息经 Adjutant 格式化后呈现 |

### 2.2 不做什么

- 不做战术/战略决策
- 不持有 task 内部状态
- 不直接生产或调度单位

### 2.3 路由优先级

```
玩家输入
  → RuntimeNLU 命中？ → 直接执行 + notify
  → Rule 命中？ → 直接执行 + notify
  → LLM 分类 → disposition:
      new → Commander
      merge → 对应 Capability
      override → Commander
      reply → 对应 Task
      query → Adjutant 自身 LLM 回答
```

### 2.4 当前状态 / 目标状态

| 维度 | 当前 | 目标 |
|------|------|------|
| RuntimeNLU | **已实现** | 保留并继续扩展覆盖 |
| Rule 路由 | **已实现** | 保留 |
| LLM classification | **已实现** | 保留，但 disposition 结果送 Commander 而非直接创建 task |
| 经济指令 merge | **已实现** | 保留 → EconomyCapability |
| 查询回答 | **已实现** | 保留 |

---

## 3. Layer 2 — Commander

### 3.1 核心原则

**全局只有一个战略决策脑。**

Commander 是唯一有权做战略判断的 LLM。它不做高频操作，只做战略级决策。

### 3.2 职责

| 功能 | 描述 |
|------|------|
| 战略意图 | 维护当前整体战略（经济优先/进攻/防守/科技爬升） |
| 阶段判断 | 当前处于哪个阶段（开局/发展/中期/后期） |
| Task 处置 | 收到 Adjutant 转来的 command → 决定 new/merge/override/interrupt |
| Capability 调用 | 指示 Capability 做特定事项（"全力爆兵"→EconomyCapability） |
| 优先级仲裁 | 多个 task/capability 争资源时裁决 |
| 主动规划 | 根据 Information Plane 主动发起行动（侦察、扩张、进攻） |

### 3.3 不做什么

- 不直接操作单位
- 不直接调 GameAPI
- 不做微操级决策
- 不做高频 tick（秒级唤醒，不是 tick 级）

### 3.4 输入

Commander 的决策依赖 Information Plane 提供的编译后语义，不从 raw state 推断：

```
[strategic_summary]
阶段: 中期 | 战略: 坦克推进
经济: 资金 2340 / 电力 +50 / 矿车 2

[capability_status]
Economy: 运行中, 坦克生产×5 + 电厂×1
Recon: 运行中, 2个侦察兵在东部
Combat: 空闲

[active_tasks]
#001 "进攻东部" — running, 等待坦克 (3/5)
#003 "防守基地" — running, 步兵就位

[unfulfilled_needs]
Task#004 需要飞机×2, 无机场

[player_command]  ← 来自 Adjutant
"集中力量进攻"

[alerts]
敌军在西南扩张 (ThreatAssessor, 30s ago)
```

### 3.5 Tool Set

```python
COMMANDER_TOOLS = [
    "create_task",           # 创建新 task（指定 capability 处理或需要 sub-agent）
    "update_task_priority",  # 调整 task 优先级
    "cancel_task",           # 取消 task
    "instruct_capability",   # 向 Capability 下达指令
    "query_world",           # 查询 Information Plane
    "send_player_message",   # 通过 Adjutant 给玩家发消息
]
```

### 3.6 唤醒策略

| 触发 | 机制 |
|------|------|
| Adjutant 转来玩家命令 | 立即唤醒 |
| 重要世界事件（基地被攻击、敌军扩张） | 事件推送唤醒 |
| Capability 上报需要战略决策 | 事件推送唤醒 |
| 定期心跳 | 15-30s（有活跃 task 时） |
| 无事可做 | sleep |

### 3.7 当前状态 / 目标状态

Commander 目前**不存在**。当前由多个 TaskAgent 各自做战略判断。

收敛路径：

1. **Phase A**（当前）：EconomyCapability 先落地，经济域从 TaskAgent 中剥离
2. **Phase B**：引入 Commander，Adjutant 的 disposition 结果交给 Commander 而非 Kernel 直接创建 task
3. **Phase C**：普通 TaskAgent 降级为 Commander 按需创建的临时 sub-agent，默认不再每 task 配 LLM

---

## 4. Layer 3 — Capability Managers

### 4.1 核心原则

Capability Manager 是持久的领域控制器。它不是一个 task，不会结束，不会被 cancel。

### 4.2 EconomyCapability（首个实现）

详细设计见 `capability_task_design.md`。核心要点：

**三层职责分离：**

| 角色 | 职责 |
|------|------|
| EconomyCapability | 全局经济规划 + 主动基建 + 响应玩家经济指令 |
| Kernel | 接收 request → idle 匹配 → fast-path 生产 → 按优先级分配 |
| 普通 Agent | `request_units` → 阻塞等待 → 拿到单位继续工作 |

**Capability 只决定"造什么"，Kernel 处理"分给谁"。**

核心接口：
- `produce_units` — 生产单位/建筑
- `query_world` — 查询状态
- `query_planner` — 生产建议
- 消费 `[unfulfilled_requests]` + `[active_production]` + `[player_messages]`

唤醒策略：
- Kernel fast-path 失败 → 有 unfulfilled request → 唤醒
- 玩家经济指令 merge → 唤醒
- 定期心跳（有 unfulfilled requests 时 5s）
- 无事可做 → sleep

### 4.3 未来 Capability（规划，尚未实现）

| Capability | 职责 | 优先级 |
|------------|------|--------|
| **ReconCapability** | 持续态势感知、侦察调度、视野管理 | Phase B |
| **CombatCapability** | 进攻/防守协调、兵力调配、交战规则 | Phase B |
| **BaseCapability** | 基地布局、修复、扩张管理 | Phase C |

每个 Capability：
- 持久存在，不因单个 task 结束而消失
- 对 Commander 暴露领域状态摘要
- 内部管理自己的 Expert/Job
- 吸收同领域需求，避免重复

### 4.4 当前状态 / 目标状态

| Capability | 当前状态 | 目标 |
|------------|---------|------|
| EconomyCapability | **Phase 1 实现中**（Xi） | 完整三层分离 |
| ReconCapability | 不存在 | Phase B |
| CombatCapability | 不存在 | Phase B |
| BaseCapability | 不存在 | Phase C |

---

## 5. Layer 4 — Execution Experts / Jobs

### 5.1 核心原则

Expert 是能力类型，Job 是 Expert 的运行时实例。Job 自主 tick 执行，直接调 GameAPI，不等 LLM。

### 5.2 Expert 三种类型

**Execution Expert**：绑资源，自主 tick 执行。
- DeployExpert / DeployJob
- EconomyExpert / EconomyJob
- ReconExpert / ReconJob
- CombatExpert / CombatJob
- MovementExpert / MovementJob

**Information Expert**：只读，持续分析 WorldModel，输出派生语义。
- BaseStateExpert
- ThreatAssessor
- 未来：QueueStateExpert / AwarenessExpert / TechGateExpert

**Planner Expert**：给出候选方案，不绑资源，不直接执行。
- ProductionAdvisor
- 未来：AttackRoutePlanner / ReconRoutePlanner

### 5.3 Expert 暴露为独立 Tool

每个 Execution Expert 作为 Agent 的独立 tool 调用，而非通过 `start_job` 大杂烩。

```python
# 目标：每个 Expert 是一个具名 tool
EXPERT_TOOLS = [
    "deploy",           # DeployExpert
    "produce_units",    # EconomyExpert
    "send_recon",       # ReconExpert
    "attack",           # CombatExpert
    "move_units",       # MovementExpert
]
```

### 5.4 Job → 上层通信

Job 通过 ExpertSignal 向上层报告：

| Signal 类型 | 用途 |
|-------------|------|
| progress | 进度更新 |
| risk_alert | 风险告警 |
| blocked | 无法继续 |
| decision_request | 需要上层裁决 |
| resource_lost | 资源丢失 |
| target_found | 发现目标 |
| task_complete | 完成 |

### 5.5 当前状态 / 目标状态

| Expert | 当前状态 | 目标 |
|--------|---------|------|
| DeployExpert | **已实现** | 强化验证条件 |
| EconomyExpert | **已实现** | 检查 produce() 返回值、abort 清队列 |
| ReconExpert | **已实现** | 资源需求排除 harvester/MCV |
| CombatExpert | **已实现** | 继续硬化 |
| MovementExpert | **已实现** | 继续硬化 |
| BaseStateExpert | **已实现** | 扩展覆盖 |
| ThreatAssessor | **已实现** | 扩展覆盖 |
| ProductionAdvisor | **已实现** | 继续完善 |

---

## 6. Layer 5 — Information Plane

### 6.1 核心原则

**不要让 LLM 从 raw state 推断 doctrine。系统先把世界编译成可决策语义，再喂给上层。**

这是近年 LLM+RTS 研究的共同结论，也是本系统最值得继续投入的方向。

### 6.2 组成部分

**WorldModel**：
- 游戏状态快照（actors / structures / economy / map）
- 分层刷新（actor 位置每 tick，经济 ~2s，地图 ~1s）
- 事件检测（快照 diff → Event[]）
- stale 检测与保护

**Runtime Facts**：
- `compute_runtime_facts()` 输出结构化决策事实
- 包括：建筑计数、科技等级、可建造列表、可行性评估、敌情摘要
- 可建造列表改用 C# `query_producible_items` API 获取真实数据，`_derive_buildable_units` 降为 fallback

**Information Experts**：
- BaseStateExpert — 基地状态评估
- ThreatAssessor — 威胁评估
- 未来扩展：QueueStateExpert / AwarenessExpert / TechGateExpert / RecoveryAdvisor

**Knowledge Base**：
- `experts/knowledge.py` — 结构化 RTS 知识（单位角色、counter 关系、建造依赖）
- 方向：Expert 自己输出知识性结论（roles / impacts / recovery_package / downstream_unlocks）

**Logs / Trace / Evaluation**：
- Session log + per-task log + Task Trace + Diagnostics replay
- 已形成可复盘能力

### 6.3 query_producible_items API

C# 侧已新增 `QueryProducibleItemsCommand`，基于 `ProductionQueue.BuildableItems()` 返回真实可建造列表（考虑科技树、前置条件、阵营限制）。

Python 侧：`GameAPI.query_producible_items()` → `WorldModel._producible_items_cache` → `runtime_facts.buildable`。

### 6.4 当前状态 / 目标状态

| 组件 | 当前状态 | 目标 |
|------|---------|------|
| WorldModel 快照 | **已实现** | 分层刷新保持 |
| Runtime Facts | **已实现** | 继续扩展（phase / failure signature / success guard） |
| query_producible_items | **已实现** | 替代 _derive_buildable_units |
| BaseStateExpert | **已实现** | 扩展 |
| ThreatAssessor | **已实现** | 扩展 |
| Knowledge base | **已实现（初级）** | 继续核实和扩展 |
| Session/Task logging | **已实现** | 完善 task_id 路由 |
| Stale world 保护 | **部分实现** | 统一总闸 |

---

## 7. Kernel — 确定性调度核心

### 7.1 职责

Kernel 是无 LLM 的确定性调度层。它不做战略判断，只做资源管理和执行调度。

| 功能 | 描述 |
|------|------|
| 资源分配 | 按 ResourceNeed + 优先级持续满足 |
| UnitRequest 处理 | idle 匹配 → fast-path 生产 → 自动分配 |
| 冲突仲裁 | 多 task 争资源 → 按优先级 |
| 事件路由 | WorldModel Event → 相关 Task/Job/Capability |
| Task 生命周期 | 创建/暂停/取消/清理 |
| Agent 阻塞/唤醒 | request waiting → suspend / fulfilled → resume |

### 7.2 UnitRequest 三步处理

```
Step 1: idle 匹配
  → 有足够 idle 单位 → bind → 返回 fulfilled

Step 2: fast-path bootstrap
  → idle 不够但可造 → 启动 EconomyJob → notify Capability
  → 返回 waiting → agent 暂停

Step 3: 自动分配
  → 单位出厂 → 按优先级匹配 pending requests → bind → 唤醒 agent
```

Kernel fast-path 失败（不可造/无法推断）→ push UNIT_REQUEST_UNFULFILLED → 唤醒 Capability。

### 7.3 当前状态 / 目标状态

| 功能 | 当前 | 目标 |
|------|------|------|
| 资源分配 | **已实现** | 保持 |
| UnitRequest | **Phase 1 实现中** | 完整三步 |
| Agent suspend/wake | **Phase 1 实现中** | 完成 |
| Capability 自动创建 | **Phase 1 实现中** | 完成 |
| create_task 默认配 LLM | 当前行为 | **目标：降级为可选** |

---

## 8. Task 在目标架构中的角色

### 8.1 重新定义

Task 是：
- 用户可见工作项
- Commander 的计划项
- Job 的归属容器

Task **不再默认等于**一个独立 LLM brain。

### 8.2 Task 不是类型，路由结果决定处理方式

Task 本身没有 "Direct / Managed" 之类的类型属性。命令怎么处理，是路由链的结果：

| 路由结果 | 处理方 | LLM 参与 | 示例 |
|---------|--------|----------|------|
| NLU/rule 命中 | Kernel 直接创建 job | 否 | "建造电厂"、"展开基地车" |
| 经济/生产域 merge | EconomyCapability 处理 | Capability 内部 | "发展经济"、"爆兵" |
| 复杂命令 → Commander | Commander 决定如何执行 | Commander 决策 | "进攻东部"、"包围敌人基地" |

Commander 收到命令后可能：
- 直接翻译为 job config（不需要 sub-agent）
- 指示某个 Capability 处理
- 判断需要多步规划 → 创建临时 sub-agent

**是否需要 sub-agent 是 Commander 的运行时判断，不是 task 创建时的类型标签。**

### 8.3 收敛路径

```
当前状态                        目标状态
─────────                      ─────────
每个 task → 独立 TaskAgent       NLU/rule 命中 → 直接 job，无 LLM
                                Capability 域 → merge 到 Capability
                                其余 → Commander 决定处理方式
```

大多数日常命令被 NLU/rule 或 Capability 消化，不需要独立 LLM。Commander 只在需要时创建临时 sub-agent。

---

## 9. 数据流总览

### 9.1 玩家命令 → 执行

```
玩家: "造5辆坦克"
  → Adjutant → RuntimeNLU 命中
  → Direct Task → EconomyJob(3tnk ×5) + notify EconomyCapability
  → Job tick → GameAPI.produce()
  → 出厂 → Kernel 分配到 idle pool（或匹配 pending request）

玩家: "爆兵"
  → Adjutant → NLU 未命中 → LLM classify → merge to EconomyCapability
  → EconomyCapability wake → 全队列满载生产

玩家: "进攻东部"
  → Adjutant → NLU 未命中 → LLM classify → new command → Commander
  → Commander: 创建 Managed Task "进攻东部"
  → Sub-agent: request_units(vehicle ×5) → 阻塞等待
  → Kernel: idle 匹配 + fast-path → 出厂 → 分配 → 唤醒 agent
  → Sub-agent: attack(target)
```

### 9.2 Information Plane → 上层消费

```
WorldModel
  → compute_runtime_facts()
  → BaseStateExpert → base_phase, critical_missing
  → ThreatAssessor → threat_level, enemy_composition
  → query_producible_items → buildable units

  ↓ 消费者：
  Commander: strategic_summary + alerts
  Capability: 领域相关 facts + unfulfilled_requests
  TaskAgent: runtime_facts + info_subscriptions
  Adjutant: 查询回答用
```

---

## 10. 废弃/过渡/目标 清单

### 10.1 废弃（应逐步移除）

| 概念 | 原因 |
|------|------|
| 每个 task 默认配独立 LLM | 多脑战略一致性差，资源浪费 |
| TaskAgent 直接调 `produce_units` | 应走 `request_units`，生产权归 Capability/Kernel |
| `_derive_buildable_units` 硬编码推断 | 被 `query_producible_items` C# API 替代（保留为 fallback） |
| TaskAgent prompt 承载全部游戏知识 | 知识应下沉到 Expert/Knowledge |

### 10.2 过渡态（当前存在，目标态会消解）

| 概念 | 说明 |
|------|------|
| Adjutant disposition 直接创建 task | 过渡：目标是 Adjutant → Commander → create_task |
| 多个 TaskAgent 并存做战略判断 | 过渡：目标是 Commander 统一战略 |
| TaskAgent 的重 prompt | 过渡：知识/guard/policy 逐步下沉 |
| QueueManager 做生产善后 | 过渡：有了 Capability + abort 清队列后应减少依赖 |

### 10.3 目标态（保留并强化）

| 概念 | 说明 |
|------|------|
| RuntimeNLU + Rule 前置路由 | 核心稳定性根基 |
| Kernel 确定性调度 | 无 LLM，毫秒级 |
| UnitRequest + Kernel 三步处理 | 经济/分配的正确分离 |
| EconomyCapability | 第一个持久 Capability |
| Commander | 唯一战略脑（Phase B） |
| Information Plane | 系统认知基础 |
| Expert/Job 自主执行 | 传统 AI tick 级执行 |
| 结构化 Task→Player 通信 | task_info / task_warning / task_question / task_complete_report |
| Session/Task logging + Trace | 可复盘工程基础 |

---

## 11. 实现阶段

### Phase A — EconomyCapability 落地（当前）

目标：把经济/生产域从 TaskAgent 中完整剥离。

- [x] C# `query_producible_items` API
- [x] Python `GameAPI.query_producible_items()` + WorldModel 集成
- [ ] Kernel `register_unit_request` 完整实现（idle 匹配 + fast-path + 自动分配）
- [ ] Agent suspend/wake 机制
- [ ] EconomyCapability 自动创建与持久运行
- [ ] 普通 TaskAgent tool 变更（`produce_units` → `request_units`）
- [ ] Adjutant 集成（NLU notify + merge 路由）

### Phase B — Commander + 更多 Capability

目标：引入 Commander 统一战略，扩展 Capability 到其他域。

- [ ] Commander 实现（战略意图 / 阶段判断 / task 处置）
- [ ] Adjutant disposition → Commander（不再直接创建 task）
- [ ] ReconCapability
- [ ] CombatCapability
- [ ] 扩展 Information Experts（QueueState / Awareness / TechGate）

### Phase C — 架构收口

目标：消解"task = 独立 LLM 脑"假设。

- [ ] Task 不再默认配 LLM，sub-agent 由 Commander 按需创建
- [ ] Prompt 精简——知识/guard/policy 全部下沉到系统层
- [ ] BaseCapability / DefenseCapability
- [ ] 完整评估体系（NLU correctness / workflow correctness / live experience）

---

## 12. 设计决策记录

| 决策 | 理由 |
|------|------|
| 单一 Commander 而非多 Task 脑 | 近年 LLM+RTS 研究一致结论：RTS 需要中心化指挥 + 领域能力 + 确定性执行 |
| Capability 只决定"造什么"，Kernel 决定"分给谁" | 避免 LLM 参与高频资源分配，降低冲突 |
| `request_units` 是阻塞的 | 降低 agent 复杂度，agent 不需要理解生产流程 |
| C# API 获取真实可建造列表 | `ProductionQueue.BuildableItems()` 已有完整实现，比 Python 硬编码推断准确且可维护 |
| Information Expert 持续输出派生语义 | LLM 不应从 raw state 推断 doctrine |
| NLU/Rule 前置 | 简单命令不该进 LLM，这是稳定性的根 |
| 渐进收敛而非一次重写 | 当前系统的正确部件（NLU、Expert、WorldModel、Logging）应保留 |

---

## 13. 与旧文档的关系

| 文档 | 状态 |
|------|------|
| `design.md` | **历史基线**。三级架构（Kernel/Task/Job）仍是基础框架，但"每 task 一个 LLM"的默认假设被本文档修正 |
| `architecture_crisis.md` | **诊断正确**。问题识别成立，收敛方向被本文档正式确认 |
| `capability_task_design.md` | **Phase A 的实施规范**。详细设计仍有效 |
| `adjutant_redesign.md` | **方向正确**。NLU 前置 + disposition 路由保留，disposition 目标从 Kernel 改为 Commander（Phase B） |
| `optimization_tasks.md` | **持续有效**。"信息优先于约束"原则是 Information Plane 的核心理念 |
| `system_report_v2.md` | **历史快照**。用于理解演化过程 |
