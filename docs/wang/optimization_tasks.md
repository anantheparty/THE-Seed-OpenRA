# 系统优化任务清单

日期：2026-04-04（v3 — 整合 Xi 补全审计结果）

核心原则：**Task Agent 的决策问题通过提供充足信息解决，不通过行为约束或权限限制。** 信息充分的 agent 自然会做出正确决策；信息不足时应主动问玩家。

审计来源：Wang 系统审计 + Yu runtime 分析 + Xi 全量代码审计（15 项 50 子项）

---

## P0 — 信息质量（根因）

### T1: 结构化 Runtime Facts 注入

**问题：** TaskAgent 只有粗粒度 world_summary（economy/military/map/known_enemy），缺少关键决策信息。"展开" 时 LLM 不知道场上只有一个 MCV、没有基地、没有任何建筑，所以把"展开"理解成"战略扩张"去做侦察。

**正确行为：** 如果 context 告诉 LLM "你有 1 个 MCV，0 个建筑，0 个其他单位"，它自然知道"展开"就是 deploy MCV。如果基地已展开但不清楚展开什么，它自然会问玩家。

**目标：** 每次 wake 时 context 包含结构化的、面向决策的 runtime facts。

**具体改动：**
1. `task_agent/context.py` — ContextPacket 新增 `runtime_facts: dict`，包含：
   ```python
   runtime_facts = {
       # 基地状态
       "has_construction_yard": bool,
       "has_power": bool,
       "has_barracks": bool,
       "has_refinery": bool,
       "has_war_factory": bool,
       "has_radar": bool,
       "tech_level": int,           # 0=无基地, 1=yard, 2=有生产, 3=有科技

       # 关键单位
       "mcv_count": int,
       "mcv_idle": bool,            # MCV 存在且空闲
       "harvester_count": int,

       # 资源
       "can_afford_power_plant": bool,
       "can_afford_barracks": bool,
       "can_afford_refinery": bool,

       # 任务相关
       "active_task_count": int,
       "this_task_jobs": [{"job_id", "expert_type", "status", "phase"}],
       "failed_job_count": int,      # 本 task 内已失败的 job 数
       "same_expert_retry_count": int, # 同类型 Expert 连续重试次数
   }
   ```
2. `world_model/core.py` — 新增 `compute_runtime_facts()` 方法，从 actors + economy 计算上述 facts
3. `task_agent/agent.py` — `_build_context()` 时调用并注入 runtime_facts
4. SYSTEM_PROMPT 告诉 LLM："runtime_facts 是精确的结构化状态，优先参考这些而非从 world_summary 推断"

**验收：**
- "展开" + MCV 存在 + 无 yard → LLM 第一步直接 query_world 找 MCV → DeployExpert
- "展开" + 已有 yard + 无可 deploy 单位 → LLM 问玩家"展开什么？"
- LLM 调用次数从 40 降到 <10

---

### T2: Task→Player 通信工具

**问题：** TaskAgent 无法主动与玩家沟通。信息不足时无法问、执行出错时无法说。design.md §6 定义了 task_info / task_warning / task_question / task_complete_report 四种消息，均未实现。

**Xi 审计补充 (10.5)：** ChatView 完全没有 pending_question 文本模式处理。设计要求 pending_question 在聊天流中以文本出现，当前仅 TaskPanel 侧栏有按钮。用户若不看侧栏将错过待回答问题。

**目标：** Task 执行期间可向玩家发消息、问问题。信息不足时主动问而不是猜。

**具体改动：**
1. `task_agent/tools.py` — 新增 `send_task_message` tool：
   ```
   send_task_message(type: "info"|"warning"|"question", content: str,
                     options?: list[str], timeout_s?: float, default_option?: str)
   ```
2. `task_agent/handlers.py` — `handle_send_task_message()` 调 Kernel 转发
3. `kernel/core.py` — `forward_task_message()` → 推送到 Adjutant 和 WS
4. `adjutant/adjutant.py` — 收到 task_question 时注册 pending_question
5. `ws_server/server.py` — 新消息类型 task_message 推送到前端
6. **前端 ChatView** — 渲染 task 消息（info/warning 直接显示，question 带选项按钮）
   - **新增：** pending_question 文本模式 — question 消息在聊天流中显示为带选项的交互卡片
7. SYSTEM_PROMPT 增加引导："当你不确定玩家意图时，使用 send_task_message(question) 询问"

**验收：**
- "展开" 在歧义场景下 TaskAgent 问 "你是要展开基地车，还是别的？"，玩家回复路由回 TaskAgent
- pending_question 在 ChatView 聊天流中可见，不仅限于 TaskPanel 侧栏

---

### T3: DeployExpert 结果验证

