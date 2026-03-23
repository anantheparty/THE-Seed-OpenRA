# Planner Expert Research

Date: 2026-04-01
Author: yu

## 1. Context

`design.md` §4 明确定义了三种 Expert：

- Information Expert
- Planner Expert
- Execution Expert

其中 Planner Expert 的职责是：

- **只给建议 / proposal**
- **不绑定资源**
- **不直接执行**
- **必须使用传统 AI（评分 / 搜索 / 规则），不用 LLM**

当前实现里，`query_planner` 已经暴露给 TaskAgent LLM，但 runtime 仍然是 stub：

- `task_agent/tools.py`：tool surface 已包含
  - `ReconRoutePlanner`
  - `AttackRoutePlanner`
  - `ProductionAdvisor`
- `task_agent/handlers.py`：`handle_query_planner()` 仍然只返回 `status="unimplemented"`
- `kernel/core.py`：`_tool_query_planner()` 也是同类 stub

这直接导致一个 live 问题：

- 在 `攻击敌人` 的无目标场景里，LLM 会调用空的 `ProductionAdvisor`
- 拿到无效 proposal 后继续自由发挥，漂到造 jeep / 扩产 / 补电 / Movement/Combat 乱串

所以 Planner 的问题不是“未来增强项”，而是**已经影响 live workflow 的当前缺口**。

## 2. 设计约束梳理

### 2.1 来自 `design.md`

Planner Expert 在设计中的定位：

- “给出候选方案/建议，不绑定资源，不直接执行”
- Task Agent 通过 `query_planner` 调用它
- 典型例子：
  - `ReconRoutePlanner`
  - `AttackRoutePlanner`
  - `ProductionAdvisor`

这意味着第一版实现不需要引入 Job lifecycle，也不需要接 GameLoop，只需要：

1. 有一个 PlannerExpert 的 concrete class
2. 有明确的 planner_type → planner instance 路由
3. 返回结构化 proposal

### 2.2 来自 `experts/base.py`

当前抽象类已经足够：

```python
class PlannerExpert(ABC):
    def plan(query_type: str, params: dict, world_state: dict) -> dict:
        ...
```

这说明第一版 Planner 不需要额外造新框架。只要：

- 把 `world_model.query(...)` 的必要结果整理成 `world_state`
- 在 planner 内做 rule/scoring
- 返回一个 proposal dict

即可落地。

### 2.3 来自 `task_agent/tools.py`

当前 `query_planner` 的 tool contract 很宽：

```json
{
  "planner_type": "ReconRoutePlanner|AttackRoutePlanner|ProductionAdvisor",
  "params": {...}
}
```

问题在于：

- **planner_type 已暴露 3 个，但 runtime 一个都没实现**
- 如果只先做 1 个，其他 2 个必须变成“明确 not_supported”，不能继续装作“未来会有”

否则 LLM 仍然会把它们当有效能力来试探。

## 3. RTS AI 中 Planner 的常见做法

这里关注的是**传统 AI** 路线，不是 RL/LLM。

### 3.1 生产/宏观规划（Production / Build Order）

RTS 里最成熟、最工程化的 Planner 方向其实是 build-order / macro planning。

常见方法：

1. **规则/优先级系统**
   - 最简单
   - 根据经济、科技前置、敌情、当前部队构成给单位/建筑打分
   - 优点：快、可解释、容易 live 调试
   - 缺点：策略空间有限

2. **Build-order search / branch-and-bound / heuristic planning**
   - 目标是找到满足目标组合的低 makespan 建造序列
   - 代表：BOSS（Build Order Search System）
   - 优点：适合“给定目标，求最优建造顺序”
   - 缺点：更适合完整 build-order 规划，不太适合我们当前这个“先给 TaskAgent 一个 tactical suggestion”场景

3. **在线自适应 build-order planning**
   - 根据观测到的对手策略动态调整
   - 可用 evolutionary planning / rule adaptation
   - 优点：适应性强
   - 缺点：实现复杂，超出当前第一版 Planner 的必要范围

从工程可行性看，**第一版应该落在规则+评分**，而不是一上来做完整 build-order search。

### 3.2 战术/进攻规划（Attack / Tactical Planning）

RTS 战术规划常见方法：

1. **Influence map / threat map + 路线评分**
   - 评估路径风险、侧翼机会、集火区域
   - 很适合 `AttackRoutePlanner`

