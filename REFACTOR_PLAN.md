# OpenRA 智能体重构计划 (REFACTOR_PLAN)

## 1. 核心架构：分层多智能体系统 (H-MAS)

本项目将从单体 FSM 架构重构为基于 **"副官-专家"** 的星形分层架构。所有 Agent 均基于 `the-seed` 框架，通过 **全局黑板 (Global Blackboard)** 进行协同。

### 1.1 总体拓扑
```text
玩家 (User)
  │ (自然语言指令)
  ▼
[ 副官 (Adjutant) ] ──────────┐
  │ (HighLevelGoal)           │
  │                           ▼
  │                  [ 全局黑板 (Global Blackboard) ]
  │                           ▲
  │                           │ (读取态势 / 写入需求)
  ├───────────────────────────┼──────────────────────────────┐
  ▼                           ▼                              ▼
[ 运营指挥官 ]           [ 战略指挥官 ]                 [ 战术指挥官 (动态) ]
(Economy Agent)         (Strategy Agent)               (Combat Agents)
```

### 1.2 通信与指挥机制
*   **指令下达 (Prompt Injection)**: 副官将玩家指令翻译后，作为 System Prompt 或 HighLevelGoal 注入到下属 Agent 的 Context 中。
*   **信息过滤 (Information Filtering)**:
    *   **战略层**: 仅接收热力图 (Heatmap) 和宏观部队统计。
    *   **战术层**: 仅接收指定区域 (Zone) 内的局部敌我列表。
    *   **目的**: 解决 Context 爆炸问题，确保每个 Agent 仅处理与其职能相关的信息。
*   **协同机制 (Blackboard Coordination)**:
    *   **无点对点通信**: Agent 之间不直接对话，所有请求（如“请求护航”）都写入黑板。
    *   **信号量**: 使用标准化的信号结构 (如 `EscortRequest`, `ResourceOpportunity`)。

---

## 2. 专家 Agent 设计与状态函数

### 2.1 战略指挥官 (Strategy Agent)
*   **职责**: 宏观兵力调配、地图控制、开矿决策。
*   **状态函数 (State Function)**:
    *   **区域热力图 (Zone Heatmap)**:
        *   **粒度**: 以矿区为核心，辅以地图关键节点（路口、桥梁、中心区）。
        *   **特征向量**: `[我方战力值, 敌方战力值, 资源价值, 探索度, 迷雾状态]`。
    *   **部队摘要**: `[闲置小队数量, 任务中小队状态]`。
*   **输出决策**:
    *   **编组 (Squading)**: 将闲置单位编组（建议使用内部逻辑管理编组，而非依赖游戏引擎的 `form_group`，更灵活）。
    *   **派遣 (Dispatch)**: 为 Squad 分配目标区域 (`Move To Zone A`) 和任务类型 (`Attack/Defend/Scout`)。
*   **API 需求**: `move_actor` (移动), `query_map` (地形), `fog_query` (迷雾)。

### 2.2 运营指挥官 (Economy Agent)
*   **职责**: 资源管理、建筑生产、科技攀升、基地扩张。
*   **状态函数 (State Function)**:
    *   **资源流**: `[资金, 资金增长率, 电力盈余]`。
    *   **生产线**: `[兵营队列阻塞, 重工队列阻塞, 科技树进度]`。
    *   **扩张机会**: 从黑板读取战略层标记的“安全高价值矿区”。
*   **启发式优化 (Heuristics)**:
    *   **目标**: 最大化 `Income` 同时最小化 `QueueIdleTime`。
    *   **算法**: LLM 生成优先级权重 -> 本地贪心算法计算最优 Build Order。
*   **API 需求**:
    *   **生产**: `start_production` (单位/建筑), `manage_production` (暂停/取消 - 用于纠错)。
    *   **建筑**: `place_building` (放置 - 需处理失败重试), `deploy` (基地车展开), `repair` (修理)。
    *   **集结**: `set_rally_point` (设置集结点)。
    *   **查询**: `query_production_queue`, `query_can_produce`。

### 2.3 战术指挥官 (Combat Agent - 动态实例)
*   **职责**: 执行具体的战斗任务，微操控制。
*   **生命周期**: 由战略 Agent 创建，绑定一个 Squad 和一个目标区域。
*   **状态函数 (State Function)**:
    *   **局部战场**: 仅包含目标区域 (Zone) 内的敌我单位详情 (`id`, `hp`, `type`, `pos`)。
    *   **克制矩阵**: `[UnitType A vs UnitType B: Advantage/Disadvantage]`。
*   **决策逻辑**:
    *   **配对优化**: 基于克制关系，生成最优 `Attack(AttackerID, TargetID)` 对。
    *   **微操**: 简单的 `Hit & Run` 或 `Focus Fire` (集火)。
*   **API 需求**: `attack` (攻击), `move_actor` (微操移动), `stop` (停火/卡位)。

---

## 3. 关键技术难点与解决方案

### 3.1 建筑放置与生产分离
*   **问题**: `start_production` 只是开始造，造完后需要 `place_building`。如果 `place_building` 失败（地形不平/有单位阻挡），会导致队列阻塞。
*   **解决方案**:
    1.  **预判**: 在 `start_production` 前，先用 `map_query` 检查目标位置地形。
    2.  **重试循环 (Retry Loop)**: 运营 Agent 需监控 `query_production_queue` 中的 `has_ready_item`。
    3.  **拥堵处理**: 如果 `place_building` 连续失败，调用 `manage_production(cancel)` 取消该建筑，避免卡死后续生产，或者尝试更换位置。

### 3.2 编队管理 (Squad Management)
*   **方案**: 推荐在 **Python 层** 实现软编队系统。
    *   **理由**: 游戏引擎的 `form_group` (Ctrl+1~9) 数量有限且状态不可控。
    *   **实现**: `Squad` 类维护一个 `List[ActorID]`。战略 Agent 分配任务时，直接传递 `Squad` 对象给战术 Agent。

### 3.3 态势感知降维
*   **方案**: **基于矿区的拓扑图 (Resource-based Topology)**。
    *   不扫描全图每个格子。
    *   仅扫描：`矿区中心点` + `基地中心点` + `主要路口`。
    *   以此构建稀疏的热力图，大幅降低 Token 消耗。

---

## 4. 开发路线图

### Phase 1: 基础设施 (Infrastructure)
1.  **Global Blackboard**: 实现支持多 Agent 读写的全局黑板。
2.  **Adjutant**: 实现指令解析与任务分发逻辑。
3.  **Zone Manager**: 实现地图区域划分与热力图计算基础算法。

### Phase 2: 运营专家 (Economy Specialist)
1.  实现资源与队列监控。
2.  实现“生产-放置”闭环逻辑（含失败重试）。
3.  接入启发式建造算法。

### Phase 3: 战略专家 (Strategy Specialist)
1.  实现基于热力图的宏观决策。
2.  实现软编队系统。
3.  实现扩张（开分矿）决策逻辑。

### Phase 4: 战术专家 (Combat Specialist)
1.  实现动态 Agent 生成工厂。
2.  实现局部战场信息提取与注入。
3.  实现基于克制的微操逻辑。
