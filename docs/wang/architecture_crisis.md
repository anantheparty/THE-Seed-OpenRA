# 架构困境报告：多 Task Agent 模式的系统性问题

**作者**: Wang | **日期**: 2026-04-06 | **状态**: 诊断已确认 — 多 Task Agent 问题成立，收敛方向已定（EconomyCapability + Adjutant 做厚 + TaskAgent 降级）

---

## 1. 问题陈述

经过 R7~R9 三轮 E2E 测试，Expert 层的 bug 已基本修复（探索卡死、经济 crash、战斗分散等），但暴露出一个更深层的架构问题：**Adjutant 的"每条输入创建独立 Task + 独立 LLM Agent"模式本身就是错误的。**

### 1.1 现象

- 玩家说"建坦克"、"发展经济"、"探索地图"→ 3 个独立 Task Agent，各有独立 LLM 循环
- Task Agent 之间零协调，争抢同一个生产队列、同一批单位
- 一个说建电厂，另一个也建电厂 → 重复建造
- "全力进攻"和"发展经济"同时活跃 → 资源拉扯
- 5 个 agent 各自轮询 LLM → 大量无效调用（R7: 252 次 LLM, 25% 空转）

### 1.2 E2E 数据证据

| 指标 | R7 | R8 | R9 |
|------|----|----|-----|
| 任务数 | 36 | 24 | 7 |
| LLM 调用 | 252 | 345 | 43 |
| 任务冲突（重复建造/资源争抢）| 9+ 个电厂 | 多 | 多 |
| defend_base 洪泛 | 13 个 | 1 个(修) | — |
| 探索效率 | 77% → 卡死 | 85% → 卡死 | 4.2% 卡死(bug) |

### 1.3 根因

**RTS 需要一个战略大脑，不是一个议会。**

当前架构让每条玩家指令都变成一个独立决策者。5 个 agent 同时活跃时：
- 没人知道全局优先级（经济 vs 军事 vs 侦察）
- 没人做资源仲裁（谁先用生产队列？）
- 没人处理指令冲突（"全力进攻"后说"发展经济"，前者应该降优先还是取消？）
- 每个 agent 都在独立调 LLM 问"我该干嘛"→ 但没人告诉它别的 agent 在干嘛

---

## 2. 成熟 RTS AI 怎么做的

| 项目 | 架构模式 | 核心特点 |
|------|---------|---------|
| **OpenRA ModularBot** | 领域独占模块 | BaseBuilder/UnitBuilder/SquadManager 各管一域，零冲突 |
| **UAlbertaBot** (SC:BW) | 单一指挥链 | 1 个 CombatCommander → Squad → Micro；1 个 ProductionManager |
| **PurpleWave** (SC:BW) | HTN 分层规划 | 1 个 planner，严格优先级资源分配 |
| **Sorian AI** (SupCom) | 策略人格 | 1 个 Strategy Manager 选 Rush/Turtle/Adaptive，全局统一 |

**共同点：所有成熟实现都是单一决策中心 + 领域执行器。没有多 agent 竞争模式。**

---

## 3. 改造方案

### 方案 A：单 Commander Agent（推荐）

```
玩家输入 → Adjutant（分类/NLU）→ Commander Agent（唯一 LLM 大脑）
                                      │
                                      ├── tool: manage_economy(build_order, produce, expand)
                                      ├── tool: manage_combat(attack, defend, retreat, patrol)
                                      ├── tool: manage_recon(scout, explore)
                                      ├── tool: manage_production(queue, priority)
                                      └── tool: query_world / send_message / ...
```

**核心变化：**
- Adjutant 不再创建 Task，而是把玩家指令注入 Commander 的 event queue
- Commander 是一个常驻 LLM agent，持续运行，拥有全局视野
- Expert 系统保持不变，作为 Commander 的 tool
- 玩家说"经济优先"→ Commander 调整内部优先级，不创建新 agent

**优势：**
- 零 Task 冲突（只有一个决策者）
- LLM 调用大幅减少（一个 agent 的 context 包含一切）
- 战略连贯（"先防守再进攻"只需 Commander 排序）
- 玩家意图传递链清晰（Adjutant → Commander → Expert）

**风险与对策：**
- Context window 压力 → 压缩 world summary，只给关键 facts
- 单点故障 → LLM 调用失败时 Expert 继续自主执行上一个指令
- 响应延迟 → Commander 用 tool 分发后立即返回，不等执行完成

**改动量估算：**
- `kernel/core.py` — 改为管理单个 Commander 而非多个 TaskAgent（~200 行改）
- `task_agent/agent.py` → `commander/agent.py` — 重构为常驻 agent（~300 行改）
- `task_agent/tools.py` → `commander/tools.py` — 领域化 tool 定义（~150 行改）
- `adjutant/adjutant.py` — command 路径改为注入 Commander queue（~50 行改）
- 总计：~700 行改动，不是重写

### 方案 B：域隔离 + 协调器

```
Adjutant → Coordinator（规则层）
              ├── EconomyAgent（独占生产+资源）
              ├── CombatAgent（独占军队）
              └── ReconAgent（独占侦察）
```

- 每域最多一个 agent，资源域隔离
- Coordinator 做跨域仲裁（规则，非 LLM）
- 优点：并行、一域挂不影响其他
- 缺点：跨域协调复杂（"全力进攻"需经济+军事配合），需要设计仲裁规则

**改动量：~1000 行**

### 方案 C：保留多 Task + 中央调度

- 最小改动，加一个 Scheduler 管优先级和资源分配
- 优点：改动小（~300 行）
- 缺点：治标不治本，Scheduler 增加 LLM 开销，多 agent 竞争本质不变

---

## 4. 推荐决策

**推荐方案 A（单 Commander Agent）**，理由：

1. **与成熟实现一致** — 所有强 RTS AI 都是单一决策中心
2. **我们的优势** — LLM 作为 Commander 天然擅长多目标权衡、优先级排序、自然语言理解
3. **改动可控** — 不是重写，是把 N 个 TaskAgent 合并成 1 个 Commander
4. **Expert 层不变** — 已投入的 Expert 优化（frontier 探索、focus fire、harvester 管理）全部保留
5. **LLM 效率提升** — 从 N 个 agent × M 次调用 → 1 个 agent × K 次调用（K << N×M）

### 实施路径

```
Phase 0: 设计 Commander SYSTEM_PROMPT + tool 定义（1天）
Phase 1: 实现 Commander agent + Adjutant 对接（2天）
Phase 2: 迁移 Expert tool 接口（1天）
Phase 3: E2E 验证（1天）
```

---

## 5. 与 Expert 改造的关系

Xi 已完成 Expert 改造设计文档（`docs/xi/expert_redesign.md`），包含 InfluenceMap、BuildOrder、CombatSim 等。这些改造和架构切换**完全兼容**：

- Expert 是执行层，无论上层是多 TaskAgent 还是单 Commander，Expert 接口不变
- InfluenceMap 等新 Expert 作为 Commander 的 tool 暴露
- 建议：**先做架构切换（A），再做 Expert 增强**——在正确的架构上优化才有意义

---

## 附录：当前架构 vs 目标架构

```
当前：
  玩家 → Adjutant → Kernel.create_task() × N → TaskAgent × N → Expert × M
                                                  (各自 LLM)

目标（方案 A）：
  玩家 → Adjutant → Commander（1 个常驻 LLM agent）→ Expert × M (tool call)
```
