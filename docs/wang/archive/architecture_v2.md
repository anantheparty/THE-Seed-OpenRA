# Architecture v2: Revised Design Proposal

> 基于 roadmap + 现有代码审查 + 传统 RTS AI 调研的综合设计

## 0. 核心认知更新

调研发现了 roadmap 的一个结构性盲区：**roadmap 把专家系统分为 Information / Planner / Execution 三类，但没有明确的层级关系。** 实际上，所有成功的 RTS AI 都有清晰的三层分工：

```
Strategy (做什么) → Tactics (怎么做) → Micro (逐帧执行)
```

这不是 roadmap 说的三类专家，而是三个**执行层级**。每个层级内部都可能有 Information + Planner + Execution 类型的模块。

---

## 1. 三层执行架构

### Layer 0: Strategy（战略层）
**时间尺度：** 30-60s 决策周期
**职责：**
- 选择主要目标（进攻/防守/扩张/科技）
- 设定全局约束（economy_first, defend_when_attacked）
- 决定兵种配比
- 触发战术任务

**实现：** 规则 + LLM（少量高不确定决策）+ Planner

**对应 roadmap：** Kernel 的 admission/constraint 部分 + Composition/Policy Expert

### Layer 1: Tactics（战术层）⭐ 核心创新点
**时间尺度：** 1-10s 决策周期
**职责：**
- 选择战术方法（Tactical Method）
- 路线规划 + 接近扇区选择
- 兵力分组 + 角色分配
- 时间协调（多路同步、牵制+主攻）
- 方法执行监控 + 失败转换

**实现：** 战术方法库 + 编队 FSM/State Tree + 影响力图 + 可选搜索

**这是"包围"问题的解决层。**

### Layer 2: Micro（微操层）
**时间尺度：** 逐帧/逐 tick
**职责：**
- 单位移动和间距
- 火力分配
- 风筝/追击/撤退
- 队形维持
- 碰撞避免

**实现：** 势场 + 本地启发式 + 每单位类型特化控制器

---

## 2. 战术方法（Tactical Method）— 核心设计

**这是整个系统最重要的新概念。** Roadmap 没有覆盖。

### 什么是战术方法

战术方法是一个**有阶段状态的、可参数化的、可中止的战术执行单元**。

不是"包围"这个词，而是一个完整的执行过程：

```python
class EncircleTarget(TacticalMethod):
    """包围战术方法"""

    phases = [
        "recon",              # 侦察目标周围地形/威胁
        "sector_selection",   # 选择 2-3 个接近扇区
        "role_assignment",    # 分配兵力到各路（前锋/侧翼/远程/阻截）
        "staging",            # 各路移动到预备位置（敌方视野外）
        "synchronize",        # 等待所有路就绪 or 触发牵制
        "collapse",           # 全路同时压缩
        "containment",        # 维持包围圈
        "abort_or_convert",   # 局势变化 → 转为追击/撤退/单面进攻
    ]
```

### 方法库（第一版建议）

| 方法 | 典型场景 |
|---|---|
| `FrontalAssault` | 正面推进，适合优势兵力 |
| `PincerAttack` | 两路夹击，需要 2 个可用接近方向 |
| `EncircleTarget` | 包围，需要 3+ 接近方向且兵力充足 |
| `FightingRetreat` | 边打边撤，保护高价值单位 |
| `HoldChoke` | 扼守要道 |
| `RunbyHarass` | 快速部队绕后骚扰 |
| `EscortSiege` | 护送远程单位到攻击位置 |
| `DefensiveShell` | 基地防御环形部署 |

### 每个方法必须具备

1. **阶段状态（Phase FSM）** — 不是黑盒，每个阶段可观测可调试
2. **地形/威胁查询** — 从 WorldModel 获取影响力图数据
3. **角色分配** — 不是平均分兵，根据兵种克制分配
4. **时间协调** — ETA 估算、同步窗口、就绪阈值
5. **适应/中止规则** — 条件不满足时降级或转换方法
6. **微操委托** — 各分组的实际移动/战斗委托给 Micro 层

---

## 3. 影响力图系统

调研确认：影响力图是**分层的**，不是一张图。

### 推荐 4 层地图

| 地图 | 更新频率 | 用途 |
|---|---|---|
| `terrain_mobility` | 一次性（地图不变） | 通行性、瓶颈、斜坡、走廊 |
| `enemy_vision` | 每 1-2s | 敌方可见区域估计 |
| `enemy_threat` | 每 1-2s | 按兵种类别的武器威胁场 |
| `friendly_support` | 每 2-5s | 增援密度、撤退覆盖、本地防空/反甲 |

### 影响力图如何驱动战术

