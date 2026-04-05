# Capability Task 架构设计

**作者**: Wang | **日期**: 2026-04-06 | **状态**: 当前主线 — Phase 1 实现中（Xi），Phase 0 C# API 已完成

---

## 1. 核心思想

当前系统的生产问题：每个 TaskAgent 各自调 `produce_units` → 多个 LLM 并发争抢同一个生产队列，互相不知道对方在造什么，导致重复建造、资源浪费、优先级混乱。

**解决方案：三层职责分离**

| 角色 | 职责 | 不管什么 |
|------|------|---------|
| **EconomyCapability** | 全局经济规划 + 主动基建 | 不管分配、不管具体请求的快速响应 |
| **Kernel** | 接收请求 → 即时 idle 匹配 → fast-path 生产 → 按优先级分配 | 不做经济规划 |
| **普通 TaskAgent** | `request_units` → 阻塞等待 → 拿到单位继续工作 | 不知道生产过程 |

```
┌──────────────────────────────────────────────────────────────────┐
│  Task#001 "进攻"     Task#002 "探索"     Task#003 "防守"         │
│    │ request(tank×5)   │ request(scout×1)   │ request(inf×10)    │
│    │ (阻塞等待...)      │ (阻塞等待...)       │ (阻塞等待...)      │
│    └────────┬──────────┴─────────┬──────────┘                    │
│             ▼                    ▼                                │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │                   Kernel (确定性)                          │   │
│  │  1. idle 匹配 → 有就直接 bind，返回成功                    │   │
│  │  2. 没有但可造 → bootstrap EconomyJob → notify Capability  │   │
│  │  3. 出厂后自动分配 → 唤醒等待中的 agent                    │   │
│  └─────────────────────────┬─────────────────────────────────┘   │
│                            │ (全局缺口可见)                       │
│                            ▼                                     │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │              EconomyCapability (持久 LLM Task)             │   │
│  │  - 全局经济规划（电力/矿/科技/扩张）                        │   │
│  │  - 看到 Kernel 已 bootstrap 的生产，避免重复                │   │
│  │  - 玩家经济指令直接 merge 到这里                            │   │
│  │  - 补充 Kernel fast-path 无法覆盖的复杂决策                 │   │
│  └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. 普通 TaskAgent 视角（极简）

### 2.1 唯一的生产相关 tool：`request_units`

```python
request_units(
    category="vehicle",     # infantry / vehicle / aircraft
    count=5,
    urgency="high",         # low / medium / high / critical
    hint="重型坦克，用于正面突破"
)
```

**返回两种结果之一：**
- `{"status": "fulfilled", "actor_ids": [45, 46, 47, 48, 49]}` — 直接 bind 成功（idle 匹配命中）
- `{"status": "waiting", "request_id": "REQ-001"}` — 需要等待生产

### 2.2 阻塞语义

`request_units` 返回 `waiting` 后，**agent 的 wake 循环暂停**，直到 request 被完整 fulfill。

- Agent 不会被 wake，不消耗 LLM token
- Request fulfilled 后 Kernel 唤醒 agent，下一轮 wake 的 context 里直接告诉结果："你请求的 5 辆坦克已到位"
- Agent 不需要 `check_request_status`，不需要轮询，不需要理解生产流程

### 2.3 批量请求

Agent 的 system prompt 要求：**如果有多个 request，一次性全部发出。**

```
# TaskAgent System Prompt 追加

## 单位请求
- 用 request_units 请求单位。你不能自己生产。
- request 可能立即成功（idle 单位匹配）或需要等待（生产中）。
- 等待期间你会暂停，不需要做任何事。
- 如果你需要多种单位，在同一轮全部请求，不要分多轮。

示例（一轮发出所有请求）：
  request_units(category="vehicle", count=5, urgency="high", hint="重坦进攻")
  request_units(category="infantry", count=3, urgency="medium", hint="步兵占点")