**问题：** DeployExpert 调 deploy_units() 后立即标记 SUCCEEDED，不验证 Construction Yard 是否实际出现。这不是权限问题——是信息回馈问题，Expert 自己都不知道自己是否成功。

**目标：** Deploy 有可靠的成功/失败信息反馈。

**具体改动：**
1. `experts/deploy.py` — 不立即 SUCCEEDED，改为 `self.phase = "verifying"`
2. 后续 tick 中 query WorldModel：
   - 检查是否出现 category=building 的 Construction Yard
   - 检查原 MCV actor 是否消失
3. 验证成功 → SUCCEEDED（signal 含 yard actor_id）
4. 超时 5s 未见 yard → FAILED（signal 含 "deploy_command_sent_but_no_yard_appeared"）

**验收：** Deploy 结果与游戏实际状态一致。TaskAgent 收到的 signal 是准确的。

---

## P0 — 可靠性

### T10: LLM Provider Timeout & Retry

**来源：** Xi 审计 9b + 9c

**问题：** `llm/provider.py` 的 `QwenProvider.chat()` 和 `AnthropicProvider.chat()` **均无 timeout 参数、无 asyncio.wait_for、无 httpx timeout 配置**。无重试循环、无指数退避、无瞬态错误捕获。

**影响分析：**
- task_agent 层有 `asyncio.wait_for` + max_retries 弥补（Xi 11a 确认），所以 Task Agent 路径安全
- **但 adjutant 的 query/classify 路径直接调 provider，无 timeout/retry 保护** — 卡死的 API 调用会阻塞事件循环无限期
- 429/500/503 瞬态错误在 adjutant 路径直接传播为未处理异常

**目标：** LLM provider 层有统一的 timeout + retry，所有调用路径均受保护。

**具体改动：**
1. `llm/provider.py` — `chat()` 方法增加 `timeout_s` 参数（默认 30s）
2. `QwenProvider.chat()` — 使用 `asyncio.wait_for(self._client.chat(...), timeout=timeout_s)`
3. `AnthropicProvider.chat()` — 同上，或使用 httpx timeout 配置
4. 两个 Provider 增加重试循环：最多 2 次重试，指数退避 (1s, 2s)，仅重试 429/500/502/503
5. 非重试异常（400/401/404）直接抛出

**验收：**
- adjutant query 在 LLM API 卡死时 30s 内超时返回
- 瞬态 429 自动重试成功
- 非瞬态错误立即抛出

---

## P1 — 效率和质量

### T4: Conversation History 压缩

**问题：** TaskAgent conversation 无限增长。Task #001 从 4.6K 字符膨胀到 79K。后期 90% 是重复信息，LLM 在噪声中做决策。

**目标：** 控制 conversation 在合理范围内，提高信息密度。

**具体改动：**
1. `task_agent/agent.py` — `_build_messages()` 实现滑动窗口：
   - 保留 system prompt + 最近 N 轮完整对话 (N=6)
   - 超出部分压缩为单条 summary message
2. 相同类型 signal 去重：连续 5 个 resource_lost → "resource_lost repeated ×5"
3. tool result 中大 payload（完整 actor 列表）截断为摘要

**验收：** Conversation 最大不超过 20K 字符。

---

### T5: Signal 日志顺序修正

**问题：** `kernel/core.py:start_job()` 中 `_rebalance_resources()` 在 `job_started` 日志之前执行，导致 LLM 看到 resource_lost 先于 job_started。信息顺序错误 = 给 LLM 错误的因果链。

**目标：** LLM 看到的事件顺序符合因果逻辑。

**具体改动：**
1. `kernel/core.py:start_job()` — 将 `slog.info("Job started")` 移到 `_rebalance_resources()` **之前**
2. 或者：在 context 构建时对 recent_signals 排序，job_started 优先于同 job 的 resource_lost

**验收：** 日志中 job_started 始终先于同 job 的 resource_lost。

---

### T6: Smart Wake — 无增量跳过 LLM

**问题：** 89% 的 TaskAgent 唤醒是 review_interval 定时轮询，无新信息也触发 LLM 调用。浪费 token 且无决策价值。

**目标：** 无信息增量时跳过 LLM 调用。

**具体改动：**
1. `task_agent/agent.py` — wake 时检查：
   - 自上次 wake 以来是否有新 signal/event
   - 如果无新信息且所有 job 状态未变 → 跳过 LLM，直接 sleep
2. `game_loop/loop.py` — review_wake 标记 `trigger="review"` 以区分

**验收：** 无信息增量的 review wake 不触发 LLM 调用。LLM 调用从 40 降到 <15。

---

### T11: Adjutant 降级路由修复

**来源：** Xi 审计 14b + 14f

