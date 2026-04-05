# Adjutant 深度重设计：意图处置模型 + 信息 Prompt

**作者**: Wang | **日期**: 2026-04-06 | **状态**: 核心已落地（NLU 前置 + disposition + capability merge），Adjutant 现为 top-level coordinator

---

## 1. 问题诊断

当前 Adjutant 的核心缺陷：**每条指令都创建新 Task，无论场上已有什么。**

### 1.1 现象

```
玩家: "造5辆坦克"           → Task#001 (create_task)
玩家: "再多造几辆"          → Task#002 (create_task) ← 应该转发给 #001
玩家: "发展经济"            → Task#003 (create_task)
玩家: "全力进攻"            → Task#004 (create_task) ← 应该覆盖 #003
玩家: "敌人在左下角"        → Task#005 (create_task) ← 应该转发给 #001 或 recon task
```

5 句话 → 5 个独立 Task Agent → 5 个 LLM 循环 → 互相争抢资源。

### 1.2 根因分析

| 缺陷 | 当前行为 | 应有行为 |
|------|---------|---------|
| 无意图处置判断 | command → 必创建 | command → merge/override/interrupt/new 四选一 |
| 无消息转发 | info → 创建新 task | info → 注入到相关 running task 的 context |
| 无任务数量管控 | 无限创建 | 上限 N，超限强制 merge 或询问 |
| overlap 检测原始 | 2+ 关键词交集 | LLM 语义理解 + 场景推理 |
| info prompt 缺并发感知 | task 不知道其他 task 在做什么 | 看得到并行任务，避免重复 |

---

## 2. 双层模型：NLU 层 vs LLM 层

### 2.1 NLU/Rule 层（确定性，零 LLM）

**当前实现**：`RuntimeNLURouter`（PortableIntentModel + CommandRouter）和 `_try_rule_match` 纯模式匹配，零 LLM 调用。命中后创建 `skip_agent=True` 的 direct-managed task。

**NLU 路径特征**：
- 确定性路由，无 LLM → 毫秒级延迟
- 创建 `skip_agent=True` task → 无 TaskAgent、无 LLM 循环
- 单次 Expert job → 秒级完成
- 无 agent 层资源竞争（"多 agent 争抢"问题不存在）

**设计决策：NLU 永远走 interrupt，不参与 disposition 判断，不占 task 配额。**

理由：
1. NLU task 没有 LLM agent → 不参与"多 agent 竞争"的核心问题
2. NLU task 轻量短命 → 秒级完成自动清理
3. NLU 的冲突在 Expert 层解决（如 EconomyExpert 检查生产队列重复）
4. 如果 NLU 也走 disposition 就必须调 LLM → 那 NLU 就失去意义了
5. NLU task 对 LLM task 可见（通过 `[concurrent_tasks]`），LLM task 自行避免重复

### 2.2 LLM 层（disposition 模型）

只有 NLU/Rule **未命中**、进入 LLM 分类路径时，才执行 disposition 判断。

```
handle_player_input()
  │
  ├─ ack → "收到"
  ├─ deploy_feedback → 快速处理
  │
  ├─ RuntimeNLU match? → YES → interrupt（直接 _start_direct_job, 不占配额）
  ├─ Rule match?       → YES → interrupt（直接 _start_direct_job, 不占配额）
  │
  └─ LLM 分类路径（NLU 未命中）
        ├─ reply → 回答 pending question
        ├─ query → LLM 直答
        ├─ cancel → cancel_task
        │
        └─ command / info → **disposition 判断**
              ├─ merge    → inject_player_message(target_task_id, text)
              ├─ override → cancel_task(target) → create_task()
              ├─ interrupt→ create_task(priority+20), 不取消旧的
              └─ new      → agent_task_count < MAX? → create_task()
                             agent_task_count >= MAX? → 强制 merge 到最相关 task
```

### 2.3 配额只计 LLM-managed tasks

```python
MAX_CONCURRENT_AGENT_TASKS = 5  # 只计有 TaskAgent 的 task
```

```python
def _count_agent_tasks(self) -> int:
    """统计当前活跃的 LLM-managed tasks（不含 direct-managed）。"""
    tasks = self.kernel.list_tasks()
    terminal = {"succeeded", "failed", "aborted", "partial"}
    return sum(
        1 for t in tasks
        if t.status.value not in terminal
        and not self.kernel.is_direct_managed(t.task_id)
    )
```

