# TaskAgent / Prompt / Runtime 深度分析报告

日期：2026-04-02  
分析对象：`Task #001` / `task_id = t_f2d56cd3` / 原始命令 `展开`  
主要证据：
- `Logs/runtime/session-20260401T190855Z/tasks/t_f2d56cd3.jsonl`
- `task_agent/agent.py`
- `task_agent/context.py`
- `task_agent/tools.py`
- `experts/base.py`
- `experts/deploy.py`
- `experts/movement.py`
- `kernel/core.py`
- `models/core.py`

## 结论摘要

这条任务暴露的主问题，不应先归因于模型能力，而应先归因于：

1. `TaskAgent` 的 system prompt 过宽、phase policy 不足。
2. 每轮注入给 LLM 的信息不够“决策友好”，缺少关键 runtime 事实。
3. 事件/信号顺序本身会误导 agent 的因果理解。
4. `DeployExpert` 的成功语义过弱，`MovementExpert` 的成功语义反而更硬。
5. `TaskAgent` 当前没有“通过 Adjutant 与玩家对话”的正式 tool，和设计存在漂移。
6. 当前系统没有“信息 Expert / 订阅式信息源”这层结构，只给了一个粗粒度 `world_summary`。

换句话说：这条任务的失败模式，更像是 `prompt + context + runtime semantics` 的系统设计问题，而不是“LLM 天生不行”。

## 1. 当前 TaskAgent 的实现方式

### 1.1 基本架构

当前系统对一个 Task 的执行链是：

1. `Adjutant` 创建 `Task`
2. `Kernel` 为这个 Task 创建一个 `TaskAgent`
3. `TaskAgent` 在每次 wake 时：
   - 拉当前 `jobs`
   - 拉 `world_summary`
   - 拉 `recent_signals / recent_events / open_decisions`
   - 组装 `context_packet`
   - 调 LLM
   - 由 LLM 调 tool
4. tool 最终落到：
   - `Kernel.start_job(...)`
   - `Kernel.patch_job(...)`
   - `Kernel.complete_task(...)`
   - 等

### 1.2 Expert 和 Job 的关系

代码语义上，系统是区分 `Expert` 和 `Job` 的：

- `Expert`
  - 是“类型”
  - 定义能力和配置 schema
  - 在 `experts/base.py` 里分为：
    - `InformationExpert`
    - `PlannerExpert`
    - `ExecutionExpert`

- `Job`
  - 是 `ExecutionExpert` 的运行时实例
  - 由 `ExecutionExpert.create_job(...)` 生成
  - 在运行时 autonomously tick

所以用户直觉是对的：

- `job` 应该理解成 `expert` 的实例

但现在系统对用户和 LLM 暴露得不够清楚，导致这两层概念在调试和叙事里容易混淆。

## 2. Prompt 注入方式

### 2.1 每一步是不是同一个 prompt

是，但要分清“wake”与“LLM turn”。

当前 `TaskAgent` 在每次 LLM 调用时都会重新构造：

1. 一个固定的 `SYSTEM_PROMPT`
2. 累积的 `conversation history`
3. 一个新的 `[CONTEXT UPDATE]`

代码位置：
- `task_agent/agent.py`
  - `SYSTEM_PROMPT`
  - `_build_messages(...)`

也就是说：

- system prompt 是固定的
- 每次 LLM 调用都会重新发送
- 它不会根据任务类型裁剪

### 2.2 Prompt 里是否“把所有专家都告诉了”

基本是。

当前 `SYSTEM_PROMPT` 一次性告诉 LLM：

- `ReconExpert`
- `CombatExpert`
- `MovementExpert`
- `DeployExpert`
- `EconomyExpert`
- 一批命令到 Expert 的静态映射
- 还要求“先 query_world 再做决定”

但它没有告诉 LLM：

- 当前这一条任务允许哪些 phase
- 当前这一条任务不允许跨到哪些 Expert
- 什么时候绝对不能判 success
- 当前世界所处阶段
- 当前有哪些“信息源/信息 Expert”可用