**问题：** `adjutant.py` 的 `_rule_based_classify()` 方法无法产出 "reply" 类型。当 LLM 分类不可用时（故障/超时），玩家对 pending_question 的回复（如"继续"、"放弃"）会被误分类为新命令，创建不必要的新 Task。

**目标：** 降级模式下 reply 仍能正确路由。

**具体改动：**
1. `adjutant/adjutant.py` — `_rule_based_classify()` 增加 reply 检测：
   - 如果存在 pending_question 且输入匹配某个 option → 分类为 reply
   - 匹配逻辑：精确匹配 option 文本 + 模糊匹配（"继续"/"放弃"/"是"/"否" 等常见回复词）
2. 确保 "继续" 在 LLM 不可用时也路由给对应 Task 的 pending_question

**验收：** LLM 故障 + pending_question 场景下，"继续"/"放弃" 正确路由为 reply，不创建新 Task。

---

### T12: WS 消息频率控制

**来源：** Xi 审计 7a

**问题：** `ws_server/server.py` 的 `publish_dashboard()` 在每个 10Hz tick 被调用。design.md 要求 world_snapshot 和 task_list 为 1Hz，但实际可能达到 10Hz（虽有 concurrent-publish guard 做轻度限流但非精确 1Hz）。前端消息量可能是设计的 10 倍。

**目标：** world_snapshot 和 task_list 严格 1Hz 发送。

**具体改动：**
1. `ws_server/server.py` — `publish_dashboard()` 中为 world_snapshot 和 task_list 增加 last_sent_at 时间戳检查
2. 距上次发送不足 1s → 跳过。task_update / log_entry / player_notification 保持实时

**验收：** 1 秒内 world_snapshot 和 task_list 各仅发一次。前端消息量降低 ~90%。

---

## P2 — 架构完善

### T7: Information Expert 实现

**问题：** design.md 定义了 Information Expert（ThreatAssessor、EconomyAnalyzer、MapSemantics），实际零实现。T1 的 runtime_facts 是快速方案，Information Expert 是完整架构。

**目标：** 实现至少 2 个 Information Expert，持续分析 WorldModel 产出派生信息。

**具体改动：**
1. `experts/info_base_state.py` — BaseStateExpert:
   - 输出：has_yard, has_power, tech_level, base_established
   - 事件驱动更新
2. `experts/info_threat.py` — ThreatAssessor:
   - 输出：threat_level, threat_direction, enemy_composition
   - 定期 + ENEMY_DISCOVERED 事件更新
3. 注册到 WorldModel 或独立运行，产出通过 context 注入 TaskAgent

**验收：** TaskAgent context 中有来自 Information Expert 的派生分析数据。

---

### T8: OpenRA 知识补全

**问题：** experts/knowledge.py 已有基础 hard facts (P0 全部完成)，缺 soft strategy。

**当前已完成：** 低电恢复、队列阻塞、矿场经济包、雷达感知、侦察策略分级、无目标回退、车辆工厂检测。

**仍缺：**
1. 开局模板 (E14)：power → barracks → refinery → war factory 标准序列
2. 科技前置条件 (E15)：升科技前需要防御/经济覆盖
3. 放置策略 (E12)：建筑靠近矿区
4. UnitRegistry 数据利用：cost 用于评分，prerequisites 用于可建性判断
5. 反制推荐：根据敌人构成推荐对应兵种

**具体改动：**
1. `experts/knowledge.py` — 新增 opening_template / tech_preconditions / counter_unit_for
2. `experts/planners.py` — ProductionAdvisor 使用开局模板
3. 引入 UnitRegistry 的 cost/prerequisites 数据

**验收：** ProductionAdvisor 对空基地推荐标准开局序列。

---

### T9: Adjutant 可观测性

**问题：** 整个 session 仅 2 条 Adjutant 日志。分类决策、路由逻辑完全不可见。

**Xi 审计补充 (5d)：** `NotificationManager` 是死代码 — adjutant.py 中有完整实现但 main.py 发布路径绕过了它，直接格式化。通知缺少 icon/severity 元数据。

**具体改动：**
1. `adjutant/adjutant.py` — 在 rule_match、LLM classification、路由决策、NLU routing 位置加 slog
2. 目标：每条玩家输入 3-5 条结构化日志
3. 清理或启用 NotificationManager（启用并使用 → 通知获得 icon/severity；或删除死代码）

**验收：** 每条玩家输入在 Adjutant 组件下有完整处理链日志。

---

### T13: Constraint 系统完整实现

**来源：** Xi 审计 12b + 12c

**问题：** Constraint 是玩家对游戏行为的约束（如"别追太远"→ `do_not_chase`、"经济优先"→ `economy_first`），由 Task Agent 创建、Job 执行时遵守。当前：
1. `ESCALATE` enforcement 是死代码 — 枚举存在但整个代码库中零运行时分支逻辑
2. 5 个 Expert 中仅 CombatJob 在 tick 中调用 `get_active_constraints()`，其余 4 个（Recon/Economy/Movement/Deploy）完全忽略 constraint