```

并行 tool call 机制已有（asyncio.gather），多个 request_units 会同时提交到 Kernel。

### 2.4 Agent 不知道的事

Agent **看不到也不需要知道**：
- 生产队列状态
- 谁在造什么
- EconomyCapability 的存在
- Kernel 的 fast-path 机制
- idle 匹配和 bootstrap 的区别

对 agent 来说就是："我要 5 辆坦克" → 等一会 → "你的坦克到了"。

---

## 3. Kernel 请求处理（确定性，三步）

### 3.1 Step 1：idle 匹配

```python
def _try_fulfill_from_idle(self, req: UnitRequest) -> bool:
    """尝试用场上 idle 单位直接满足请求。"""
    idle = self.world_model.find_actors(
        owner="self", idle_only=True, unbound_only=True,
        category=req.category,
    )
    # 按 hint 偏好排序
    idle.sort(key=lambda a: _hint_match_score(a, req.hint), reverse=True)
    
    to_bind = idle[:req.count - req.fulfilled]
    for actor in to_bind:
        self._bind_to_request(req, actor)
    
    return req.fulfilled >= req.count  # 完全满足？
```

如果 idle 能完全满足 → 返回 `fulfilled`，agent 不暂停。

### 3.2 Step 2：fast-path bootstrap 生产

idle 不够 → Kernel 直接启动 EconomyJob（direct-managed, skip_agent=True）：

```python
def _bootstrap_production_for_request(self, req: UnitRequest) -> None:
    """不够 idle → 直接 bootstrap 生产，不等 Capability LLM。"""
    remaining = req.count - req.fulfilled
    if remaining <= 0:
        return
    
    # 根据 category + hint 推断具体 unit_type
    unit_type, queue_type = self._infer_unit_type(req.category, req.hint)
    if unit_type is None:
        return  # 无法推断，留给 Capability 处理
    
    # 检查是否可造
    if not self.world_model.can_produce(unit_type):
        return  # 不可造，留给 Capability 处理（可能需要先建前置建筑）
    
    # bootstrap EconomyJob
    config = EconomyJobConfig(unit_type=unit_type, count=remaining, queue_type=queue_type)
    self._start_direct_job(req.request_id, "EconomyExpert", config)
    
    # 通知 Capability（避免重复生产）
    if self._capability_task_id:
        self.inject_player_message(
            self._capability_task_id,
            f"[Kernel fast-path] 已为 Task#{req.task_label} 启动生产: "
            f"{unit_type}×{remaining} (REQ-{req.request_id})",
        )
```

**`_infer_unit_type` 推断逻辑**：
- hint 中有具体单位名（"重坦"→3tnk, "步兵"→e1）→ 使用 PortableIntentModel 或 knowledge 模块解析
- hint 模糊（"战斗单位"）→ 按 category 选默认（vehicle→3tnk, infantry→e1）
- 无法推断 → 返回 None，留给 Capability

### 3.3 Step 3：自动分配

单位出厂后（PRODUCTION_COMPLETE event）→ `_fulfill_unit_requests()`：

```python
def _fulfill_unit_requests(self) -> None:
    """扫描 idle 单位，按优先级匹配 pending requests。"""
    idle = self.world_model.find_actors(
        owner="self", idle_only=True, unbound_only=True,
    )
    if not idle:
        return
    
    pending = sorted(
        [r for r in self._unit_requests.values()
         if r.status in ("pending", "partial")],
        key=lambda r: (
            -_URGENCY_WEIGHT[r.urgency],
            -self.tasks[r.task_id].priority,
            r.created_at,
        ),
    )
    
    for req in pending:
        remaining = req.count - req.fulfilled
        if remaining <= 0:
            continue
        matched = [a for a in idle if _actor_matches_category(a, req.category)]
        matched.sort(key=lambda a: _hint_match_score(a, req.hint), reverse=True)
        for actor in matched[:remaining]:
            self._bind_to_request(req, actor)
            idle.remove(actor)
        
        # 如果 request 完全满足 → 唤醒等待中的 agent
        if req.fulfilled >= req.count:
            req.status = "fulfilled"
            self._wake_waiting_agent(req.task_id)
        
        if not idle:
            break