2. **Script portfolio / tactical search**
   - 用一组战术脚本（rush / hold / kite / flank）做搜索或评分
   - 更偏 combat simulator / tactical sandbox

3. **Cluster / squad-level search**
   - 先把单位聚成小队，再在 squad level 搜索
   - 适合大规模战斗，不适合当前第一版

问题是：`AttackRoutePlanner` 真做对，**需要更强的地图语义和威胁表示**，而当前 WorldModel v1 还没有：

- chokepoint 语义
- 路线图
- influence map
- 威胁场
- 局部 combat simulation

所以现在做它，容易做成一个看似存在、实际价值有限的“假 Planner”。

### 3.3 侦察规划（Recon Route）

侦察路径规划常见方法：

1. 固定探点序列
2. 基于地图区域/起始点先验的评分
3. 基于未探索区 frontier 的启发式搜索
4. 基于敌方可能出生点 / tech timings 的 hypothesis-driven scouting

但当前系统里：

- `ReconExpert` 自己已经有 waypoint heuristic
- `WorldModel` 还没有 frontier/chokepoint/control-region 这类 richer map semantics

所以现在做 `ReconRoutePlanner`，会和 `ReconExpert` 现有逻辑高度重叠，收益不大。

## 4. 当前 repo 能支撑哪个 Planner 先做

### 4.1 `ReconRoutePlanner`

优点：

- 概念简单
- 和已有 `ReconExpert` 接得上

缺点：

- 和 `ReconExpert` 现有 waypoint heuristic 重叠太多
- 当前 WorldModel 对 frontier / unexplored cells / semantic regions 支撑不够
- 解决不了现在最痛的 live 问题

结论：

- **不建议作为第一个 Planner**

### 4.2 `AttackRoutePlanner`

优点：

- 从产品价值上很强
- 直接关系到“攻击敌人”这类用户命令

缺点：

- 当前 WorldModel 缺 threat map / chokepoint / route graph / terrain semantics
- 很容易做成“按敌方已知位置给一个 target_position”，这其实只是 query-world 的薄包装
- 测试也难做，因为没有足够强的地图/威胁语义作为输入

结论：

- **值得做，但不适合作为第一个 Planner**

### 4.3 `ProductionAdvisor`

优点：

- **直接命中当前最痛的 live 问题**
  - `攻击敌人` 无目标时，LLM 会调用 `ProductionAdvisor`
- **和当前 WorldModel 能力高度匹配**
  - 已有：
    - `economy`
    - `production_queues`
    - `my_actors`
    - `enemy_actors`
    - `world_summary`
- **可以完全用规则/评分实现**
  - 不需要 search framework
  - 不需要 richer map semantics
  - 容易写 deterministic tests
- **输出可以非常有约束**
  - “继续生产什么”
  - “先补什么前置”
  - “此刻不建议生产，应该先侦察”

缺点：

- 如果 contract 设计不好，容易让 TaskAgent 把它当“万能战略脑”

结论：

- **我建议第一个实现 `ProductionAdvisor`**

## 5. 为什么 `ProductionAdvisor` 最适合先做

一句话结论：

- **它是唯一一个同时满足“能解决当前 live 痛点 / 现有数据面够用 / 可以做成高质量传统 AI”的 Planner。**

更具体地说：

1. **它直接填当前 live 缺口**
   - 现在 `query_planner(ProductionAdvisor)` 已经在被调用
   - 如果不实现，LLM 会继续拿着 stub 自由发挥

2. **它天然适合 rule/scoring**
   - 生产建议本来就是规则化很强的领域
   - 比进攻路线规划更容易保证 deterministic 和可解释

3. **它对当前 WorldModel 要求最低**
   - 不需要新地图语义
   - 不需要 combat simulator
   - 不需要 pathfinding abstraction

4. **它可以自然输出“不要生产，先侦察”**
   - 这点对当前 `攻击敌人` 无目标场景尤其关键
   - 也就是说，ProductionAdvisor 不一定要建议“造什么”，它也可以建议：
     - `strategy = scout_first`
     - `reason = no_visible_enemy`
     - `recommended_expert = ReconExpert`

## 6. 建议的第一版 contract

我建议第一版 `ProductionAdvisor` 的 proposal 不要做成自由文本，而是一个很窄的结构：