---

## 3. 意图处置模型（Intent Disposition）

### 3.1 四种处置

| 处置 | 语义 | 何时触发 | 动作 |
|------|------|---------|------|
| **new** | 全新领域，无重叠 | "探索地图" (无探索任务在跑) | `create_task()` |
| **merge** | 补充/修正/追加到已有任务 | "再多造几辆" → 已有生产任务 | `inject_player_message(task_id, text)` |
| **override** | 新意图取代旧意图 | "全力进攻" 取代 "发展经济" | `cancel_task(old)` → `create_task()` |
| **interrupt** | 紧急任务，旧任务暂停不取消 | "基地被打了" → 紧急防守 | `create_task(high_priority)`, 旧任务降优先级 |

### 3.2 超限处理（agent_task_count >= MAX）

disposition 为 new 但已达上限时：
1. 找最相关的 running agent task → merge
2. 如果无相关 → 找最老/最低优先级的 → override
3. 通知玩家 "任务已满(5/5)，已合并到 #00X"

---

## 4. Adjutant Classification Prompt 重设计

### 4.1 当前 prompt 问题

- 只输出 `type`（command/reply/query/cancel/info），缺 `disposition`
- 缺失对活跃任务语义关系的推理
- 没有"这个新指令和 #001 是什么关系"的概念

### 4.2 新 Classification Prompt

```
You are the Adjutant (副官) in a real-time strategy game.
Your job is to classify player input AND decide how it relates to active tasks.

## Input Types
1. "reply" — answering a pending question
2. "query" — asking for information (no action)
3. "cancel" — explicitly cancel a task ("取消001", "停止#002")
4. "command" — new order/instruction or follow-up to existing
5. "info" — intelligence/feedback about game state

## Disposition (for command/info types ONLY)
When type is "command" or "info", you MUST also decide disposition:

- "new" — no active task covers this intent. Start a fresh task.
- "merge" — an active task already handles this domain. Forward the message to it.
  Use when: adjusting, supplementing, or following up on an active task.
  Examples: "再多造几辆" → merge into production task
            "敌人在左下角" → merge into recon/combat task
            "快点" → merge into most recent task
- "override" — this intent REPLACES a conflicting active task. Cancel the old, start new.
  Use when: the new intent contradicts or supersedes an active task's goal.
  Examples: "全力进攻" overrides "发展经济"
            "停止探索，去攻击" overrides recon task
            "不要造坦克了，造飞机" overrides tank production task
- "interrupt" — urgent/time-sensitive event. Create new task without cancelling existing.
  Use when: emergency that needs immediate action alongside existing tasks.
  Examples: "基地被打了", "敌人来了", "紧急防守"

## Decision Logic
1. If there are pending questions and the input matches one → "reply"
2. If the player explicitly asks to cancel → "cancel"
3. If the player is purely asking for information → "query"
4. Otherwise it is "command" or "info":
   a. Is there an active task in the SAME DOMAIN? → likely "merge" or "override"
   b. Does it ADD to the task (more of the same, slight adjustment)? → "merge"
   c. Does it CONTRADICT the task (opposite goal, different strategy)? → "override"
   d. Is it an emergency report? → "interrupt"
   e. No overlap? → "new"
5. "info" type almost always merges — player is giving intelligence to the AI, not ordering a new action.
6. Short follow-ups ("继续", "快点", "多造点") → merge into the most recent/relevant task.

## Output Format
Respond with ONLY a JSON object:
{"type": "command"|"reply"|"query"|"cancel"|"info", "disposition": "new"|"merge"|"override"|"interrupt", "target_task_label": "001", "confidence": 0.0-1.0, "reason": "brief why"}

- disposition and target_task_label are required ONLY when type is "command" or "info"
- target_task_label is the label of the existing task (from active_tasks), or null if disposition is "new"
```

### 4.3 Classification Context 增强

