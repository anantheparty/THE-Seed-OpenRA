# Architecture Analysis: Current State → Roadmap Target

## 1. 系统现状摘要

### 已有资产（可复用）

| 层 | 组件 | 文件 | 成熟度 | 备注 |
|---|---|---|---|---|
| **Adapter** | GameAPI (Socket RPC) | `openra_api/game_api.py` | ✅ 生产级 | 底层稳定，retry+cache |
| **Adapter** | MacroActions | `openra_api/macro_actions.py` | ✅ 可用 | 高层封装，显式 Job 赋值 |
| **Adapter** | RTSMiddleLayer | `openra_api/rts_middle_layer.py` | ✅ 简洁 | 统一门面 |
| **Intel** | IntelService | `openra_api/intel/service.py` | ✅ 核心 | 多层缓存，抽象状态 |
| **Intel** | ZoneManager | `openra_state/intel/zone_manager.py` | ⚠️ 部分 | DBSCAN 聚类，缺 chokepoint |
| **NLU** | Phase2NLUGateway | `agents/nlu_gateway.py` | ✅ 生产级 | 60-80% 命中率，安全门控 |
| **NLU** | CommandRouter | `the-seed/.../command_router.py` | ✅ 成熟 | 模板生成 + 模糊匹配 |
| **NLU** | IntentModel | `nlu_pipeline/artifacts/` | ✅ 训练管线 | sklearn, 9.4MB |
| **Jobs** | JobManager | `openra_api/jobs/manager.py` | ⚠️ 基础 | 资源绑定可用，缺生命周期 |
| **Jobs** | AttackJob | `openra_api/jobs/attack.py` | ⚠️ 简单 | 最近目标优先，无状态机 |
| **Jobs** | ExploreJob | `openra_api/jobs/explore.py` | ⚠️ 可用 | 射线采样，有粘性目标 |
| **Agent** | EnemyAgent | `agents/enemy_agent.py` | ⚠️ 可参考 | 45s tick 自治循环 |
| **Agent** | CombatAgent | `agents/combat/combat_agent.py` | ⚠️ 实验 | 编队微操，LLM 目标分配 |
| **Agent** | EconomyEngine | `agents/economy/engine.py` | ⚠️ 固定 | 算法驱动，无 LLM |
| **Tactical** | BiodsEnhancer | `tactical_core/enhancer.py` | ⚠️ 有价值 | 势场微操，协同撤退 |
| **Data** | combat_data | `openra_state/data/combat_data.py` | ✅ 可用 | 单位分类+战斗值 |

### 核心缺失（必须新建）

| 缺失 | Roadmap 对应 | 严重程度 |
|---|---|---|
| **Kernel** — 中央调度/仲裁 | §4 | 🔴 架构核心 |
| **统一对象模型** — Directive/TaskSpec/ExecutionJob/Outcome | §2 | 🔴 数据骨架 |
| **共享世界模型** — 不是 IntelService 的复用，是更高层抽象 | §7 | 🔴 自然性来源 |
| **专家契约** — 标准化接口 (Info/Planner/Execution) | §3 | 🔴 扩展基础 |
| **Executor 实例化框架** — 多实例隔离 | §3.2 | 🟡 并发前提 |
| **Constraint 系统** — 持续约束对象 | §2.5 | 🟡 策略调制 |
| **正式生命周期** — pending→running→succeeded/failed | §5 | 🟡 可观测性 |
| **地图语义外置** — chokepoint/expansion/key positions | §7.1C | 🟡 游戏常识 |

---

## 2. 关键架构决策点（Phase 0 必须拍板）

### 决策 1：Kernel 实现策略 ✅ 已拍板

**决策：Kernel 是被动的，不拥有循环。**

- Kernel 不做 tick 轮询
- Task 自己拥有循环（4 种 Task 类型，持续型 Task 自己 cycle）
- Kernel 只在关键节点介入：Task 创建 / 完成 / 资源冲突 / 抢占
- 本质上 Kernel 是一个**事件驱动的仲裁器**，不是调度器

这比之前的"混合 tick + 事件"方案更简洁。Task 的自治性更强。

### 决策 2：世界模型 vs IntelService 的关系 ✅ 已拍板