- **"包围"路线选择**：选择 enemy_threat 低 + terrain_mobility 高的接近扇区
- **撤退路线**：选择 friendly_support 高 + enemy_threat 低的方向
- **偷袭路线**：选择 enemy_vision 低的路径
- **集结点**：enemy_vision 外 + friendly_support 高的位置

---

## 4. 修正后的系统全景

```
┌─────────────────────────────────────────────────┐
│              User / Interpreter                  │
│  "包围那个基地"                                    │
│  → Directive{intent:surround, target:enemy_base} │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│               Kernel (被动仲裁)                   │
│  Directive → TaskSpec → ExecutionJob             │
│  资源分配 / 冲突仲裁 / Outcome 判定              │
│  （无自己的循环，事件驱动）                         │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│          Layer 0: Strategy                       │
│  决定是否现在进攻、兵力是否足够、约束设置          │
│  → 选择战术方法: EncircleTarget                  │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│          Layer 1: Tactics  ⭐                     │
│  EncircleTarget 执行中:                           │
│    phase: staging                                │
│    flank_A: [重坦x3, 火箭x2] → 西北集结点        │
│    flank_B: [步兵x5, 防空x2] → 东南集结点        │
│    timing: flank_A ETA 8s, flank_B ETA 12s       │
│    decision: flank_A 先牵制, flank_B 4s 后出发    │
│                                                   │
│  查询 WorldModel:                                 │
│    terrain_mobility → 西北走廊可通行              │
│    enemy_threat → 东侧反坦克威胁高 → 步兵走东侧  │
│    enemy_vision → 南侧盲区 → 集结在此            │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│          Layer 2: Micro                          │
│  flank_A: 势场推进 + 火力分配 + 间距控制          │
│  flank_B: 势场推进 + 风筝/追击 + 队形维持        │
│  per-unit: 受伤撤后、反甲集火、防空覆盖          │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│          WorldModel (共享状态面)                   │
│  影响力图 × 4 | Zone 图 | 任务/资源绑定          │
│  敌情假设 | 战略记忆 | 编组语义                   │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│          Game Adapter                            │
│  GameAPI (Socket RPC) → OpenRA                   │
└─────────────────────────────────────────────────┘
```

---

## 5. 与 Roadmap 的对照

| Roadmap 概念 | v2 对应 |
|---|---|
| Interpreter | 不变，LLM + NLU → Directive |
| Kernel | 被动仲裁器（无循环），事件驱动 |
| Information Expert | WorldModel 内的各种推导器 + 影响力图系统 |
| Planner Expert | Strategy 层 + Tactical Method 的规划阶段 |
| Execution Expert | Tactical Method 执行 + Micro 控制器 |
| Shared World Model | 影响力图 × 4 + Zone 图 + 任务/资源/假设/记忆 |
| ExecutorInstance | TacticalMethod 实例（每次任务一个实例，有阶段状态） |
| Game Adapter | GameAPI，基本不变 |

### Roadmap 缺失的、v2 新增的

1. **三层执行架构**（Strategy → Tactics → Micro）— roadmap 没有明确的层级
2. **Tactical Method 概念** — 有阶段状态的战术执行单元
3. **影响力图系统** — 4 层分层地图
4. **方法库** — 8 个可复用的战术方法模板
5. **时间协调模型** — ETA / 同步窗口 / 就绪阈值

---

## 6. 与现有论文/系统的对标

| 系统 | 相似点 | 差异点 |
|---|---|---|
| SwarmBrain | LLM 做宏观 + 快速反射做战术 | 我们更强调方法库而非反射 |
| Adaptive Command | LLM 战略顾问 + BT 执行 | 我们用方法库替代通用 BT |
| UAlbertaBot | 编队 + 战斗模拟 + 管理器 | 我们加入了 LLM Interpreter |
| EISBot | 层级反应式行为 + 黑板 | 我们用 WorldModel 替代黑板 |
| OpprimoBot | 势场 + A* 混合微操 | 我们把势场放在 Micro 层下 |

---

## 7. 开发优先级建议

### Phase 1: 骨架
1. 对象模型（Directive, TaskSpec, ExecutionJob, Outcome）
2. Kernel 仲裁器
3. WorldModel facade（先包装 IntelService）
4. 一个最简 TacticalMethod（FrontalAssault）
5. Micro 层（从 BiodsEnhancer 演化）

### Phase 2: 空间智能
6. 影响力图系统（terrain_mobility 先行）
7. enemy_vision + enemy_threat 动态图
8. 路线规划器（使用影响力图）

### Phase 3: 方法扩展
9. PincerAttack
10. EncircleTarget
11. FightingRetreat
12. HoldChoke

### Phase 4: 完善
13. 看板/Dashboard
14. Interpreter 改造（NLU → Directive）
15. 战略记忆 + 敌情假设
16. 剩余方法库