```

### 3.4 Agent 阻塞 / 唤醒机制

```python
def _suspend_agent_for_requests(self, task_id: str) -> None:
    """如果该 task 有 waiting 的 request → 暂停 agent wake。"""
    has_waiting = any(
        r.status in ("pending", "partial")
        for r in self._unit_requests.values()
        if r.task_id == task_id
    )
    if has_waiting:
        agent = self._agents.get(task_id)
        if agent:
            agent.suspend()  # 暂停 wake 循环

def _wake_waiting_agent(self, task_id: str) -> None:
    """该 task 所有 request 都 fulfilled → 恢复 agent wake。"""
    all_fulfilled = all(
        r.status in ("fulfilled", "cancelled")
        for r in self._unit_requests.values()
        if r.task_id == task_id
    )
    if all_fulfilled:
        agent = self._agents.get(task_id)
        if agent:
            agent.resume_with_event(Event(
                type=EventType.UNIT_ASSIGNED,
                data={"message": "所有请求的单位已到位"},
            ))
```

---

## 4. EconomyCapability 设计

### 4.1 职责（精简）

Capability **不参与请求的快速响应**（Kernel fast-path 处理）。Capability 负责：

1. **全局经济规划** — 电力平衡、矿场/矿车扩张、科技升级路线
2. **补充 Kernel 无法处理的缺口** — Kernel fast-path 失败（不可造/无法推断 unit_type）的请求
3. **玩家经济指令** — "发展经济"、"爆兵"、"多建电厂" 直接 merge 到 Capability
4. **主动优化** — 看到 Kernel bootstrap 的生产后，判断是否需要调整（比如"全在造坦克但电不够了"）

### 4.2 Info Prompt

```
[economy]
资金: 2340 / 电力: +50 / 矿车: 2辆活跃