**决策：WorldModel 和 IntelService 都是可以完整改造/重写的观察类专家系统。**

- 不存在"保留 IntelService 作为数据层"的约束
- WorldModel 本身就是一种观察型专家系统
- IntelService 也是，两者都需要重新设计
- 最终形态：统一的 WorldModel 作为所有专家的共享状态面
- 内部可以有缓存、Zone 分析、派生推导，但对外是一个接口

### 决策 3：现有代码处置 ✅ 已拍板

**决策：全部可以重写。核心系统全面重新设计。**

用户明确表示：
- 没有人在使用当前系统，"全部干翻"也可以
- 不需要增量兼容或迁移路径
- 这是大工程，但不急
- 需要加入大量传统 RTS AI 技术（BT、FSM、HSM、影响力图、GOAP 等）

现有代码中可参考的设计模式（不是保留，是参考）：
- CombatAgent.company_states → ExecutorInstance 模式参考
- EconomyEngine.decide() → Planner 模式参考
- BiodsEnhancer 势场微操 → 可能在新 CombatExecutor 中重新实现
- NLUGateway → Interpreter 设计参考
- GameAPI / Socket RPC → 底层适配器可能保留（取决于 OpenRA mod 侧是否改变）

### 决策 4：对象模型在代码中的表达

- 用 `dataclass` + `enum`，简单直接
- 不引入 ORM、protobuf、或 pydantic

### 决策 5：LLM 调用点收敛

**LLM 的角色：语义解释器，不是执行者。**

必须 LLM 的点：
1. Interpreter：用户自然语言 → Directive

可能 LLM 的点：
2. 高不确定战略决策（极少数，如两线受攻先守哪边）

**绝不 LLM 的点** — 这些全部由传统 AI 专家系统处理：
- 战术执行（包围、夹击、撤退路线）→ BT/FSM + 影响力图 + 势场
- 目标分配 → 评分系统 + 克制关系规则
- 生产决策 → 经济引擎（算法）
- 侦察路径 → 影响力图 + 评分函数
- 微操 → 势场 + 状态机

### 决策 6（新增）：传统 RTS AI 技术栈 🔲 待调研

**这是 roadmap 最大的缺口。** 用户核心痛点：

> "包围"不是把兵分成两路从上下走。真正的包围涉及地形分析、兵力分配、时间协调、实时适应。

需要深度调研并设计：
- **影响力图 / 威胁图** — 区域控制、安全通路、弱点发现
- **行为树 / FSM / HSM** — 编队级别的行为控制
- **GOAP / HTN** — 战术计划生成与分解
- **战术编组 AI** — 多单位协调（包围、夹击、牵制+主攻）
- **势场微操** — 单位级别的移动和战斗
- **多层架构** — 战略 → 战术 → 微操的通信模式

**已分配 yu 进行深度互联网调研。**

---

## 3. 分层架构概览（设计提案）

```
┌─────────────────────────────────────────────┐
│                User Input                    │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│          Interpreter (NLU + LLM)            │
│  NLUGateway → CommandRouter → [LLM fallback]│
│  Output: Directive                           │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│               Kernel                         │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
│  │ Admission │ │Arbitrator│ │  Lifecycle   │ │
│  │  Gate     │ │(resource │ │  Manager     │ │
│  │          │ │ conflict)│ │              │ │
│  └──────────┘ └──────────┘ └─────────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
│  │Constraint│ │  Expert   │ │  Outcome     │ │
│  │ Registry │ │ Activator │ │  Judge       │ │
│  └──────────┘ └──────────┘ └─────────────┘ │
│  Input: Directive → TaskSpec → ExecutionJob  │
│  Output: Outcome                             │
└───────┬───────────────────┬─────────────────┘
        ▼                   ▼
┌──────────────┐   ┌──────────────────────────┐
│ Info Experts │   │   Planner Experts         │
│ ─────────────│   │ ─────────────────────     │
│ ThreatAssess │   │ ReconRoutePlanner         │
│ MapSemantics │   │ CombatModeAdvisor         │
│ ResourcePress│   │ ExpansionPlanner          │
│ EnemyHypoth  │   │ ProductionAdvisor         │
└──────┬───────┘   └───────────┬──────────────┘
       │  read                 │ propose
       ▼                       ▼
┌─────────────────────────────────────────────┐
│           Shared World Model                 │
│  ┌─────────┐ ┌──────────┐ ┌──────────────┐ │
│  │AreaCtrl │ │Hypotheses│ │ Groupings    │ │
│  │ThreatMap│ │EnemyModel│ │ FrontLine    │ │
│  │MapSem   │ │Resources │ │ ActiveJobs   │ │
│  └─────────┘ └──────────┘ └──────────────┘ │
│  Backed by: IntelService (data) + derived   │
└──────────────────┬──────────────────────────┘
                   ▲ read/write
┌──────────────────┴──────────────────────────┐
│          Execution Experts                   │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
│  │ ReconExec│ │CombatExec│ │ EconomyExec │ │
│  │ Instance │ │ Instance │ │  Instance   │ │
│  │ #1, #2   │ │ #1..#N   │ │  (single)   │ │
│  └──────────┘ └──────────┘ └─────────────┘ │
│  ┌──────────┐ ┌──────────┐                  │
│  │DefenseExe│ │MovementEx│                  │
│  │ Instance │ │ Instance │                  │
│  └──────────┘ └──────────┘                  │
│  Internal: FSM / HSM / BT per expert        │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│          Game Adapter                        │
│  GameAPI ← MacroActions ← IntelService      │
│  Socket RPC (7445) ↔ OpenRA C# Mod          │
└─────────────────────────────────────────────┘
```

