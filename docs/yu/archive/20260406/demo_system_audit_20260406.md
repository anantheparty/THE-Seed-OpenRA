# Demo 收尾系统审计

日期：2026-04-06  
作者：yu

## 1. 结论

当前系统不是“完全不可 demo 的半成品”，而是：

- **主链已经能跑**
- `Adjutant/NLU`、`WS/trace`、`GameLoop` 三条快测当前都通过
- 但仍然存在几个**会在 live demo 中直接伤观感或误导判断**的结构点

我对当前状态的总体判断是：

- **可以进入 demo 收尾**
- 但要把注意力集中在少数几个 `P0/P1`，而不是继续扩功能

本轮我实际检查了：

- 核心运行时：`adjutant/`, `kernel/`, `task_agent/`, `world_model/`
- 执行链：`experts/`, `openra_api/`, `main.py`, `game_loop/`
- 前端与可观测性：`web-console-v2/`, `ws_server/`, `logging_system/`
- 快测结果：
  - `python3 tests/test_adjutant.py` → `40 passed`
  - `python3 tests/test_ws_and_review.py` → `13 passed`
  - `python3 tests/test_game_loop.py` → `11 passed`

## 2. 当前最强的 5 个优点

1. **NLU 已经真正接回 runtime，不再只靠 LLM 猜**
   - `adjutant/runtime_nlu.py`
   - 简单命令、shorthand、安全复合序列已能直接落到当前 `Kernel/Expert`

2. **系统已经具备基本可观测性**
   - `Task Trace`
   - session log
   - per-task log path
   - history replay

3. **GameLoop 阻塞 I/O 隔离已经做对**
   - `game_loop/loop.py`
   - 不再像早期那样直接饿死 Adjutant/LLM coroutine

4. **共享生产链已经开始中央化**
   - `UnitRequest`
   - `QueueManager`
   - 这说明系统不再完全把共享队列交给自由 LLM

5. **WorldModel + runtime facts + information experts 方向正确**
   - 这是后面把系统做“像样”的关键基础

## 3. P0：明天 demo 前最该警惕的问题

### P0-1：`sync_request` 可能拿不到“当前快照”，因为被全局 throttle 吃掉

文件：
- [main.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/main.py)
- [server.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/ws_server/server.py)

证据：
- `RuntimeBridge.on_sync_request()` 只是调用 `publish_dashboard()`，见 `main.py:321-326`
- `publish_dashboard()` 里发送当前状态依赖：
  - `send_world_snapshot()`，见 `ws_server/server.py:252-257`
  - `send_task_list()`，见 `ws_server/server.py:268-280`
- 这两个发送函数都用了**全局** throttle 时间戳：
  - `_last_world_snapshot_at`
  - `_last_task_list_at`

问题：
- 如果 A 客户端刚触发过一次 dashboard publish
- B 客户端在 1 秒内后打开并发 `sync_request`
- 那 B 这次 `sync_request` 可能根本收不到最新 `world_snapshot` 和 `task_list`
- 只会收到历史 replay

demo 影响：
- 你 late open `Diagnostics` / reconnect / second screen 时，可能看到日志和历史，但看不到当前状态
- 这会让人误以为“系统没同步”

建议：
- `sync_request` 走一条**不受 throttle 的定向快照发送**
- 不要复用 broadcast 节流路径

### P0-2：TaskAgent 每轮上下文存在明显重复注入，长任务容易把 prompt 撑爆

文件：
- [context.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/task_agent/context.py)
- [agent.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/task_agent/agent.py)

证据：
- `context_to_message()` 先写：
  - `[CONTEXT UPDATE]`
  - 一整段 JSON header，见 `context.py:272-287`
- 然后又把同一批 task/jobs/signals/events/world/runtime 信息转成可读文本再次写一遍，见 `context.py:289-340`
- `TaskAgent` 每轮 wake 都会重新发送固定 `SYSTEM_PROMPT` 和新 context

问题：
- 对长任务来说，重复信息太多
- 既增加 token 成本
- 也增加“关键信息被埋掉”的风险

demo 影响：
- 复杂任务更慢
- 更容易出现“做了很多轮但 reasoning 质量并不高”

建议：
- demo 前不一定必须大改
- 但至少要意识到：**复杂长任务的不稳定，不一定是模型本身，而是上下文注入太重**

## 4. P1：会影响观感和调试效率的问题

### P1-1：按 task 落盘的日志其实还不完整

文件：
- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/logging_system/core.py)

证据：
- `PersistentLogSession.append()` 只有在 `record.data["task_id"]` 存在时才写入 `tasks/<task_id>.jsonl`，见 `logging_system/core.py:59-64`

问题：
- 很多日志虽然和某 task 强相关，但未必把 `task_id` 放在顶层 `data.task_id`
- 例如可能在：
  - `holder_task_id`
  - nested `data`
  - 或只带 `job_id`
- 这些记录会进 `all.jsonl`，但不会进 task 专属日志

demo 影响：
- 你以为自己拿到的是“task 全量日志”
- 但实际可能缺关键中间事件
- 事后复盘容易误判