[active_production]  ← Kernel bootstrap 的 + Capability 自己安排的
Vehicle: 3tnk×4 (Kernel fast-path, for Task#001)
Building: powr×1 (Capability, 主动补电)
Infantry: 空闲

[unfulfilled_requests]  ← Kernel 无法 fast-path 的请求
REQ-005 from Task#004("空袭") — aircraft ×2, high, "对地攻击机" — 原因: 无机场，无法生产

[buildable_units]
Infantry: e1, e3 | Vehicle: 3tnk, v2rl, harv | Building: powr, barr, proc, weap

[player_messages]  ← 玩家直接经济指令
2s ago: "多建几个电厂"
```

### 4.3 Tool Set

```python
CAPABILITY_TOOLS = [
    "produce_units",          # 生产单位（现有）
    "query_world",            # 查询状态（现有）
    "query_planner",          # ProductionAdvisor（现有）
    "update_subscriptions",   # 订阅 info feeds（现有）
]
```

### 4.4 System Prompt

```
你是 EconomyCapability，RTS 游戏的经济规划总管。

## 职责
1. 全局经济规划：电力平衡、矿场扩张、科技升级
2. 处理 Kernel 无法自动生产的请求（[unfulfilled_requests]）
3. 响应玩家经济指令（[player_messages]）
4. 监督已有生产（[active_production]），必要时补充/调整

## 你不需要做的
- 不需要分配单位（Kernel 自动处理）
- 不需要响应简单生产请求（Kernel fast-path 已处理）
- 不需要 complete_task（你是持久任务）

## 决策优先级
1. [unfulfilled_requests] 中的请求（需要你建前置建筑或选择单位类型）
2. [player_messages] 中的指令
3. 电力不足 → 建电厂
4. 矿车不足 → 造矿车
5. 科技升级 → 按局势判断
```

### 4.5 唤醒策略

| 触发 | 机制 |
|------|------|
| Kernel fast-path 失败（有 unfulfilled request） | push UNIT_REQUEST_UNFULFILLED event |
| 玩家经济指令 merge | Adjutant inject_player_message |
| Kernel bootstrap notify | inject_player_message（只通知，不唤醒） |
| 定期心跳（有 unfulfilled requests 时） | 5s periodic wake |

**无 unfulfilled requests + 无玩家指令 → sleep。**

多数简单生产请求被 Kernel fast-path 处理，Capability 只在需要复杂决策（建前置建筑、选择科技路线、玩家指挥）时才 wake。

---

## 5. 玩家指令路由

### 5.1 NLU 具体生产命令

"造5辆坦克"、"建3个电厂" → NLU 命中 → direct EconomyJob + notify Capability

```python
# adjutant.py
job = self._start_direct_job(text, "EconomyExpert", config)
if self.kernel._capability_task_id:
    self.kernel.inject_player_message(
        self.kernel._capability_task_id,
        f"[NLU直达] 玩家命令已执行: {text}",
    )
```

### 5.2 玩家模糊经济/爆兵指令

"发展经济"、"爆兵"、"多建电厂"、"全力发展科技" → **直接 merge 到 Capability**

Adjutant 分类时，经济/生产类 command → disposition=merge → target=EconomyCapability。

Capability 收到 [player_messages] 后自行决策（爆兵 → 全队列满载步兵+坦克，发展经济 → 均衡基建等）。

### 5.3 路由总结

| 玩家输入 | NLU? | 路由 | 处理方 |
|---------|------|------|-------|
| "造5辆坦克" | Yes | direct job + notify | NLU → EconomyJob |
| "爆兵" | No | merge → Capability | Capability LLM |
| "发展经济" | No | merge → Capability | Capability LLM |
| "多建电厂" | Partial | merge → Capability | Capability LLM |
| "全力进攻" | No | new Task | TaskAgent → request_units |
| "基地被打了" | No | interrupt Task | TaskAgent → request_units(critical) |
| "停止生产" | No | merge → Capability | Capability 暂停队列 |

---

## 6. 数据模型

### 6.1 UnitRequest

```python
@dataclass
class UnitRequest:
    request_id: str
    task_id: str
    task_label: str
    task_summary: str
    category: str                  # infantry / vehicle / aircraft / building
    count: int
    urgency: str                   # low / medium / high / critical
    hint: str
    fulfilled: int = 0
    status: str = "pending"        # pending / partial / fulfilled / cancelled
    bootstrap_job_id: Optional[str] = None  # Kernel fast-path 创建的 job
    created_at: float = field(default_factory=time.time)
```

### 6.2 新增 EventType

```python
class EventType(str, Enum):
    ...
    UNIT_REQUEST_UNFULFILLED = "UNIT_REQUEST_UNFULFILLED"  # → 唤醒 Capability
    UNIT_ASSIGNED = "UNIT_ASSIGNED"                        # → 唤醒等待中的 agent
```

### 6.3 Task 新增字段

```python
@dataclass
class Task:
    ...
    is_capability: bool = False    # 保护标记：不可 override/cancel
```

---

## 7. Kernel 新增接口

```python
class Kernel:
    # 新增状态
    self._unit_requests: dict[str, UnitRequest] = {}
    self._capability_task_id: Optional[str] = None
    
    def register_unit_request(
        self, task_id: str, category: str, count: int,
        urgency: str, hint: str,
    ) -> dict:
        """注册请求 → idle 匹配 → fast-path 生产 → 返回结果。
        
        Returns:
            {"status": "fulfilled", "actor_ids": [...]}  — 直接满足
            {"status": "waiting", "request_id": "..."}   — 需要等待
        """
    
    def cancel_unit_request(self, request_id: str) -> bool:
        """取消请求。"""
    
    def list_unit_requests(self, status: Optional[str] = None) -> list[UnitRequest]:
        """列出请求（Capability 查询用）。"""
    
    # cancel_task 修改
    def cancel_task(self, task_id: str, ...):
        # ... 已有逻辑 ...
        # 新增：取消该 task 的所有 pending 请求
        for req in self._unit_requests.values():
            if req.task_id == task_id and req.status in ("pending", "partial"):
                req.status = "cancelled"
```

---

## 8. 场景推演

### 场景 A：idle 命中 → 秒级返回

```
Task#001 "进攻东部":
  → request_units(vehicle, 5, high, "重坦") 
  → Kernel: 场上有 5 辆 idle 3tnk
  → 直接 bind → 返回 fulfilled + actor_ids
  → Agent 不暂停，立即 attack

延迟：~0ms（无 LLM，纯 Kernel 操作）
```

### 场景 B：idle 不足 → fast-path 生产 → 自动分配

```
Task#001 "进攻东部":
  → request_units(vehicle, 5, high, "重坦")
  → Kernel: 2 辆 idle 3tnk → bind 2 辆（partial）
  → 还差 3 → _infer_unit_type("vehicle", "重坦") → 3tnk
  → can_produce(3tnk) → True
  → bootstrap EconomyJob(3tnk ×3)
  → notify Capability: "[fast-path] 3tnk×3 for Task#001"
  → 返回 waiting → Agent 暂停

... 3 辆坦克陆续出厂 ...
  → _fulfill_unit_requests → bind 给 REQ-001
  → REQ-001 fulfilled → 唤醒 Agent

Task#001 wake:
  context: "你请求的 5 辆重坦已到位 (actor: 45,46,47,48,49)"
  → attack(target)

延迟：~生产时间（EconomyJob tick，无 LLM 等待）
```

### 场景 C：不可造 → Capability 介入

```
Task#004 "空袭":
  → request_units(aircraft, 2, high, "对地攻击机")
  → Kernel: 0 idle aircraft
  → _infer_unit_type → mig (米格)
  → can_produce(mig) → False（无机场）
  → fast-path 失败 → push UNIT_REQUEST_UNFULFILLED → 唤醒 Capability
  → 返回 waiting → Agent 暂停

EconomyCapability wake:
  [unfulfilled_requests]: aircraft ×2, "对地攻击机" — 无机场
  → 需要先建机场
  → produce_units("afld", 1, "Building")
  ... 机场建成 ...
  → produce_units("mig", 2, "Aircraft")
  ... 米格出厂 → Kernel 分配 → Agent 唤醒 ...
```

### 场景 D：多任务竞争

```
Task#001 request(vehicle ×5, high)    → Kernel bootstrap 3tnk×5
Task#003 request(infantry ×10, critical) → Kernel bootstrap e1×10

步兵先出厂 → Kernel 分配:
  REQ-002 (critical) 先满足 → 步兵全给 Task#003 → Task#003 唤醒

坦克出厂 → Kernel 分配:
  REQ-001 (high) → 坦克给 Task#001 → Task#001 唤醒
```

### 场景 E：玩家"爆兵"

```
玩家: "爆兵"
  → Adjutant: NLU 未命中 → LLM classify → command, disposition=merge → EconomyCapability
  → inject_player_message(capability_task_id, "爆兵")

EconomyCapability wake:
  [player_messages]: "爆兵"
  → produce_units("e1", 10, "Infantry")
  → produce_units("3tnk", 5, "Vehicle")
  → 全队列满载

出厂单位 → Kernel _fulfill_unit_requests:
  → 有 pending request 的先分配
  → 无 request 的保持 idle（等 task claim）
```

### 场景 F：批量请求

```
Task#001 "全面进攻" (一轮发出 3 个 request):
  → request_units(vehicle, 5, high, "重坦主力")
  → request_units(infantry, 3, medium, "步兵跟进")
  → request_units(vehicle, 2, medium, "火箭车支援")

Kernel:
  → idle 满足部分
  → fast-path 生产剩余
  → Agent 暂停

... 全部 fulfilled ...
  → 所有 3 个 request 完成 → 唤醒 Agent
  → Agent 一次性拿到所有单位
```

---

## 9. C# 侧新增 API：query_producible_items

### 9.1 背景

当前 Python 侧 `_derive_buildable_units()` 是硬编码推断（从建筑计数推导可造单位），不准确且维护成本高。C# 侧 `ProductionQueue.BuildableItems()` 已有完整实现，考虑了科技树、前置条件、阵营限制。需要新增一个 query API 暴露它。

### 9.2 C# 实现

**文件**: `OpenCodeAlert/OpenRA.Mods.Common/ServerCommands.cs`

新增 `QueryProducibleItemsCommand`，注册为 `query_producible_items`：

```csharp
public static JObject QueryProducibleItemsCommand(JObject json, World world)
{
    var player = ResolvePlayer(json, world);
    var result = new JObject();
    
    // 遍历所有队列类型
    var queues = world.ActorsWithTrait<ProductionQueue>()
        .Where(a => a.Actor.Owner == player && !a.Actor.IsDead && a.Trait.Enabled)
        .Select(a => a.Trait);
    
    var grouped = new Dictionary<string, JArray>();
    
    foreach (var queue in queues)
    {
        var queueType = queue.Info.Type;  // "Building", "Infantry", "Vehicle", etc.
        if (!grouped.ContainsKey(queueType))
            grouped[queueType] = new JArray();
        
        foreach (var item in queue.BuildableItems())
        {
            var name = item.Name.ToLowerInvariant();
            // 避免重复
            if (grouped[queueType].Any(t => t["name"]?.ToString() == name))
                continue;
            
            var tooltip = item.TraitInfoOrDefault<TooltipInfo>();
            var valued = item.TraitInfoOrDefault<ValuedInfo>();
            var buildable = item.TraitInfoOrDefault<BuildableInfo>();
            
            var entry = new JObject
            {
                ["name"] = name,
                ["display_name"] = tooltip?.Name ?? name,
                ["cost"] = valued?.Cost ?? 0,
                ["build_duration"] = buildable?.BuildDuration ?? 0,
                ["prerequisites"] = new JArray(
                    buildable?.Prerequisites?.Select(p => p) ?? Array.Empty<string>()
                ),
            };
            grouped[queueType].Add(entry);
        }
    }
    
    foreach (var kv in grouped)
        result[kv.Key] = kv.Value;
    
    return result;
}
```

注册（WorldLoaded）：
```csharp
w.CopilotServer.QueryHandlers["query_producible_items"] = QueryProducibleItemsCommand;
```

### 9.3 返回格式

```json
{
    "Building": [
        {"name": "powr", "display_name": "Power Plant", "cost": 300, "build_duration": 250, "prerequisites": []},
        {"name": "barr", "display_name": "Barracks", "cost": 300, "build_duration": 350, "prerequisites": ["powr"]},
        ...
    ],
    "Infantry": [
        {"name": "e1", "display_name": "Rifle Infantry", "cost": 100, "build_duration": 125, "prerequisites": ["barr"]},
        ...
    ],
    "Vehicle": [
        {"name": "3tnk", "display_name": "Heavy Tank", "cost": 1150, "build_duration": 600, "prerequisites": ["weap"]},
        ...
    ]
}
```

### 9.4 Python 侧对接

**GameAPI 新增方法**:
```python
def query_producible_items(self) -> dict[str, list[dict]]:
    """查询所有队列当前可造单位列表。"""
    response = self._send_request('query_producible_items', {})
    return self._handle_response(response, "查询可造单位失败")
```

**WorldModel 替换 `_derive_buildable_units`**:
- `compute_runtime_facts()` 中 `buildable` 字段改为从 GameAPI 真实数据获取
- 刷新频率：跟随 economy refresh（~2s），不需要每 tick
- `_derive_buildable_units` 保留为 fallback（GameAPI 断连时）

### 9.5 受益方

| 消费者 | 当前 | 改进后 |
|--------|------|--------|
| EconomyCapability | 硬编码推断 | 真实可造列表 + cost + 前置条件 |
| Kernel fast-path | _infer_unit_type 猜测 | 查 producible_items 验证后再 bootstrap |
| 普通 TaskAgent context | 不完整的 buildable 列表 | 不再需要（不直接生产） |
| NLU 路由 | can_produce 逐个查询 | 批量查一次缓存即可 |

---

## 10. 实现计划

### Phase 0: C# API — query_producible_items（Wang 负责）

1. `OpenCodeAlert/OpenRA.Mods.Common/ServerCommands.cs` — 新增 `QueryProducibleItemsCommand`
2. `OpenCodeAlert/OpenRA.Mods.Common/ServerCommands.cs` — WorldLoaded 注册
3. `openra_api/game_api.py` — `query_producible_items()` 方法
4. `world_model/core.py` — `compute_runtime_facts()` buildable 字段改用真实 API 数据

### Phase 1~4: Capability Task 全部实现（Xi 负责）

**Phase 1: 数据模型 + Kernel 请求机制（~250 行）**
1. `models/core.py` — `UnitRequest` dataclass + `Task.is_capability`
2. `models/enums.py` — `UNIT_REQUEST_UNFULFILLED`, `UNIT_ASSIGNED`
3. `kernel/core.py` — `_unit_requests`, `_capability_task_id`
4. `kernel/core.py` — `register_unit_request()` (idle 匹配 + fast-path bootstrap)
5. `kernel/core.py` — `_fulfill_unit_requests()` 自动分配算法
6. `kernel/core.py` — `_infer_unit_type()` hint → 具体单位（查 producible_items 验证）
7. `kernel/core.py` — agent suspend/wake 机制（request waiting ↔ fulfilled）
8. `kernel/core.py` — PRODUCTION_COMPLETE / unbind / cancel_task 触发分配
9. `kernel/core.py` — `cancel_task()` 请求清理

**Phase 2: 普通 TaskAgent 变更（~80 行）**
1. `task_agent/tools.py` — 移除 `produce_units`，新增 `request_units`
2. `task_agent/handlers.py` — `handle_request_units` 对接 Kernel
3. `task_agent/agent.py` — System prompt 更新 + suspend/resume 支持
4. `task_agent/context.py` — fulfilled request 结果注入 wake context

**Phase 3: EconomyCapability（~150 行）**
1. `task_agent/tools.py` — `CAPABILITY_TOOL_DEFINITIONS`
2. `task_agent/agent.py` — `is_capability` 选择 tool set + system prompt
3. `task_agent/context.py` — `[unfulfilled_requests]` + `[active_production]` + `[economy]` info block
4. Capability 自动创建逻辑

**Phase 4: Adjutant 集成 + 测试（~120 行）**
1. `adjutant/adjutant.py` — NLU notify Capability; 经济指令 merge to Capability
2. `adjutant/adjutant.py` — `is_capability` 保护
3. 测试：idle 匹配 / fast-path 生产 / 自动分配 / 阻塞唤醒 / 多优先级竞争 / Capability wake

**总改动量：~700 行（Phase 0 ~100 行 + Phase 1-4 ~600 行）**

---

## 10. 开放问题

### Q1: _infer_unit_type 推断失败率？
hint 是自然语言，推断可能不准。**建议**：用已有的 `production_names.py` + `knowledge.py` 做匹配，匹配失败时留给 Capability。实际上大部分请求是"坦克"、"步兵"这种明确类别，推断成功率应该很高。

### Q2: 建筑请求？
TaskAgent 能否 `request_units(category="building", hint="科技中心")`？**建议可以**，Kernel 同样 fast-path 处理。但复杂建筑链（需要先建 A 才能建 B）留给 Capability。

### Q3: Bootstrap 阶段（开局）？
Capability 在建造厂建成后启动。开局 MCV 部署由 NLU/普通 task 处理。

### Q4: 多个 Capability？
先只实现 EconomyCapability。未来可扩展 `is_capability` 为 `capability_type: Optional[str]`。