---

## 4. 与现有代码的映射关系

| Roadmap 组件 | 现有代码 | 改造策略 |
|---|---|---|
| Interpreter | NLUGateway + CommandRouter + SimpleExecutor | 输出从"执行代码"改为"Directive 对象" |
| Kernel | 不存在 | **新建** |
| WorldModel | IntelService (部分) | IntelService 保留为数据层，上建 WorldModel 抽象层 |
| ReconExecutor | ExploreJob | 包装为 Executor 接口 |
| CombatExecutor | AttackJob + CombatAgent + BiodsEnhancer | 整合，拆出 Info/Planner |
| EconomyExecutor | EconomyEngine | 改接口 |
| DefenseExecutor | 不存在 | **新建** |
| MovementExecutor | 不存在 | **新建** |
| ThreatAssessment (Info) | IntelService.combat 部分 | 抽出为独立 Expert |
| MapSemantics (Info) | ZoneManager | 扩展 |
| GameAdapter | GameAPI + MacroActions | 保持不变 |

---

## 5. 关键发现（来自 yu 深度审查）

### 发现 1：两套 Intel 栈并行
系统中有**两个独立的 intel 实现**，不是一个不够用的问题：
- `openra_api.intel.IntelService` → 给 Jobs/中间层用，返回 `IntelModel`
- `openra_state.intel.IntelligenceService` → 给 StrategyAgent 用，维护 zone/blackboard

它们概念重叠但不统一。CombatAgent 和 EconomyAgent 甚至都不用这两个，直接调 API。

**影响**：WorldModel 设计必须先解决这个分裂，否则新专家体系也会各自为政。

### 发现 2：CombatAgent.company_states 是最佳 ExecutorInstance 参考
CombatAgent 已经有：
- 每个 company 独立的 `status` (combat/relocate)
- 独立的 `params`、`strategic_target_pos`
- `is_processing` 锁
- pending_orders 缓冲

这几乎就是 Roadmap 说的 ExecutorInstance 模式，只是没有正式化。

### 发现 3：EconomyEngine 天然是 Planner 模式
`EconomyEngine.decide(state) → List[Action]` 已经是：
- 纯决策（不直接调 API）
- 输入是状态快照
- 输出是动作建议

这就是 Roadmap 里 PlannerExpert 的接口原型。

### 发现 4：Job 系统是执行基座，不是 Kernel
- 生命周期：create → register → bind → tick → 无限运行
- 失败：只有异常驱动，没有语义失败
- 无优先级，无抢占，无 Outcome
- Actor 死亡 = 自动解绑，Job 不受影响（可以 0 actor 继续"运行"）

**结论**：JobManager 可以作为 Executor 内部的 actor 绑定机制保留，但 Kernel 必须在其上层新建。