```json
{
  "active_tasks": [
    {
      "label": "001",
      "raw_text": "造5辆坦克",
      "status": "running",
      "is_nlu": false,
      "expert_type": null,
      "age_seconds": 45
    },
    {
      "label": "002",
      "raw_text": "探索地图",
      "status": "running",
      "is_nlu": true,
      "expert_type": "ReconExpert",
      "age_seconds": 12
    }
  ],
  "agent_task_count": 1,
  "max_agent_tasks": 5,
  "pending_questions": [...],
  "recent_dialogue": [...],
  "recent_completed_tasks": [
    {
      "label": "003",
      "raw_text": "造3辆坦克",
      "result": "succeeded",
      "summary": "已生产3辆重型坦克(3tnk)",
      "expert_type": "EconomyExpert",
      "is_nlu": true,
      "completed_seconds_ago": 8
    }
  ],
  "player_input": "再多造几辆"
}
```

**关键信息链 — Adjutant 必须知道的两件事**：

1. **玩家下达了什么**：`active_tasks` 含所有在跑的任务（含 NLU 直达），`recent_dialogue` 含命令历史
2. **上一个 Expert 的结果**：`recent_completed_tasks` 含 `summary`（具体产出/失败原因/Expert 类型）

**数据流**（已存在，需增强展示）：
- NLU 命令执行时 → `_record_dialogue("adjutant", "收到指令...")` → 进入 recent_dialogue
- Expert 完成时 → `notify_task_completed(summary=...)` → 进入 recent_completed_tasks + dialogue_history
- 两者都在分类 context 中，LLM 做 disposition 时可见

**改进点**：
- `recent_completed_tasks` 当前只有 `{label, raw_text, result, summary}` → 增加 `expert_type`, `is_nlu`, `completed_seconds_ago`
- `is_nlu=true` 的任务通常不是 merge 目标（无 agent 接收消息），但 LLM 需要看到它们来避免创建重复任务
- `expert_type` 让 LLM 知道刚才用了哪个 Expert，对后续决策有帮助

---

## 5. 消息注入机制（Message Forwarding / Merge 路径）

### 5.1 Kernel 新接口

```python
def inject_player_message(self, task_id: str, text: str) -> bool:
    """注入玩家消息到指定 task 的 event 队列，唤醒 TaskAgent。

    Returns True if the message was injected, False if task not found or terminal.
    Only works for LLM-managed tasks (non-direct-managed).
    """
```

实现：
1. 验证 task_id 存在且 running 且非 direct-managed
2. 创建 `Event(type=EventType.PLAYER_MESSAGE, data={"text": text})`
3. 追加到 task 的 event buffer（`_event_buffers[task_id]`）
4. 唤醒对应 TaskAgent（写入 agent queue）

### 5.2 EventType 扩展

```python
class EventType(str, Enum):
    ...
    PLAYER_MESSAGE = "PLAYER_MESSAGE"   # 玩家消息注入（merge 路径）
```

### 5.3 Adjutant merge 路径实现

```python
async def _handle_merge(self, text: str, target_label: str) -> dict[str, Any]:
    """将玩家消息转发到目标 task。"""
    tasks = self.kernel.list_tasks()
    target = next(
        (t for t in tasks
         if getattr(t, "label", "") == target_label
         or getattr(t, "label", "").lstrip("0") == target_label.lstrip("0")),
        None,
    )
    if target is None or self.kernel.is_direct_managed(target.task_id):
        # Fallback: NLU task 或找不到 → 创建新 task
        return await self._handle_command(text)

    ok = self.kernel.inject_player_message(target.task_id, text)
    if not ok:
        return await self._handle_command(text)

    return {
        "type": "command",
        "ok": True,
        "merged": True,
        "existing_task_id": target.task_id,
        "response_text": f"收到，已转发给任务 #{target.label}（{target.raw_text}）",
    }
```

---

## 6. Info Prompt（TaskAgent Context）增强

### 6.1 当前 6-block 结构保留

Xi 已实现的结构不变：
- `[task_parse]` — 任务解析
- `[entity_grounding]` — 实体落地
- `[decision_packet]` — 决策包
- `[state_delta]` — 状态变化
- `[action_ledger]` — 动作账本
- `[completion_rule]` — 完成标准

### 6.2 新增 `[player_messages]` 块

位于 `[decision_packet]` 之前，**最高优先级**：

```
[player_messages]
2s ago: "再多造几辆"
15s ago: "优先重坦"
```