**影响：** 玩家说"别追太远"只对 CombatJob 生效。玩家说"经济优先"完全被静默忽略。这是玩家意图传递链的断裂。

**目标：** 玩家设定的 constraint 在所有相关 Expert 中正确生效。

**具体改动：**
1. ESCALATE enforcement → Signal(CONSTRAINT_VIOLATED) 发送给 Task Agent 决策
2. ReconJob — 读取 constraint，处理 `defend_base`（不离开基地太远）
3. EconomyJob — 读取 constraint，处理 `economy_first`（优先生产经济单位）
4. MovementJob — 读取 constraint，处理 `do_not_chase`（限制移动范围）
5. DeployJob — 评估是否有适用 constraint（可能无需处理）

**验收：** 玩家说"别追太远"→ 所有 Job 遵守距离限制。"经济优先"→ EconomyJob 调整生产优先级。ESCALATE 约束触发 Signal 反馈给 Task Agent。

---

### T14: 前端功能补全（取消 + 语音）

**来源：** Xi 审计 10.2 + 10.9

**问题：**
1. command_cancel — 后端支持但前端无取消按钮/交互，用户无法从 UI 取消正在执行的任务
2. ASR/TTS — design.md 要求基础框架支持，当前零代码

**目标：** 前端交互完整，含语音输入/输出。

**具体改动：**
1. 前端 TaskPanel — 每个活跃 Task 卡片增加取消按钮，发送 command_cancel
2. ASR 实现：
   - ChatView 输入框旁增加麦克风按钮
   - 使用 Web Speech API (`SpeechRecognition`) 或接入第三方 ASR（如 Whisper API）
   - 识别结果填入输入框，用户确认后发送（或可配置自动发送）
   - 支持中文语音识别
3. TTS 实现：
   - 系统消息（task_info/task_warning/task_complete_report）可朗读
   - 使用浏览器 `SpeechSynthesis` API 或第三方 TTS
   - 可配置开关（默认关闭，用户手动开启）

**验收：** 用户可从 UI 取消任务。可通过语音下达命令。系统关键消息可语音播报。

---

## P2 — 小修复（可合并到相关任务中）

### T15: 零散修复

**来源：** Xi 审计杂项

| # | 来源 | 问题 | 修复 |
|---|---|---|---|
| 4c | world_model | `find_actors()` 缺 mobility 过滤 | 增加 `mobility` 参数 |
| 5d | adjutant | `format_task_message()` 死代码 | 删除或启用（与 T9 合并） |
| 6e | game_api | SOCKET_TIMEOUT 是 per-recv 非 per-request | 低风险，添加注释说明即可 |
| 11b | 错误恢复 | GameAPI 持续断连无告警升级 | 添加超时升级: 连续断连 >30s → player_notification |
| 14a | main.py | Adjutant 可选导致绕过路由 | 低风险（开发便利），添加注释 |
| 14d | adjutant | 多问题拆分回复未实现 | 当前 fallback 行为可接受，记录 TODO |

---

## 依赖关系

```
T1  (Runtime Facts)         — 独立，最高优先
T2  (Task→Player 通信)      — 独立，与 T1 互补（信息不足时问）
T3  (Deploy 验证)            — 独立
T10 (LLM Provider 可靠性)   — 独立，影响 adjutant 路径
T4  (Conversation 压缩)     — 独立
T5  (Signal 顺序)           — 独立
T6  (Smart Wake)            — 独立
T11 (Adjutant 降级路由)     — 独立
T12 (WS 频率控制)           — 独立
T7  (Info Expert)           — T1 的架构完善版
T8  (知识补全)              — 独立
T9  (Adjutant 可观测性)     — 独立
T13 (Constraint 清理)       — 独立
T14 (前端补全)              — 独立
T15 (零散修复)              — 可合并到相关任务
```

**建议执行顺序：** T1 → T2 → T3 → T10 → T5 → T4 → T6 → T11 → T12 → T8 → T7 → T9 → T13 → T14 → T15

---

## Xi 审计合规统计

总计 50 子项：✅ 31 (62%) | ⚠️ 14 (28%) | ❌ 5 (10%)

**❌ 未实现 (5)：**
- 9b: LLM provider 无 timeout → T10
- 9c: LLM provider 无 retry → T10
- 12b: escalate enforcement 死代码 → T13
- 10.5: ChatView pending_question 文本模式 → T2
- 10.9: ASR/TTS 框架 → T14

**⚠️ 需关注 (14)：** 7a→T12, 12c→T13, 14b→T11, 其余为低风险（设计简化/超集行为/命名差异等）