所以它是“全量能力提示”，不是“任务约束提示”。

### 2.3 是否有重复 prompt 注入

有，而且量不小。

不是“同一条消息里复制两遍 system prompt”，而是：

- 同一个 `SYSTEM_PROMPT` 被反复发送
- 旧的 assistant/tool 对话被持续回放
- 每个 wake 又追加新的 `[CONTEXT UPDATE]`

在 `Task #001` 里，量化结果是：

- `context_snapshot`：19 次
- `llm_input`：40 次
- 第一轮 `llm_input`：
  - `2` 条 message
  - 约 `4,593` 字符
- 最后一轮 `llm_input`：
  - `80` 条 message
  - 约 `79,225` 字符
- 单条 `SYSTEM_PROMPT` 长度约：
  - `3,330` 字符

所以回答你的问题：

- 是，原始 log 很长，确实说明大量重复信息也很多
- 尤其是重复的系统提示、重复的旧推理、重复的旧 tool 结果

## 3. 当前给 LLM 的信息是否足够

结论：不够，而且不够的不是数量，而是“决策质量”。

### 3.1 当前 `context_packet` 有什么

`task_agent/context.py` 当前只给：

- `task`
- `jobs`
- `world_summary`
- `recent_signals`
- `recent_events`
- `open_decisions`

`world_summary` 也只是：

- `economy`
- `military`
- `map`
- `known_enemy`

### 3.2 关键缺失

对 `Task #001` 这类任务，缺失了非常多关键运行时事实，例如：

- `has_yard`
- `mcv_present`
- `mcv_idle`
- `deploy_confirmed`
- `deploy_retry_count`
- `same_failure_signature_count`
- `phase`
- `task_template`
- `available_information_sources`
- `current_strategy_stage`
- `base_established`

这就导致 LLM 只能自己从粗 summary 和零散 signal 里脑补。

### 3.3 当前没有“信息 Expert / 按需订阅”机制

你提出的方向是对的：

- 初始不应该把所有信息一股脑塞进 prompt
- 而应该有一批信息型 Expert / Manager
- Task 初始只打开一部分
- 后续按需订阅更多

而当前系统没有这层。

现状更像是：

- 所有任务共用一个 `WorldSummaryProvider`
- TaskAgent 没有信息订阅能力
- 也没有针对任务类型的“预开信息源”机制

所以你说“它连现在世界什么样、有几个 agent、什么阶段都不知道，自然会乱扯”，这个判断是成立的。

## 4. Task #001 的逐步执行分析

## 4.1 总体数据

- 原始命令：`展开`
- `task_id = t_f2d56cd3`
- 总 runtime 时长：约 `179.7s`
- `llm_input` 调用：`40` 次
- `context_snapshot`：`19` 次
- 运行中创建过的 job 共 `8` 个

job 链：

- `ReconExpert j_08549d47`
- `DeployExpert j_76966359`
- `DeployExpert j_1fc66fff`
- `MovementExpert j_39234327`
- `MovementExpert j_f394d608`
- `DeployExpert j_178209b3`
- `DeployExpert j_6b08cdb3`
- `DeployExpert j_928437c2`

### 4.2 逐轮 LLM 调用表

下表按 `llm_input` 为单位，而不是按 wake。