构建逻辑：从 `recent_events` 中过滤 `type=PLAYER_MESSAGE` 的事件，按时间倒序排列。

### 6.3 增强 `[concurrent_tasks]` 块

当前 `other_active_tasks` 已有基础数据，改进展示格式：

```
[concurrent_tasks]
#001 "探索地图" running — ReconExpert [NLU直达]
#003 "生产步兵" running — EconomyExpert [NLU直达]
#004 "全力进攻" running — LLM管理, 45s ago
禁止重复已有任务的工作。如需协调，通过complete_task让位或等待。
```

标注 `[NLU直达]` vs `LLM管理` 让 TaskAgent 知道哪些是确定性执行、哪些可能被 merge。

### 6.4 TaskAgent System Prompt 追加

```
## 玩家追加消息
[player_messages]包含玩家在任务进行中注入的补充指令或情报。
- 追加指令（"再多造几辆"）→ 调整当前目标数量/范围
- 情报（"敌人在左下角"）→ 调整搜索方向或战术
- 优先级变更（"先造重坦"）→ 调整生产顺序
- 必须在本次wake响应，不可忽略
```

---

## 7. NLU 层与 LLM 层的可见性

### 7.1 NLU task 对 LLM task 可见

NLU task（skip_agent=True）虽然不参与 disposition、不占配额，但**必须出现在 LLM task 的 `[concurrent_tasks]` 块中**。

目的：
- LLM task "发展经济" 看到 NLU task "造5辆坦克 [NLU直达]" → 知道玩家已经下了具体指令，不重复生产坦克
- LLM task 可以围绕 NLU task 做互补（例如"发展经济"看到已在造坦克 → 补电厂而非再造坦克）

### 7.2 LLM task 对 NLU task 不可见

NLU task 无 agent，无 context，无法感知并行 task。冲突在 Expert 层解决：
- EconomyExpert：检查生产队列避免重复
- ReconExpert：避免重叠探索区域
- CombatExpert：不重复分配同一单位

---

## 8. 实现计划

### Phase 1: Kernel 基础设施（~100 行）
- `models/enums.py` — 添加 `EventType.PLAYER_MESSAGE`
- `kernel/core.py` — 添加 `inject_player_message()` + event buffer 路由 + 唤醒 agent
- `adjutant/adjutant.py` KernelLike protocol — 添加 `inject_player_message` 签名

### Phase 2: Adjutant 分类 + 处置（~200 行）
- `adjutant/adjutant.py` — 重写 `CLASSIFICATION_SYSTEM_PROMPT`（§4.2 全文）
- `_classify_input()` — 解析 disposition + target_task_label
- `_parse_classification()` — 兼容新旧格式
- `handle_player_input()` — LLM 路径增加 merge/override/interrupt 分支
- 新增 `_handle_merge()`, `_handle_override()`, `_count_agent_tasks()`
- `_build_context()` — 增加 is_nlu/agent_task_count/max_agent_tasks
- `MAX_CONCURRENT_AGENT_TASKS = 5`，超限自动 merge

### Phase 3: Info prompt 增强（~80 行）
- `task_agent/context.py` — 新增 `_build_player_messages()` 块
- `task_agent/context.py` — 增强 `[concurrent_tasks]` 标注 NLU/LLM + Expert 类型
- `task_agent/agent.py` — System prompt 追加 §6.4 规则

### Phase 4: 测试（~150 行）
- `tests/test_adjutant.py` — disposition 解析、merge 路径、override 路径、task count limit
- `tests/test_e2e_adjutant.py` — 多轮对话 merge/override 场景

**总改动量：~530 行新/改代码**

---

## 9. 与架构切换（Plan A Commander）的关系

此设计和 Commander 架构**不冲突**：
- 如果最终走 Commander 模式，disposition 逻辑移入 Commander 内部（同一个 LLM 自行决定）
- `inject_player_message` 变成给 Commander 的 event queue 追加消息
- 如果保持多 Task 模式，这个设计直接解决核心问题
- NLU 层的"直达 interrupt"逻辑在两种架构下都成立

---

## 附录 A：NLU 层分析

### 当前 NLU 路径完整流程