```python
{
  "planner_type": "ProductionAdvisor",
  "status": "ok",
  "recommendation": {
    "action": "produce" | "tech_up" | "scout_first" | "hold",
    "unit_type": "e1" | "2tnk" | "jeep" | None,
    "queue_type": "Infantry" | "Vehicle" | "Building" | None,
    "count": 1 | 2 | 3 | None,
    "prerequisites": ["weap", "powr"] | [],
    "reason": "no_visible_enemy" | "low_power" | "queue_blocked" | "need_mobile_scout" | ...
  },
  "alternatives": [...],
  "timestamp": ...
}
```

这个 contract 的关键点：

- 它是**建议**，不是命令
- 它允许 Planner 返回 `scout_first`
- 它允许 Planner 返回 `hold`
- 它不直接创造 Job，不绕过 TaskAgent

## 7. 第一版规则建议

第一版 `ProductionAdvisor` 不需要太复杂，可以先做成 deterministic rule stack：

### 输入

- `world_summary`
- `economy`
- `production_queues`
- `my_actors`
- `enemy_actors`
- `params`（由 TaskAgent 传入）

### 建议规则（第一版）

优先级顺序建议：

1. **如果无可见敌人，且玩家请求攻击/进攻**
   - 返回 `action="scout_first"`
   - `reason="no_visible_enemy"`

2. **如果低电**
   - 返回 `action="tech_up"` / `unit_type="powr"` / `queue_type="Building"`
   - `reason="low_power"`

3. **如果生产队列阻塞**
   - 返回 `action="hold"`
   - `reason="queue_blocked"`

4. **如果有战车工厂且需要机动侦察**
   - 返回 `action="produce"` / `unit_type="jeep"` / `queue_type="Vehicle"` / `count=1`

5. **如果已有可用攻击单位足够**
   - 返回 `action="hold"`
   - `reason="sufficient_force"`

核心原则：

- 第一版只做**高价值的保守建议**
- 不做全局 build-order 优化
- 不做长链多步战略规划

## 8. 实现建议

### 文件落点

建议新增：

- `experts/planners.py`

内容：

- `ProductionAdvisor(PlannerExpert)`
- 可选：一个小的 planner registry / factory

### 接线

1. `task_agent/handlers.py`
   - 用 planner registry 替换 stub
   - 未实现 planner 返回明确 `not_supported`

2. `kernel/core.py`
   - `_tool_query_planner()` 也应走同一 planner surface
   - 不要继续保留第二套 stub 逻辑

3. 测试
   - `tests/test_planners.py`
   - `tests/test_tool_handlers.py`

## 9. 需要注意的风险

### 风险 1：Planner 过度自由

如果 proposal 结构太松，TaskAgent 仍会把 Planner 当成“弱 LLM”，然后继续漂。

解决：

- 返回窄结构
- 限定 action 枚举

### 风险 2：把 Planner 做成半个执行器

Planner 不应直接调用 GameAPI，不应创建 Job。

解决：

- 严格遵守 `PlannerExpert.plan(...) -> proposal dict`

### 风险 3：把 ProductionAdvisor 做成“全局战略脑”

第一版不要试图直接解决所有宏观问题。

解决：

- 只覆盖当前 live 高价值场景
- 只做 deterministic rules

## 10. Recommendation

我的建议：

### 先实现：`ProductionAdvisor`

理由：

- 直接解决当前 live 最痛的问题
- 数据面已经足够
- 最容易用传统 AI 做成可解释、可测、稳定的第一版

### 暂不先做：`AttackRoutePlanner`

理由：

- 战略价值高，但当前地图/威胁语义还不够
- 容易做成价值有限的假 Planner

### 暂不先做：`ReconRoutePlanner`

理由：

- 会和现有 `ReconExpert` waypoint heuristic 重叠
- 不解决最痛的问题

## 11. Implementation Scope I Recommend for Approval

如果 Wang 同意，我建议实现范围严格收在：

1. `experts/planners.py`
   - `ProductionAdvisor`

2. `task_agent/handlers.py`
   - `query_planner` 从 stub 改为真实路由

3. `kernel/core.py`
   - `_tool_query_planner()` 对齐同一套 planner 逻辑

4. tests
   - `tests/test_planners.py`
   - 更新 `tests/test_tool_handlers.py`

并且第一版只覆盖：

- `no_visible_enemy -> scout_first`
- `low_power -> build power`
- `queue_blocked -> hold`
- `need_mobile_scout -> produce jeep`

不做额外 Planner，不扩到完整战略层。
