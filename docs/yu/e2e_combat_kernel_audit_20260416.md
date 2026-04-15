# E2E Combat / Kernel Audit — 2026-04-16

## Scope

Latest live E2E combat session:

- [session-20260415T203507Z/session.json](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T203507Z/session.json)

Main inspected tasks:

- [t_932cc010.jsonl](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T203507Z/tasks/t_932cc010.jsonl) — `家里这些兵去骚扰一下对面基地。`
- [t_1616b921.jsonl](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T203507Z/tasks/t_1616b921.jsonl) — `我被打了`
- [t_6f146462.jsonl](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T203507Z/tasks/t_6f146462.jsonl) — `defend_base`
- [t_75b81b11.jsonl](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T203507Z/tasks/t_75b81b11.jsonl) — `defend_base`
- [t_0c806f25.jsonl](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T203507Z/tasks/t_0c806f25.jsonl) — `趁着这个机会去反击。`

## Direct Answers

### Kernel 现在在做什么

Kernel 仍然是资源调度器和任务隔离层，不是要求每个 task 手工指定具体 actor 的“点兵器”。

代码证据：

- [task_agent/tools.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/task_agent/tools.py) 暴露 `request_units(...)`
- [task_agent/handlers.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/task_agent/handlers.py) 中普通 task 只有 `request_units`，没有 `produce_units`
- [kernel/resource_need_inference.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/kernel/resource_need_inference.py) 仍支持 generic combat / recon 资源申请

结论：

- 当前主方向仍是 “agent 提需求，Kernel 分配资源”
- 这轮 E2E 暴露的是这条语义在 combat 默认值上被写坏了

### agent 现在能不能看见空闲兵力

能。

代码证据：

- [kernel/runtime_projection.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/kernel/runtime_projection.py) 注入 `active_actor_ids` / `active_group_size`
- [task_agent/context.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/task_agent/context.py) 注入 `world_summary.military.idle_self_units`
- [adjutant/adjutant.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/adjutant/adjutant.py) 计算 `free_combat_units`

结论：

- 问题不是 “agent 完全看不见兵力”
- 问题是这些信号没有被稳定消费，或者被更坏的默认路径覆盖

### combat / recon 现在是否必须显式 actor_ids

不是必须。

代码证据：

- [task_agent/tools.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/task_agent/tools.py) 中 `attack` / `move_units` / `scout_map` 都把 `actor_ids` 标成 optional
- [task_agent/handlers.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/task_agent/handlers.py) 中 `_resolve_unit_actor_ids(...)` 优先复用 task-owned group
- [experts/combat.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/experts/combat.py) / [experts/recon.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/experts/recon.py) 在没有显式 actor_ids 时仍支持 generic 资源需求

结论：

- 系统没有彻底漂回“必须 actor_ids”
- 但 generic combat 的默认 contract 当前太激进

## Root Causes

### A. Generic combat 当前会退化成“全图抢兵”

最关键根因在这里。

代码证据：

- [experts/combat.py:104](/Users/kamico/work/theseed/THE-Seed-OpenRA/experts/combat.py:104)

当前行为：

- `CombatExpert.get_resource_needs()`
- 当 `actor_ids` 缺失且 `unit_count <= 0`
- 直接把 `count` 设成 `999`

这在资源语义上等于：

- “给我所有能打的单位”

日志证据：

- [t_6f146462.jsonl](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T203507Z/tasks/t_6f146462.jsonl)
- 任务 `defend_base` 启动 `CombatExpert(config={actor_ids:null, unit_count:0, engagement_mode:hold, ...})`
- 紧接着拿到 30+ 个 `resource_granted`
- 然后立即出现 `resource_lost: Missing 963 actor resource(s)`

这不是展示层问题，是资源需求本身写成了全局 claim。

### B. Adjutant 并行开战任务会放大这个 bug

同一 session 中同时出现这些 combat 相关任务：

- `#013` 骚扰敌方基地
- `#015` 我被打了
- `#018` defend_base
- `#019` 趁着这个机会去反击
- `#021` defend_base

并行任务本身不一定错。

但在 current combat semantics 下：

- 一个 auto/direct combat 任务只要走 `actor_ids=None, unit_count=0`
- 它就会把 Kernel generic allocation 拉满
- 其他 combat/recon 任务就只能被 revoke / 抢回 / 重分配

结论：

- Adjutant fan-out 是放大器
- generic combat global-claim 才是主根因

### C. TaskAgent 的 combat control loop 依然很浅

任务 `#013` 的形态很典型：

1. `request_units(category='infantry', count=10, min_start_package=5)`
2. `attack(target_position=[58,50], engagement_mode='harass')`
3. 后续大多数 wake 直接 `wait`

日志证据：

- [t_932cc010.jsonl](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T203507Z/tasks/t_932cc010.jsonl)

表现：

- 收到 `resource_lost`
- 收到 `risk_alert`
- 敌方数量变化
- 己方组规模下降

这些都没有触发有效 follow-up control。

最终 completion narrative 还过度声称“造成敌军损失”，但日志里没有足够强的敌军死亡证据支撑。

## Practical Meaning

### 不是 “必须回去手动指定 actor_id”

更准确的说法是：

- 系统同时存在 task-owned group 和 generic kernel allocation 两种语义
- 当前 generic combat 默认值写成了 “全拿”
- 所以你在 E2E 里感受到的是资源踩踏，而不是单纯的 actor-id burden

### 你想要的方向是对的

你说的目标：

- task 抬手就是一个 CombatTask
- agent 只说要什么兵、多少兵、做什么
- Kernel 负责匹配和分配

这个方向没有问题。

当前差的不是重做架构，而是先把这两层收口：

- generic combat allocation contract
- Adjutant 并行任务和 follow-up 合并策略

## Minimal Next Fixes

### Slice 1

修 generic combat 的默认语义。

要求：

- `actor_ids=None, unit_count=0` 不得再等于 `999`

安全方向：

- 若 task 已有 `active_actor_ids`，只用 task-owned group
- 若没有 owned group，则要求显式 `unit_count`
- 或者给 auto combat task 一个 bounded 默认值，而不是 all-available

### Slice 2

区分两类 combat 启动：

- follow-up combat on owned group
- new defensive/offensive task that still needs force assignment

前者可以直接复用 group。

后者必须：

- 有明确 bounded demand
- 或先 `request_units`

### Slice 3

提升 managed combat supervision loop。

最小闭环：

- `resource_lost`
- `risk_alert`
- `enemy discovered / disappeared`
- `group size materially changed`

这些不能再默认只回 `wait`。

### Slice 4

收紧 completion report truth。

要求：

- 战果表述必须基于明确敌军死亡/清场/目标消失证据
- 不能用 narrative 自行脑补“敌军损失惨重”

## Priority

建议顺序：

1. 修 combat global-claim
2. 修 auto combat bounded request contract
3. 修 combat supervision loop
4. 修 Adjutant 并行 combat merge / overlap

原因：

- 前两刀直接影响下一轮 E2E 稳定性
- 后两刀是行为质量提升，但必须建立在资源语义先稳定的前提上
