# OpenRA 智能体重构设计蓝图 (Refactoring Blueprint)

本文档将 `REFACTOR_PLAN.md` 中的架构目标分解为可执行的开发阶段与具体任务。我们将采用 **"基础先行，逐步替换"** 的策略，在不修改 `the-seed` 核心框架的前提下，构建多智能体系统。

## Phase 1: 基础架构 (Infrastructure)
**目标**: 建立多智能体运行环境，实现简单的 "副官-执行" 结构。

- [ ] **Task 1.1: 全局黑板 (Global Blackboard)**
    - 创建 `agents/global_blackboard.py`。
    - 实现单例模式或共享实例，支持线程安全的读写（如果未来上多线程）。
    - 定义标准数据分区：`command` (用户指令), `market` (任务市场), `intelligence` (公共情报)。
- [ ] **Task 1.2: 智能体基类 (Base Agent Wrapper)**
    - 创建 `agents/base_agent.py`。
    - 封装 `the_seed.FSM` 和 `NodeFactory`。
    - 实现 `tick()` 方法，用于驱动一次 FSM 状态流转。
    - 实现与 `GlobalBlackboard` 的连接，将全局数据注入到本地 `FSMContext.blackboard` 中。
- [ ] **Task 1.3: 主循环重构 (Main Loop)**
    - 创建 `main_mas.py` (新入口)。
    - 实现 Round-Robin 调度器，循环调用所有注册 Agent 的 `tick()`。
    - 迁移 `DashboardBridge` 以支持显示多 Agent 状态（或暂时只显示副官状态）。

## Phase 2: 感知与通信 (Awareness & Communication)
**目标**: 解决 "信息过载" 问题，让各智能体只看到自己关心的信息。

- [ ] **Task 2.1: 区域管理器 (Zone Manager)**
    - 创建 `openra_api/intel/zone_manager.py`。
    - 实现地图静态分析：识别矿区、路口、基地位置。
    - 实现 `MapPartition` 类，将坐标映射到 Zone ID。
- [ ] **Task 2.2: 动态热力图 (Heatmap)**
    - 在 `GlobalBlackboard` 中维护 `ZoneHeatmap`。
    - 实现定期更新逻辑：遍历所有单位，更新各 Zone 的敌我力量对比。
- [ ] **Task 2.3: 信号通信机制**
    - 定义标准信号类 `Signal` (sender, receiver, type, payload)。
    - 在 `GlobalBlackboard` 实现信号总线。

## Phase 3: 运营专家 (Economy Specialist)
**目标**: 剥离生产建设逻辑，独立运行。

- [ ] **Task 3.1: 资源监控器**
    - 专门从 `GameAPI` 读取资源、电力、生产队列状态。
    - 将结构化数据写入 `EconomyAgent` 的本地黑板。
- [ ] **Task 3.2: 生产循环 (Production Loop)**
    - 迁移原 `main.py` 中的 MacroActions 到 `EconomyAgent`。
    - 实现 "检查-生产-放置-重试" 的鲁棒逻辑。

## Phase 4: 战略与战术 (Strategy & Combat)
**目标**: 实现分层指挥与微操。

- [ ] **Task 4.1: 战略指挥官 (Strategy Agent)**
    - 基于热力图生成 `HighLevelPlan` (e.g., "Expand to Zone B", "Defend Zone A").
    - 实现编队逻辑 (`SquadManager`)，将闲置单位划分为 Squad。
- [ ] **Task 4.2: 战术指挥官 (Combat Agent)**
    - 设计动态 Agent 池：为每个活跃 Squad 创建一个临时的 `CombatAgent`。
    - 实现局部战斗微操 (Hit & Run)。

## 开发规约
1.  **不修改 `the-seed`**: 所有扩展通过继承或组合实现。
2.  **沟通交流**: 每个 Task 进行前，需要先讨论，增加具体细节后再开发。
3.  **增量提交**: 每个 Task 完成后需经过验证。
4.  **文档同步**: 随时更新本蓝图的状态。
5.  **代码热修复**: 及时修复 `the-seed` 中的 bug，并将修复的技术细节简要记录在 `REFACTOR_BLUEPRINT.md` 中。