### 发现 5：命令发出路径完全分裂
| Agent | 命令路径 |
|---|---|
| EnemyAgent | 自然语言 → Executor 文本命令 |
| StrategyAgent | company_order API |
| CombatAgent | 直接 Socket API |
| EconomyEngine | Action 对象 → wrapper 执行 |

四种完全不同的命令抽象。

---

## 6. 风险评估

### 风险 1：重构范围过大
- 现有代码 ~8000 行（不含 the-seed 框架）
- 全部重写不现实
- **对策**：增量式改造，Adapter 层完全不动，从 Kernel 和对象模型开始

### 风险 2：Interpreter 输出格式变化
- 现有 NLU 输出是"可执行代码"，新架构要求输出 Directive
- 这是最大的接口断裂点
- **对策**：Phase 1 可以让 Interpreter 同时输出代码（兼容旧路径）和 Directive（新路径）

### 风险 3：JobManager 生命周期不完整
- 现有 Job 没有 pending/admitted/succeeded 等状态
- 但已有 assign/unassign/tick 基本骨架
- **对策**：扩展 Job base class，不替换 JobManager

### 风险 4：测试困难
- 没有看到单元测试
- RTS 实时环境难 mock
- **对策**：WorldModel 和 Kernel 可以纯逻辑测试，Executor 需要 GameAPI mock

---

## 6. 专家契约设计（yu 草稿 + wang/yu 对齐）

详见 `expert_contracts_draft.py`。

### 统一生命周期
```
bind(task, world) → ResourceClaim[]
start(task, world)
tick(world) → ActionProposal[]
status() → ExpertStatus
release(world, reason)       # 优雅退出
abort(reason)                # 强制抢占（新增）
```

### 4 个设计问题已对齐

| 问题 | 决策 |
|---|---|
| ActionProposal 执行者 | **Proposal-first 为默认**：expert 提议，Kernel/Adapter 执行。微操可声明 `direct_fast_path` 策略例外 |
| 强制抢占 | **新增 `abort(reason)`**：与 `release()` 分开。abort 幂等、尽力而为 |
| 并发策略 | **类级别声明** `ConcurrencyPolicy(max_instances, resource_kinds, share_policy, preempt_policy, merge_policy, direct_fast_path)` |
| 资源 ID | **统一 str**：`"actor:123"`, `"queue:Building"`, `"squad:1"`, `"zone:5"`。合约层用 str，内部适配器保持原生类型 |

---

## 7. Intel 合并方案（yu 分析 + wang/yu 对齐）

详见 `intel_merge_analysis.md`。

### 合并策略：Facade with Unified Semantics
```
WorldModel (facade)
├── 原始快照 ← IntelService.snapshot cache
├── 派生情报 ← IntelService.IntelModel
├── Zone/区域 ← IntelligenceService.ZoneManager
├── 运行时任务/资源 ← 新建 TaskRegistry + ResourceBindings
└── 记忆/假设 ← 新建 MemoryStore + HypothesisManager
```

### 设计不变式（wang/yu 共识）
1. **WorldModel 必须包含任务/资源状态**，否则只是又一个 intel 封装
2. **Facade 必须提供统一查询语义**，不是纯 pass-through（如 `get_area_threat(pos, r)` 融合两栈数据）

### 4 阶段迁移
1. Facade + 双栈委托 + 新建任务/资源层
2. Strategy 改读 Facade
3. Jobs/Expert 改读 Facade
4. 内部整合（可选）

---

## 8. Phase 0 交付清单（更新版）

Phase 0 的目标是"结构定型"，不写执行代码。

1. ✅ **系统现状分析** — architecture_analysis.md
2. ✅ **现有代码深度审查** — yu_investigation_report.md
3. ✅ **专家契约草稿** — expert_contracts_draft.py（4 个设计问题已对齐）
4. ✅ **Intel 合并分析** — intel_merge_analysis.md
5. 🔲 **统一对象模型定义** — Directive, TaskSpec, ExecutionJob, Constraint, Outcome
6. 🔲 **Kernel 接口定义** — 方法签名 + 状态转移图
7. 🔲 **并发策略声明** — 每种 Executor 的 ConcurrencyPolicy
8. 🔲 **Interpreter 输出改造方案** — Directive schema + NLU 兼容
9. 🔲 **用户拍板** — 3 个核心架构决策