| Step | 耗时 | 主要看到的信息 | 主要动作 | 问题/反思 |
| --- | ---: | --- | --- | --- |
| 1 | 7.705s | 世界几乎未探索、现金 5000、无基础设施 | 先把 `展开` 理解成“战略扩张”，决定先侦察 | 缺 phase policy，`展开` 被过度解释成 strategy，而不是更受控的 establish base |
| 2 | 6.234s | 查到 MCV `actor_id=129` | 起 `ReconExpert` | 仍然先侦察，说明 prompt 没把 deploy-first 钉死 |
| 3 | 2.024s | Recon job 已启动 | 无动作，等待 | 这一步正常，但建立在错误初始方向上 |
| 4 | 9.891s | Recon 报 awareness degraded；无 power | 终于意识到应先 deploy MCV，并准备暂停 Recon | 方向纠正太晚，且花了近 10 秒 |
| 5 | 3.377s | Recon 已暂停 | 起 `DeployExpert j_76966359` | 正常 |
| 6 | 2.409s | Deploy job 在 waiting | 无动作，等待 | 对 `waiting` 没有更强解释 |
| 7 | 4.537s | `resource_lost`，MCV 还被 Recon 绑定 | abort Recon | 这里能看出因果，但系统没有预先避免这种资源冲突 |
| 8 | 2.541s | Recon abort 成功 | 无动作，等待 deploy 自动恢复 | 合理 |
| 9 | 3.399s | Deploy 仍在 waiting | 再查 world | 开始进入“查一次、猜一次”的循环 |
| 10 | 4.162s | MCV 在移动，不是 idle | 继续等待 | 仍然缺少明确 deploy 状态语义 |
| 11 | 3.973s | 仍在 moving | 等待 | 正常但信息增量低 |
| 12 | 7.268s | 等太久无进展 | abort 旧 deploy，换新 deploy 位置 `[48,96]` | 开始把“长 waiting”解释成位置有问题，但没有证据 |
| 13 | 3.356s | deploy 已 abort | 启新 `DeployExpert j_1fc66fff` | 进入 retry |
| 14 | 3.086s | 新 deploy 在 waiting | 继续等待 | 正常 |
| 15 | 3.056s | 又见 `resource_lost` | 再查 MCV | 反复出现同类 signal，但缺少重复失败摘要 |
| 16 | 5.822s | MCV 实际跑到 `[86,94]`，方向偏了 | abort deploy，改起 `MovementExpert` | 这一步是重要 pivot，但仍然靠猜测产生 |
| 17 | 3.426s | deploy 已 abort | 起 `MovementExpert j_39234327` | 方向开始收敛 |
| 18 | 3.209s | movement waiting | 等待 | 正常 |
| 19 | 3.800s | movement 也报 `resource_lost`，但 summary 又说 idle_self_units=1 | 再查 world | 这就是信息矛盾：signal 与 summary 缺统一解释 |
| 20 | 4.203s | MCV idle at `[99,92]` | abort old movement | 合理，但说明 runtime/资源语义不稳 |
| 21 | 3.692s | movement 已 abort | 起新 `MovementExpert j_f394d608` | 正常 |
| 22 | 3.148s | movement running | 等待 | 正常 |
| 23 | 2.601s | movement running+bound actor | 等待 | 正常 |
| 24 | 3.613s | progress tick=10 | 等待 | 正常 |
| 25 | 3.620s | 仍在 running | 等待 | 正常 |
| 26 | 3.366s | progress tick=20 | 等待 | 正常 |
| 27 | 3.120s | tick=30 | 预期 soon deploy | 这里开始把 movement success 预设成 deploy success 前奏 |
| 28 | 3.319s | progress 无新变化 | 查位置 | 合理 |
| 29 | 5.718s | MCV 到 `[68,95]`，接近目标 | 在 movement 尚未完全收口时就起 `DeployExpert j_178209b3` | 这里开始把 movement success 过度等同于 deploy-ready |
| 30 | 3.482s | deploy waiting | 等待 | 正常 |
| 31 | 7.738s | movement succeeded；deploy 仍 waiting/resource_lost | 仍选择继续等待 deploy | 将 movement success 当成“部署即将成功”的强证据 |
| 32 | 5.808s | deploy 长时间 waiting | abort `j_178209b3` | 又进 retry |
| 33 | 3.166s | abort 成功 | 起 `DeployExpert j_6b08cdb3` | retry |
| 34 | 3.297s | deploy waiting | 等待 | retry |
| 35 | 6.276s | deploy 仍 waiting/resource_lost | 开始猜“也许已部署成功，只是没传播”并查 structures | 这里 narrative 已压过显式 abort/waiting 信号 |
| 36 | 6.496s | 查到仍只有 MCV，没有 yard | abort `j_6b08cdb3`，改成微调位置 `[64,95]` | 合理 fallback，但仍是自由试错 |
| 37 | 3.025s | abort 成功 | 起 `DeployExpert j_928437c2` | retry |
| 38 | 3.807s | deploy waiting | 等待 | retry |
| 39 | 8.263s | deploy 继续 waiting/resource_lost | 这轮选择“什么也不做，再等等” | escalation policy 不清晰 |
| 40 | 8.602s | 长时间没有 deploy 成功证据 | 直接 `complete_task(succeeded)` | 这是最严重的问题：无证据成功判定 |