建议：
- 后续至少把这些别名也纳入 task file 路由：
  - `holder_task_id`
  - nested `task_id`
  - 或 `job_id -> task_id` 反查

### P1-2：历史回放时，task message 被降格成 notification，语义丢失

文件：
- [main.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/main.py)

证据：
- `_replay_history()` 不直接回放历史 `task_message`
- 它把 `TASK_INFO / TASK_WARNING / TASK_COMPLETE_REPORT` 转成 `player_notification` 发送，见 `main.py:497-516`
- `TASK_QUESTION` 甚至直接跳过，见 `main.py:497-499`

问题：
- live 期间如果你晚开调试界面
- 看见的是“通知”
- 不是原始 task-level message 语义

demo 影响：
- 对“task 是否真的在中途主动说话”这件事，历史回放会显得比实际更模糊

建议：
- 未来 history replay 应保留原始 `task_message`
- notification 可以作为兼容副本，而不是替代物

### P1-3：`TaskPanel` 对用户仍然太工程化

文件：
- [TaskPanel.vue](/Users/kamico/work/theseed/THE-Seed-OpenRA/web-console-v2/src/components/TaskPanel.vue)

问题：
- 状态直接显示原始枚举值：
  - `running`
  - `failed`
  - `partial`
- Expert 名直接显示类名：
  - `EconomyExpert`
  - `ReconExpert`
- 对懂代码的人有用，对 demo 观众不够友好

demo 影响：
- 观众能看懂“有东西在跑”
- 但不容易立刻理解“这个 task 正在干嘛”

建议：
- demo 前如果还有时间，做最小映射：
  - `EconomyExpert -> 生产/建设`
  - `ReconExpert -> 侦察`
  - `DeployExpert -> 部署`
  - `running -> 执行中`

### P1-4：Diagnostics Trace 只保最近窗口，长任务早期过程会被截断

文件：
- [DiagPanel.vue](/Users/kamico/work/theseed/THE-Seed-OpenRA/web-console-v2/src/components/DiagPanel.vue)

证据：
- `traceEntries` 超过 800 会截旧数据，见 `DiagPanel.vue:156-159`
- 当前选中任务只展示最后 120 条，见 `DiagPanel.vue:104-109`

问题：
- 对长任务后期开调试时，早期关键分叉可能已经不在 UI 里

demo 影响：
- 如果你要现场回放一个长任务失败链，前面的关键一步可能已经掉了

建议：
- 现场调试优先结合磁盘日志路径
- 不要完全依赖 UI trace 视图

## 5. P2：结构上还不成熟，但明天可以先不动

### P2-1：系统仍是“多 TaskAgent 过渡态”

文件：
- [core.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/kernel/core.py)

证据：
- `create_task()` 仍然每个 task 创建一个新的 `TaskAgent`，见 `kernel/core.py:212-248`

影响：
- 这是长期架构问题，不是明天 demo 的第一优先级
- 但要明白：
  - 现在稳定性的很多提升，来自 patch 和 guard
  - 不是因为“多脑结构已经正确”

### P2-2：information expert 机制还只是雏形

文件：
- [context.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/task_agent/context.py)
- [info_base_state.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/experts/info_base_state.py)
- [info_threat.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/experts/info_threat.py)

问题：
- 目前只有少量 info experts
- `production` subscription 还是 placeholder，见 `context.py:26-35`
- 部分输出仍是英文摘要：
  - `critical — no construction yard`
  - `economy-only — no combat production`
  - `northwest/southeast`

影响：
- 这不会马上毁 demo
- 但会限制 LLM 的决策质量和语言一致性

### P2-3：知识层已有雏形，但仍有未核准的硬编码

文件：
- [knowledge.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/experts/knowledge.py)

问题：
- 里面仍有 `TODO: verify`、疑似未最终确认的 unit mapping
- 比如 counter/soft-strategy 仍混有推测性内容

影响：
- 作为演示期知识层可以接受
- 但如果进入长期维护，必须继续硬化

## 6. 这轮审计对 demo 的直接建议

### 必须盯住的

1. `sync_request` / late-open 视图是否总能拿到当前状态
2. 复杂长任务不要作为主演示
3. 出现异常时，优先看：
   - 当前 task log path
   - `all.jsonl`
   - 不是只信 UI trace

### 明天适合演示的

- `展开基地车`
- `建造电厂`
- `建造兵营`
- `生产3个步兵`
- `探索地图`
- `战况如何`

### 明天不适合当主演示的

- 复杂复合战略命令
- 长时间 managed-task 自由规划链
- 依赖长 trace 才能解释清楚的行为

## 7. 最后的判断

当前系统给我的感觉不是“没做出成果”，而是：

- 运行主链已经成形
- 基础设施已经具备
- 最大的问题是仍在过渡架构上，且少数地方会在 live 中露出明显的工程接缝

如果只看明天 demo，我的建议是：

- 把这套系统当成一个**能工作的智能副官原型**
- 重点展示：
  - NLU 快速路由
  - Expert 执行
  - 结构化反馈
  - 调试可见性
- 不要把“多 Task 自由 LLM 规划”当主卖点

那样它是能站得住的。