```
handle_player_input(text)
  → _try_runtime_nlu(text)
    → RuntimeNLURouter.route(text)
      → PortableIntentModel.predict_one(text)     # 模式匹配, 零 LLM
      → CommandRouter.route(rewritten_text)        # 规则路由, 零 LLM
    → 置信度/安全检查
    → 返回 RuntimeNLUDecision（steps: list[DirectNLUStep]）
  → _handle_runtime_nlu(text, decision)
    → _start_direct_job(text, expert_type, config)
      → kernel.create_task(skip_agent=True)        # 无 TaskAgent
      → kernel.start_job(task_id, expert_type)     # 直接启动 Expert
```

### NLU 不走 disposition 的技术原因

| 如果 NLU 走 disposition | 后果 |
|------------------------|------|
| 需要调 LLM 判断 | +300~500ms 延迟, 抵消 NLU 速度优势 |
| merge 到 LLM task | LLM task 要等 wake 才看到, 但 NLU 指令通常需要立即执行 |
| override LLM task | NLU 是具体指令，LLM task 是战略目标，层级不同，不应互相覆盖 |
| 配额计入 | NLU task 秒级完成，占配额无意义 |

### NLU 与 LLM task 冲突的解决路径

```
玩家: "发展经济"    → LLM task #001 (manages overall economy)
玩家: "造5辆坦克"   → NLU task #002 (skip_agent, EconomyExpert)

#001 的 [concurrent_tasks]:
  #002 "造5辆坦克" running — EconomyExpert [NLU直达]

#001 LLM 看到后: "已有坦克生产在执行, 我不重复, 补电厂和矿场"
```

冲突在信息层解决，不需要阻止 NLU 执行。

---

## 附录 B：场景推演

### 场景 A: 追加指令（merge）

```
玩家: "造5辆坦克"
  → NLU 命中 → interrupt → Task#001 (NLU直达, EconomyExpert)
玩家: "发展经济"
  → NLU 未命中 → LLM 分类 → disposition: new → Task#002 (LLM管理)
玩家: "多建几个电厂"
  → NLU 未命中 → LLM 分类 → disposition: merge → target=#002
  → inject "多建几个电厂" to #002
  → #002 的 LLM 看到 [player_messages]: "多建几个电厂" → 优先建电厂
```

### 场景 B: 冲突覆盖（override）

```
玩家: "发展经济"
  → LLM → new → Task#001
玩家: "全力进攻"
  → LLM → override → target=#001 → cancel #001 → Task#002
  → #002 拥有全部资源，无冲突
```

### 场景 C: 紧急打断（interrupt）

```
玩家: "探索地图"
  → NLU 命中 → interrupt → Task#001 (NLU直达)
玩家: "发展经济"
  → LLM → new → Task#002
玩家: "基地被打了"
  → LLM → interrupt → Task#003 (priority=70)
  → #002 继续但低优先, #001 NLU task 不受影响
```

### 场景 D: 情报转发

```
玩家: "全力进攻"
  → LLM → new → Task#001 (CombatExpert via LLM)
玩家: "敌人在左下角"
  → LLM → type: info, disposition: merge → target=#001
  → inject "敌人在左下角" to #001
  → #001 的 LLM 看到情报 → 调整攻击方向到左下角
```

### 场景 E: 任务满额（MAX=5）

```
#001~#005 全部 running (LLM管理)
玩家: "建雷达"
  → NLU 命中 → interrupt → Task#006 (NLU直达, 不占配额) ← 直接执行
玩家: "加强防御"
  → NLU 未命中 → LLM → disposition: new, 但 agent_task_count=5 >= MAX
  → 找最相关 task (#003 "防守基地") → 强制 merge
  → "任务已满(5/5)，已合并到 #003（防守基地）"
```

### 场景 F: NLU task 对 LLM task 可见

```
玩家: "造5辆坦克"
  → NLU → Task#001 (NLU直达, EconomyExpert)
玩家: "全面发展"
  → LLM → Task#002

#002 的 info prompt:
  [concurrent_tasks]
  #001 "造5辆坦克" running — EconomyExpert [NLU直达]
  规则: 坦克生产已由#001处理，不重复。聚焦其他发展项。

#002 LLM: "坦克已有人造了, 我来建电厂+兵营+矿场"
```