### 4.3 这条 trace 暴露出的关键问题

1. `展开` 没有 phase policy。
2. deploy 相关状态没有结构化成功/失败证据。
3. repeated waiting/resource_lost loop 没有被压缩成高语义 runtime facts。
4. movement success 被错误地当成“接近 deploy success”。
5. narrative summary 可以盖过明确的 deploy failure / abort history。

## 5. Signal 语义与顺序问题

你指出的 `resource_lost` 非常关键，而且这条观察是成立的。

### 5.1 现象

在多个 `DeployExpert` job 上，落盘时序是：

- `expert_signal resource_lost`
- `signal_routed resource_lost`
- `job_started`

例如：

- `j_76966359`
- `j_1fc66fff`
- `j_178209b3`
- `j_6b08cdb3`
- `j_928437c2`

都表现出这个顺序。

### 5.2 根因

在 `kernel/core.py` 里，`start_job()` 的顺序是：

1. 创建 controller
2. 放入 `_jobs`
3. `_rebalance_resources()`
4. `_sync_world_runtime()`
5. 记录 `job_started`

而 `_rebalance_resources()` 会触发资源回调，Job 可能在这一步就发出 `resource_lost`。

所以从 log 视角看，TaskAgent/Diagnostics 会先看到：

- 资源丢失/没拿到

再看到：

- job 已启动

这会污染 agent 的因果建模，也会污染人类调试的直觉。

### 5.3 影响

这不是“日志顺序不好看”这么简单，而是：

- LLM 会误以为 job 一出生就失败
- 或误以为 runtime 自相矛盾
- 导致它更加依赖 narrative guess，而不是稳定状态机

## 6. 当前日志为什么这么长

有两类原因。

### 6.1 正常原因

- 现在确实把 `context_snapshot`
- `llm_input`
- `tool_execute`
- `tool_execute_completed`
- `llm_reasoning`
- `signal_routed`

都落盘了，所以可见性显著提高。

### 6.2 无效重复原因

同样非常多，主要包括：

- 固定 `SYSTEM_PROMPT` 被重复发送
- 旧 assistant/tool 消息被反复回放
- 每轮都重新注入完整 context
- 同一类 `waiting/resource_lost` narrative 被多轮重新解释

在这条 task 里，后期 `llm_input` 已膨胀到：

- `80` 条 message
- 约 `79,225` 字符

这说明：

- log 长不等于信息质量高
- 其中确实包含大量重复和低增益内容

## 7. 现在 task 能不能跟玩家说话

不能直接说，只能间接说。

当前 tool 集合是：

- `start_job`
- `patch_job`
- `pause_job`
- `resume_job`
- `abort_job`
- `complete_task`
- `create_constraint`
- `remove_constraint`
- `query_world`
- `query_planner`
- `cancel_tasks`

没有：

- `send_task_message`
- `ask_player`
- `notify_player`

这意味着：

- Task 中途不能正式通过 Adjutant 对玩家说 `task_info / task_question`
- 只能靠少量 `TaskMessage` 或最终 `complete_task`

这和设计目标存在漂移。

## 8. 现状与理想设计的漂移

当前实现更像：

- 一个带 tool use 的 per-task LLM
- 控若干 job
- 通过弱 signal 做中途判断

设计上更理想的形态应该是：

- `TaskAgent` 先拿到明确 phase policy
- 初始只打开必要的信息源
- 需要时按需订阅更多信息 Expert
- 通过正式 `TaskMessage` 与玩家交互
- 用硬 success criteria 决定成功/失败

当前主要漂移有：

1. 没有“信息 Expert / 订阅”层。
2. 没有正式 task-to-player 对话 tool。
3. 没有 per-task phase template。
4. success criteria 太弱。
5. signal 顺序会误导 agent。

## 9. 我对你这 8 点问题的直接回答

### 9.1 每一步都一样的 prompt 吗？所有什么专家都告诉了吗？

是，system prompt 基本固定；每次 LLM 调用都会重新带一遍。  
是，当前会把主要 ExecutionExpert 全告诉，但不会把“这条任务该用哪些信息源/哪些阶段”说清楚。

### 9.2 原始 log 很长，是不是无效信息也很多？

是。  
长的原因一半是可见性提升，一半是重复 prompt、重复上下文、重复历史消息。

### 9.3 是否有重复 prompt 注入？

有。  
不是同一条消息内双写，而是：

- 固定 system prompt 每轮重复
- 累积 conversation history 持续回放
- 每个 wake 再打一份 context update

### 9.4 是否应该做信息 Expert，初始只带部分，后续按需订阅？

应该。  
当前没有这层，这是明显缺口。

### 9.5 初始 Adjutant 分配 task 时，是否应该预开第一批信息 Expert？

应该。  
例如：

- deploy/expand 类任务：
  - `base_state`
  - `mcv_state`
  - `buildability`
- combat 类任务：
  - `target_visibility`
  - `threat_summary`
- recon 类任务：
  - `awareness_status`
  - `map_progress`

### 9.6 `resource_lost` 信息不足，且顺序反直觉，这个问题是否存在？

存在，而且已确认：

- 语义不够强
- 顺序还会先于 `job_started`

### 9.7 `job` 和 `Expert` 概念是否混淆？

系统内部有区分，但对 LLM 和用户暴露得不够清楚。  
概念上应明确：

- `Expert` = 类型
- `Job` = Expert 的实例

### 9.8 `Movement success` 被当成接近 `deploy success`，甚至 narrative 能压过 explicit abort，这个问题是否成立？

成立。  
`Task #001` 这条 trace 已经证明了这一点。

## 10. 修复建议

### P0

1. 给 `Deploy / Expand / Establish Base` 加 phase policy。
2. 给 deploy 引入硬 success guard：
   - 没有 yard / 没有 deploy success signal / MCV 仍存在  
   - 就不能 `complete_task(succeeded)`
3. 修正 `job_started` 与资源信号的顺序，或至少在 context 中补一个更高语义的状态汇总。

### P1

4. 引入“信息 Expert / 信息 Manager”层，支持订阅。
5. `Adjutant` 创建 task 时预开一批初始信息源。
6. 用结构化 runtime facts 取代让 LLM自己从粗 summary 猜：
   - `has_yard`
   - `mcv_present`
   - `deploy_confirmed`
   - `retry_count`
   - `phase`

### P2

7. 增加 `send_task_message / ask_player` tool，恢复设计里 task 通过 Adjutant 说话的能力。
8. 缩减每轮注入的无效上下文，控制 conversation 膨胀。

## 11. 最终判断

这条 `Task #001` 不是“模型天然不行”的最好证据，反而是“当前 agent 设计对模型太不友好”的最好证据。

如果不先修：

- prompt 约束
- context 结构
- signal 顺序
- success guard

那么即使换更强模型，也仍然容易在这类 task 上：

- 漂阶段
- 重复试错
- 错判成功

相反，如果把这四层收紧，当前模型的表现会显著稳定。

