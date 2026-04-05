# 完整补全系统审计报告

**审计人：** xi
**日期：** 2026-04-04
**方法：** 对照 `docs/wang/design.md` 逐一验证代码实现
**范围：** 15 项遗漏（10 个模块 + 5 个维度）

> **⚠ 文档时效性说明 (2026-04-06 更新)**
>
> 本报告基于 2026-04-04 代码快照。此后有多轮提交改变了部分结论。
> **不要将本文当作当前事实全量快照**，以下为已关闭/变更的项目：
>
> | 审计项 | 原结论 | 当前状态 (2026-04-06) |
> |---|---|---|
> | 4c find_actors 缺 mobility | ⚠️ 缺失 | ✅ **已关闭** — `find_actors()` 已添加 `mobility` 参数 (commit 4c865ef) |
> | 9b LLM 无 timeout | ❌ 未实现 | ⚠️ **仍有效** — provider 层仍无 timeout，但 task_agent 层 `asyncio.wait_for` + Adjutant `classification_timeout`/`query_timeout` 提供双层保护 |
> | 9c LLM 无 retry | ❌ 未实现 | ⚠️ **仍有效** — provider 层仍无 retry，task_agent 层 `max_retries=1` 弥补 |
> | 10.9 ASR/TTS 缺失 | ❌ 未实现 | ⚠️ **部分关闭** — voice 子系统已添加 (commit 295b94b)，阿里云 ASR/TTS 集成中 |
> | 12b escalate 死代码 | ❌ 未实现 | ❌ **仍有效** — escalate enforcement 仍为死代码 |
> | 10.5 ChatView pending_question | ❌ 未实现 | ❌ **仍有效** — 文本模式仍缺失 |
> | Adjutant stale-world | (未审计) | ✅ **新增** — Yu 添加了 stale-world guards，Adjutant 在 WorldModel 状态过期时 fail-closed |
> | Capability Task 架构 | (未审计) | ✅ **新增** — Phase 1-4 完成，register_unit_request 完整实现 |
>
> 未列出的审计项结论仍然有效。如需确认具体项，请对照当前代码验证。

---

## 合规统计

| 合规等级 | 数量 | 占比 |
|---|---|---|
| ✅ 一致 | 31 | 62% |
| ⚠️ 部分漂移 | 14 | 28% |
| ❌ 未实现 | 5 | 10% |

**高风险项：** 4 个（9b LLM 无 timeout, 9c LLM 无 retry, 12b escalate 死代码, ChatView 缺 pending_question 文本模式）

---

## 模块级审计 (Items 1–10)

### [1] main.py — Runtime 组装层

#### 1a. RuntimeBridge 组装 Kernel/Adjutant/WS/GameLoop

**design.md 要求：** RuntimeBridge 是薄集成层，组装所有组件
**代码实现：** `main.py:142–636` — `ApplicationRuntime.__init__` 创建并连接所有组件。`bridge.attach_ws_server()` + `bridge.attach_runtime()` + `game_loop._dashboard_callback = bridge.on_tick` 完成连线
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 1b. WS 入站消息路由

**design.md 要求：** `command_submit`, `command_cancel`, `mode_switch` 三种入站消息
**代码实现：** `ws_server/server.py:161–191` 分发 7 种入站类型。`main.py:243–287` 实现 `on_command_submit`, `on_command_cancel`, `on_mode_switch` + 4 种扩展类型
**合规：** ✅
**漂移细节：** 实际有 7 种入站类型（额外 question_reply/game_restart/sync_request/session_clear），设计要求的 3 种全部覆盖
**风险：** 无

#### 1c. 启动顺序

**design.md 要求：** §2: `GameAPI → UnitRegistry → WorldModel → Kernel → Dashboard(WS) → GameLoop`
**代码实现：** `ApplicationRuntime.__init__` 构造顺序：GameAPI(573) → UnitRegistry(574) → WorldModel(583) → Kernel(599) → **GameLoop(623)** → **WSServer(637)**。但 `start()` 中 WS 先启动(652)再创建 loop task(656)
**合规：** ⚠️
**漂移细节：** 构造顺序 GameLoop 在 WSServer 之前，与设计相反。但运行时启动顺序正确
**风险：** 低 — 构造顺序不影响功能

#### 1d. 关闭序列

**design.md 要求：** 正确释放资源
**代码实现：** `ApplicationRuntime.stop()` (659–668): loop停止(2s超时) → API关闭 → WS关闭 → 导出报告 → 事件通知
**合规：** ✅
**漂移细节：** 无
**风险：** 无

---

### [2] game_loop/loop.py — 主循环

#### 2a. Tick 执行顺序

**design.md 要求：** §2: `refresh → events → route → job tick → push dashboard`
**代码实现：** `GameLoop._tick()` (202–237): refresh(209) → detect_events(212) → route_events(216) → kernel.tick(220) → _tick_jobs(226) → _check_agent_reviews(229) → queue_manager.tick(232) → dashboard_callback(237)
**合规：** ✅
**漂移细节：** 额外插入 kernel.tick / _handle_world_model_health / _check_agent_reviews / queue_manager.tick，均为合理扩展，不破坏设计顺序
**风险：** 无

#### 2b. Job tick_interval

**design.md 要求：** Combat 0.2s, Recon 1.0s, Economy 5.0s
**代码实现：** `experts/combat.py:55` → 0.2, `experts/recon.py:33` → 1.0, `experts/economy.py:55` → 5.0
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 2c. asyncio 协作

**design.md 要求：** 单线程 GameLoop，与 asyncio 配合
**代码实现：** `GameLoop.start()` 是 async def，阻塞操作用 `await asyncio.to_thread(...)` 卸载
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 2d. 10Hz 默认 tick rate

**design.md 要求：** 默认 10Hz
**代码实现：** `GameLoopConfig.tick_hz = 10.0` (loop.py:64)，`RuntimeConfig.tick_hz = 10.0` (main.py:64)
**合规：** ✅
**漂移细节：** 无
**风险：** 无

---

### [3] kernel/core.py — 未审部分

#### 3a. cancel / cancel_tasks

**design.md 要求：** `Kernel.cancel(task_id)` 或 `Kernel.cancel_tasks(filters)` → 批量取消
**代码实现：** `kernel/core.py:235` — 方法名为 `cancel_task(task_id)` (非 `cancel`)。`cancel_tasks(filters)` (254) 存在并正确遍历+过滤。`_task_matches_filters` (762) 支持 task_ids/kind/priority_below/status
**合规：** ⚠️
**漂移细节：** 单任务取消方法名 `cancel_task` vs 设计的 `cancel`，语义一致但命名不同。`cancel_task` 未作为 tool 暴露（仅 `cancel_tasks` 在 tool 注册表中）
**风险：** 低

#### 3b. 冲突仲裁 / 抢占

**design.md 要求：** 多 Task 争资源 → 按优先级。高优先级夺低优先级：单资源 Job → abort，多资源 Job → degrade
**代码实现：** `_rebalance_resources()` (862) 按 `(-priority, created_at)` 排序。`_find_preemptable_resource()` (926) 仅抢占严格低优先级。`_preempt_resource()` (967) 单资源 `len(resources) <= 1` → abort，多资源 → `on_resource_revoked` 降级
**合规：** ✅
**漂移细节：** 无。单/多资源抢占行为完全匹配设计
**风险：** 无

#### 3c. 事件路由规则

**design.md 要求：**
- UNIT_DIED/UNIT_DAMAGED: actor_id 匹配 Job.resources → 路由该 Job
- ENEMY_DISCOVERED/STRUCTURE_LOST: 广播所有活跃 Task Agent
- BASE_UNDER_ATTACK: 触发预注册规则 + 广播
- ENEMY_EXPANSION/FRONTLINE_WEAK/ECONOMY_SURPLUS: 推 player_notification，不路由
- 无法匹配 → 丢弃

**代码实现：** `kernel/core.py:340–362`:
- UNIT_DIED/UNIT_DAMAGED → `_route_actor_event` (347)
- ENEMY_DISCOVERED/STRUCTURE_LOST → `_broadcast_event` (350)
- BASE_UNDER_ATTACK → `_apply_auto_response_rules` (343) + `_broadcast_event` (353)
- ENEMY_EXPANSION/FRONTLINE_WEAK/ECONOMY_SURPLUS → `_push_player_notification` (356)
- PRODUCTION_COMPLETE → `_rebalance_resources` (359)
- 其他 → return None (丢弃)

**合规：** ⚠️
**漂移细节：** `_route_actor_event` (1052–1075) 不仅路由给 Job，还路由给 Task Agent (`runtime.agent.push_event`)。设计仅说"路由给该 Job"。这是扩展行为（additive），不是遗漏
**风险：** 低 — 额外路由给 Task Agent 是有益的

#### 3d. 被动事件自动响应: defend_base

**design.md 要求：** BASE_UNDER_ATTACK → 自动创建 Task(kind=managed, raw_text="defend_base", priority=80)
**代码实现：** `_handle_base_under_attack_auto_response` (1156–1158) → `_ensure_defend_base_task()` (1089–1093) 检查已有 defend_base 任务，无则 `create_task("defend_base", MANAGED, 80)` → `_ensure_immediate_defend_base_job()` (1131–1154) 立即创建 CombatExpert(HOLD)
**合规：** ✅
**漂移细节：** 代码额外创建即时 CombatJob(HOLD)，超出设计范围但符合意图
**风险：** 无

#### 3e. pending_question 超时

**design.md 要求：** Kernel 持有定时器，每 tick 检查，超时 → 自动发 PlayerResponse(answer=default_option)
**代码实现：** `tick()` (590–600) 扫描 `_pending_questions`，过期则调 `_expire_pending_question` (1183–1195) → `_deliver_player_response(answer=pending.default_option)`。`register_task_message` (518–522) 设置 `deadline_at = timestamp + timeout_s`
**合规：** ✅
**漂移细节：** 无
**风险：** 无

---

### [4] world_model/core.py — 未审部分

#### 4a. 分层刷新间隔

**design.md 要求：** §2: actor 每 tick，economy 500ms，map 1s
**代码实现：** `RefreshPolicy` (93–97): `actors_s=0.1` (100ms), `economy_s=0.5`, `map_s=1.0`
**合规：** ⚠️
**漂移细节：** Actor 刷新间隔 100ms vs 设计的 "每 tick"。在 10Hz GameLoop 下差异不大（tick=100ms），但设计原文意为每次 tick 都刷新
**风险：** 低 — 10Hz 下等效

#### 4b. detect_events() 事件类型覆盖

**design.md 要求：** 9 种事件类型全覆盖
**代码实现：** 所有 9 种均已实现：UNIT_DIED(725), STRUCTURE_LOST(727), ENEMY_DISCOVERED(748), UNIT_DAMAGED(763), ENEMY_EXPANSION(792), BASE_UNDER_ATTACK(805), PRODUCTION_COMPLETE(838), FRONTLINE_WEAK(862), ECONOMY_SURPLUS(880)
**合规：** ✅
**漂移细节：** 额外实现 GAME_RESET(667)
**风险：** 无

#### 4c. find_actors() predicates 匹配

**design.md 要求：** `find_actors(predicates={category: vehicle, mobility: fast})`
**代码实现：** `find_actors()` (333–371) 有 `category` 参数过滤(353)。**无 `mobility` 参数**。NormalizedActor 有 mobility 字段(607)但 find_actors 不支持按 mobility 过滤
**合规：** ⚠️
**漂移细节：** mobility 过滤缺失，调用方须手动 post-filter
**风险：** 中 — LLM 若按设计 spec 生成 tool call 会失败

#### 4d. runtime_state() 返回内容

**design.md 要求：** 返回 active tasks/jobs, resource bindings, constraints
**代码实现：** `runtime_state()` (430–437) 返回 active_tasks, active_jobs, resource_bindings, constraints, timestamp
**合规：** ✅
**漂移细节：** 无
**风险：** 无

---

### [5] adjutant/ — 未审部分

#### 5a. Query 路径

**design.md 要求：** §6: "查询 → Adjutant 直接 LLM+WorldModel 回答，不进 Kernel"
**代码实现：** `adjutant.py:832–865` — `_handle_query()` 调 `world_model.world_summary()` + `llm.chat()` (QUERY_SYSTEM_PROMPT)，15s timeout，无 Kernel 参与
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 5b. pending_question 流程

**design.md 要求：** 注册 → tick 检查 → 超时发 default → 迟到回复处理
**代码实现：** 完整 4 步实现：`register_task_message()` (518) → `tick()` (590) → `_expire_pending_question()` (1183) → `submit_player_response()` 检查 `_timed_out_questions` 返回"已按默认处理"(542–550)
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 5c. runtime_nlu.py (429 行)

**design.md 要求：** NLU 运行时处理完整逻辑
**代码实现：** 双门槛置信度 (model + router)，支持 deploy_mcv/produce/explore/mine/stop_attack/query_actor 6 种直接意图，复合序列路由带 gate flag
**合规：** ✅
**漂移细节：** `mine`/`stop_attack` 意图需 `game_api`，为 None 时 RuntimeError
**风险：** 中 — mock 模式下这两个意图会崩溃

#### 5d. notifications.py (151 行)

**design.md 要求：** 通知管理
**代码实现：** `NotificationManager` 类完整实现 poll/push/格式化。**但 main.py 未使用它**。`main.py:386–392` 的 `_publish_notifications()` 直接用偏移量轮询 kernel，绕过了 `NotificationManager`
**合规：** ⚠️
**漂移细节：** `NotificationManager` 是死代码。通知从 main.py 直接推送，缺少 icon/severity 字段
**风险：** 中 — 前端收到的通知缺少 icon/severity 元数据

---

### [6] openra_api/game_api.py — GameAPI 协议

#### 6a. TCP 持久连接

**design.md 要求：** 底层 Socket RPC
**代码实现：** `game_api.py:77–124` — 单 socket 持久连接，`RLock` 保护并发，`_ensure_connection_locked()` 按需重建
**合规：** ✅
**漂移细节：** 无 keep-alive/heartbeat，半开连接只在下次请求时发现
**风险：** 低

#### 6b. 请求帧格式

**design.md 要求：** apiVersion / requestId / command / params / language
**代码实现：** `game_api.py:197–204` — 所有 5 个字段齐全，换行分隔 JSON
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 6c. 响应解析

**design.md 要求：** 响应解析
**代码实现：** `_receive_payload()` (138–166) 读取换行分隔帧 + fallback 完整 JSON 检测。requestId 交叉校验(228)。status<0 为错误(233)
**合规：** ✅
**漂移细节：** recv timeout 时的部分 payload 可能误判为完整 JSON（极端 edge case）
**风险：** 低

#### 6d. 断连重连

**design.md 要求：** 断连重连机制
**代码实现：** `_send_request()` (206–261) — 3 次重试循环，捕获 socket.timeout/ConnectionError/OSError → 关闭旧 socket → 下轮 `_ensure_connection_locked()` 重连。RETRY_DELAY=0.5s
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 6e. 命令超时

**design.md 要求：** 命令超时
**代码实现：** `SOCKET_TIMEOUT=10.0` 应用于每次 recv 调用。是 per-recv 超时，非 per-request 总时间
**合规：** ⚠️
**漂移细节：** 慢速 drip-feed 可使总时间无限延长（理论上）
**风险：** 低 — 本地进程通信不会出现

---

### [7] ws_server/server.py — WebSocket 后端

#### 7a. 出站消息类型

**design.md 要求：** 6 种出站: world_snapshot(1Hz), task_update(变更时), task_list(1Hz), log_entry(实时), player_notification(事件触发), query_response(查询回复)
**代码实现：** 全部 6 种均有 send 方法(223–250)。额外 benchmark(225) + session_cleared(229)
**合规：** ⚠️
**漂移细节：** **频率未强制 1Hz**。`publish_dashboard()` 在每个 10Hz tick 被调，world_snapshot/task_list 可能达到 10Hz（有 concurrent-publish guard 做轻度限流但非精确 1Hz）
**风险：** 中 — 前端消息量可能是设计的 10 倍

#### 7b. 入站消息类型

**design.md 要求：** 3 种入站: command_submit, command_cancel, mode_switch
**代码实现：** 全部 3 种已处理(161–169)。额外 question_reply(173)/game_restart(181)/sync_request(183)/session_clear(185)
**合规：** ✅
**漂移细节：** 超集，无遗漏
**风险：** 无

#### 7c. 客户端生命周期

**design.md 要求：** 客户端管理
**代码实现：** connect: `_client_counter` + `_clients` dict (126–129)。disconnect: finally 块 pop (153)。broadcast: 自动清理断连客户端(203–209)
**合规：** ✅
**漂移细节：** 无
**风险：** 无

---

### [8] queue_manager.py — 队列管理

#### 8a. 就绪建筑超时检测

**design.md 要求：** 就绪建筑超时检测
**代码实现：** `tick()` (77–109) 查询 production_queues，跟踪 `_ReadyState(first_seen_at)`，超过 `ready_timeout_s` (默认5s) 触发行动
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 8b. auto_place / warn / off 模式

**design.md 要求：** 三种模式
**代码实现：** `QueueManagerMode = Literal["off", "warn", "auto_place"]`。off(78) 清理并返回，warn(112) 单次通知，auto_place(128–181) 调 game_api.place_building
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 8c. 事件发射

**design.md 要求：** queue_ready_stuck / queue_auto_placed
**代码实现：** queue_ready_stuck(113)，queue_auto_placed(162)，额外 queue_auto_place_failed(134)
**合规：** ✅
**漂移细节：** 无
**风险：** 无

---

### [9] llm/provider.py — LLM 抽象

#### 9a. Provider 切换机制

**design.md 要求：** Provider 切换
**代码实现：** `LLMProvider(ABC)` (65–98) 定义接口。`QwenProvider`(101), `AnthropicProvider`(195), `MockProvider`(291) 三种实现。通过构造注入切换
**合规：** ✅
**漂移细节：** 无动态运行时切换/failover，仅启动时选择
**风险：** 低

#### 9b. Timeout 处理

**design.md 要求：** timeout 处理
**代码实现：** `QwenProvider.chat()` (127–168) 和 `AnthropicProvider.chat()` (220–288) **均无 timeout 参数**。无 `asyncio.wait_for`。无 httpx timeout 配置
**合规：** ❌ 未实现
**漂移细节：** LLM API 调用无任何超时保护。卡死的 API 调用会阻塞整个事件循环无限期
**风险：** **高** — 生产环境可靠性关键缺陷

#### 9c. Retry 逻辑

**design.md 要求：** retry 逻辑
**代码实现：** `chat()` 方法均为单次 await，**无重试循环、无指数退避、无瞬态错误捕获**（SDK 可能有内置 retry，但不可控）
**合规：** ❌ 未实现
**漂移细节：** 429/500/503 等瞬态错误直接传播为未处理异常
**风险：** **高** — 与 9b 组合，任何 API 不稳定直接级联为游戏 Agent 故障

#### 9d. Token 计数

**design.md 要求：** token 计数
**代码实现：** `LLMResponse.usage: dict[str, int]` (31) 携带 prompt_tokens + completion_tokens。两个 Provider 均从 API 响应填充
**合规：** ✅
**漂移细节：** 仅 post-call 报告，无 pre-call 估算
**风险：** 低

---

### [10] 前端 (web-console-v2/src/)

#### 10.1 App 布局 & 双模式

**design.md 要求：** §7: 双模式（用户/调试），三区（Operations/Tasks/Diagnostics），主界面=对话
**代码实现：** `App.vue` — 左侧 TaskPanel, 中间 ChatView (flex:1), 右侧 OpsPanel(user) / DiagPanel(debug)。mode ref 切换 user/debug
**合规：** ✅
**漂移细节：** 用户模式下 OpsPanel 默认隐藏需手动展开
**风险：** 低

#### 10.2 WS 入站: command_cancel 缺失

**design.md 要求：** §7: command_submit, command_cancel, mode_switch
**代码实现：** command_submit (ChatView:83) ✅, mode_switch (App:66 + OpsPanel:55) ✅, **command_cancel 无 UI 实现** ❌
**合规：** ⚠️
**漂移细节：** 后端支持 command_cancel 但前端无取消按钮/交互
**风险：** 中 — 用户无法从 UI 取消任务

#### 10.3 WS 出站消息分发

**design.md 要求：** 6 种出站消息类型
**代码实现：** 所有 6 种均已处理分发到对应组件
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 10.4 useWebSocket 重连

**design.md 要求：** 可靠 WS 连接
**代码实现：** 3s 重连延迟，reconnecting 状态，重连后 sync_request
**合规：** ✅
**漂移细节：** WS URL 硬编码 `ws://localhost:8765/ws`，无环境变量支持
**风险：** 低（开发环境）/ 中（部署环境）

#### 10.5 ChatView: pending_question 文本模式

**design.md 要求：** §6 两种输出模式: 文本模式 "[进攻] 继续还是放弃？" 显示在 Chat；看板模式 Task 卡片+按钮
**代码实现：** ChatView **无 pending_question 处理**。仅 TaskPanel 有按钮（看板模式）。文本模式完全缺失
**合规：** ❌ 未实现
**漂移细节：** 设计明确要求 pending_question 在聊天流中以文本形式出现。当前实现中用户若不看 TaskPanel 侧栏将错过待回答问题
**风险：** **中-高** — 主交互面缺少关键信息展示

#### 10.6 TaskPanel: Task 卡片 + pending_question 按钮

**design.md 要求：** Task 卡片 + Job 子卡 + pending_question 按钮
**代码实现：** 完整实现。状态色 border, Job 子行, pending_question 按钮发送 question_reply
**合规：** ✅
**漂移细节：** pending_question 独立显示区而非嵌入对应 Task 卡片内
**风险：** 低

#### 10.7 DiagPanel: 日志过滤 + Benchmark

**design.md 要求：** 日志过滤 + benchmark 展示
**代码实现：** Level filter (ALL/INFO/WARN/ERROR) + Component filter。Benchmark 聚合统计 top 20
**合规：** ⚠️
**漂移细节：** 日志时间戳用 `toLocaleTimeString()` 绝对时间，非设计要求的 "Xs ago" 格式
**风险：** 低 — 调试面板用绝对时间可能更实用

#### 10.8 OpsPanel

**design.md 要求：** 操作面板
**代码实现：** game start/stop/restart 按钮 + mode switch + VNC iframe + WS 状态指示
**合规：** ✅
**漂移细节：** VNC URL 仅通过 query param 配置
**风险：** 低

#### 10.9 语音/ASR+TTS

**design.md 要求：** §7 + 决策26: 基础框架支持 ASR+TTS
**代码实现：** **完全缺失** — 无任何语音相关代码、stub 或架构钩子
**合规：** ❌ 未实现
**漂移细节：** 无麦克风输入、语音合成、音频 API 调用
**风险：** 中 — 设计要求 "基础框架"，当前连 placeholder 都没有

---

## 维度级审计 (Items 11–15)

### [11] 错误恢复

#### 11a. LLM API 超时/失败

**design.md 要求：** Task Agent 用 default_if_timeout 继续；重试 1 次；连续失败 → 通知玩家
**代码实现：** `task_agent/agent.py` — `AgentConfig`: llm_timeout=30s, max_retries=1, max_consecutive_failures=3。`_call_llm` (646–681) 用 `asyncio.wait_for` + 重试循环。失败时 `_apply_defaults`(283–302)，连续≥2次通知玩家(724–750)，≥3次自动终止(752–770)
**合规：** ✅
**漂移细节：** **注意：这是 task_agent 层的保护，而 llm/provider.py 层完全无 timeout/retry (见 9b/9c)**。两层之间 task_agent 的 `asyncio.wait_for` 弥补了 provider 层的缺失
**风险：** 低 — task_agent 层有完整保护

#### 11b. GameAPI 断连

**design.md 要求：** Job pause, GameLoop 重连；恢复后 Job resume
**代码实现：** `game_loop/loop.py:280–306` — WorldModel stale 触发 `_pause_jobs_for_recovery()`，stale 清除触发 `_resume_jobs_after_recovery()`。GameAPI 自身有 3 次重试重连机制
**合规：** ⚠️
**漂移细节：** 设计说 "GameLoop 重连"，实际 GameLoop 不主动重连，而是等 WorldModel 下次 refresh 时 GameAPI 自动重连成功后 stale 清除。如果 GameAPI 重连持续失败，Jobs 保持暂停但无超时升级
**风险：** 中 — 持续断连无告警升级路径

#### 11c. WorldModel 刷新失败

**design.md 要求：** 用上次快照 + 标记 stale + 告警；连续失败 → 通知玩家
**代码实现：** refresh 异常保留旧 state (212–261)，`stale=True`，跟踪 `_consecutive_refresh_failures`(252)。GameLoop 检测 `consecutive_failures >= threshold(3)` 时推 player_notification (293–306)
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 11d. Job 未处理异常

**design.md 要求：** 捕获 → Signal(task_complete, result=failed) → Task Agent 决定
**代码实现：** `game_loop/loop.py:252–263` — try/except 包裹 `job.do_tick()`，异常时 `job.status = FAILED` + `emit_signal(TASK_COMPLETE, result="failed")`
**合规：** ✅
**漂移细节：** 无
**风险：** 无

---

### [12] Constraint 系统

#### 12a. create_constraint / remove_constraint

**design.md 要求：** 完整 CRUD 流程
**代码实现：** `kernel/core.py:1231–1242` create → 存储 + world_model.set_constraint + 同步。`1244–1250` remove → pop + world_model.remove_constraint + 同步
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 12b. enforcement=clamp vs escalate 传播

**design.md 要求：** clamp（Job 自动遵守）vs escalate（升级给 Brain）
**代码实现：** `ConstraintEnforcement` 枚举有 CLAMP 和 ESCALATE (models/enums.py:34–36)。**仅 CombatJob._effective_chase_distance (combat.py:337–345) 处理 clamp enforcement**。**ESCALATE 在整个代码库中无任何运行时行为** — 存在枚举但零分支逻辑
**合规：** ❌ 未实现
**漂移细节：** escalate enforcement 是死代码。仅 CombatJob 的 chase distance 有 clamp 实现。其他 constraint kind (economy_first, defend_base) 在所有 Expert 中无 enforcement 实现
**风险：** **高** — escalate 约束被静默接受但完全不生效。除 CombatJob chase 外所有 constraint 均无效果

#### 12c. Job 每 tick 读取匹配 scope 的 Constraint

**design.md 要求：** Job 每 tick 读取匹配 scope 的活跃 Constraint
**代码实现：** `BaseJob.get_active_constraints()` (base.py:255–263) 查询 global + expert_type + task_id scope。**仅 CombatJob 在 tick 中调用此方法**。ReconJob, EconomyJob, MovementJob, DeployJob 均有 constraint_provider 注入但从不调用
**合规：** ⚠️
**漂移细节：** 4/5 种 Expert 完全忽略 constraint
**风险：** 中 — 针对非 Combat Expert 的 constraint 静默无效

#### 12d. global scope → 所有当前+未来 Job

**design.md 要求：** global scope 影响所有当前和未来 Job
**代码实现：** `_constraints_for_scope("global")` 在 tick 时实时查询，新 Job 自动继承（回调架构）
**合规：** ✅
**漂移细节：** 受 12c 限制 — 只有 CombatJob 会实际读取
**风险：** 低（继承自 12c）

---

### [13] ResourceNeed 声明式资源模型

#### 13a. predicates 匹配逻辑

**design.md 要求：** predicates 匹配（category/mobility）
**代码实现：** `_actor_matches_need()` (kernel/core.py:1002–1033) 评估 owner/category/mobility/can_attack/can_harvest/name/actor_id。静态 actor 守卫防止建筑被意外匹配
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 13b. 持续满足: 死了补、新造自动分配

**design.md 要求：** 死了补，新造自动分配
**代码实现：** UNIT_DIED → `_rebalance_resources()` (1069–1074)。PRODUCTION_COMPLETE → `_rebalance_resources()` (359–361)
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 13c. 不足降级运行

**design.md 要求：** 资源不足 → 降级运行
**代码实现：** `_rebalance_resources()` (885–889) 零资源时 status=WAITING。部分资源时保持 RUNNING（隐式降级）。`RESOURCE_LOST` signal 仅发一次
**合规：** ⚠️
**漂移细节：** 无显式 "降级模式" 信号或行为适配。部分资源时 Job 继续运行但 Task Agent 缺乏持续降级感知
**风险：** 低-中

#### 13d. "只有 Task Agent 有权取消"

**design.md 要求：** 只有 Task Agent 有权判断"没希望了"取消
**代码实现：** Kernel 资源丢失时发 RESOURCE_LOST signal + options(wait/use_alt/abort)，default="wait_for_production"。Kernel 不自动 abort（仅抢占时 abort）
**合规：** ✅
**漂移细节：** 无
**风险：** 无

---

### [14] Adjutant 对话管理

#### 14a. Adjutant 为唯一对话窗口

**design.md 要求：** §6: Adjutant 是玩家和系统之间的唯一对话窗口
**代码实现：** `main.py:243–273` 有 `if self.adjutant is None` fallback 直接创建 task
**合规：** ⚠️
**漂移细节：** Adjutant 是可选的。无 Adjutant 时绕过分类/query/reply 路由
**风险：** 低（开发期便利）

#### 14b. 输入分类: 新命令 / 回复 / 查询

**design.md 要求：** LLM 判断三种类型
**代码实现：** `handle_player_input()` (160–218) 优先级管道: deploy反馈 → NLU → 规则匹配 → LLM 分类。`_classify_input()` (703–733) LLM JSON 分类
**合规：** ⚠️
**漂移细节：** **规则兜底 `_rule_based_classify()` (768–773) 无法产出 "reply" 类型**。LLM 故障 + pending_question 场景下，玩家回复会被误分类为新命令
**风险：** 中 — 降级模式下 reply 路由失败

#### 14c. pending_question 注册/呈现/超时/default

**design.md 要求：** 完整生命周期
**代码实现：** 见 5b — 完整实现
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 14d. 多问题优先级排序

**design.md 要求：** 高优先级先展示；模糊回复只匹配最高优先级
**代码实现：** `list_pending_questions()` (kernel/core.py:466) 按 (priority, timestamp) 降序。`_handle_reply()` (784–788) 无匹配时取 `pending[0]`
**合规：** ⚠️
**漂移细节：** 设计的"拆分回复路由给多个 Task"未实现。仅路由给最高优先级。这是设计的 fallback 行为，但 "ideal path" 不存在
**风险：** 中 — 多问题场景下非最高优先级的问题必然走 default_option

#### 14e. 迟到回复处理

**design.md 要求：** "已按默认处理，如需更改请重新下令"
**代码实现：** `kernel/core.py:542–550` 返回准确字符串，通过 adjutant 传达给玩家
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 14f. "继续" 路由

**design.md 要求：** "继续" 被识别为回复 → 路由给对应 Task
**代码实现：** 依赖 LLM 分类判断。LLM 上下文含 pending_questions。正常工作时路由正确
**合规：** ⚠️
**漂移细节：** 同 14b — 无 LLM 时 "继续" 被误创建为新任务
**风险：** 中

#### 14g. Query 独立 LLM 调用

**design.md 要求：** 独立 LLM 调用 + WorldModel 上下文
**代码实现：** `_handle_query()` (832–865) — 正确
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 14h. 两种输出模式

**design.md 要求：** 文本模式 / 看板模式
**代码实现：** `format_task_message()` (907–942) 支持 text/card 模式。**但 main.py 发布路径未使用此方法** — 直接格式化
**合规：** ⚠️
**漂移细节：** format_task_message 是死代码
**风险：** 低

---

### [15] 主动通知链

#### 15a. WorldModel 事件 → Kernel 规则 → 推通知

**design.md 要求：** §1: ENEMY_EXPANSION/FRONTLINE_WEAK/ECONOMY_SURPLUS → 推通知不路由
**代码实现：** `kernel/core.py:356–358` — 三种事件类型 → `_push_player_notification()` + `return`。内容匹配设计原文
**合规：** ✅
**漂移细节：** 无
**风险：** 无

#### 15b. 前端通知展示

**design.md 要求：** 通知到达看板展示
**代码实现：** kernel → `list_player_notifications()` → `main.py._publish_notifications()` offset 轮询 → WS broadcast `player_notification` → ChatView(109–112) + DiagPanel(250–263)
**合规：** ⚠️
**漂移细节：** 直推路径缺少 icon/severity 字段（NotificationManager 有但未使用）
**风险：** 中 — 前端通知缺少视觉分类元数据

---

## 风险汇总（按优先级排序）

### ❌ 高风险 (P0)

| # | 模块 | 问题 | 影响 |
|---|---|---|---|
| 9b | llm/provider.py | LLM API 调用无 timeout | 卡死的 API 会阻塞事件循环无限期。task_agent 层有 wait_for 弥补，但 adjutant 的 query_timeout 依赖 LLM 调用返回 |
| 9c | llm/provider.py | LLM API 调用无 retry | 瞬态错误直接传播。task_agent 有自己的 retry，但 adjutant 无 |
| 12b | constraint | escalate enforcement 是死代码 | escalate 约束被接受但完全不生效 |

### ⚠️ 中风险 (P1)

| # | 模块 | 问题 | 影响 |
|---|---|---|---|
| 10.5 | 前端 ChatView | pending_question 文本模式缺失 | 主交互面缺少关键信息 |
| 12c | constraint | 仅 CombatJob 读取 constraint | 4/5 Expert 忽略约束 |
| 14b | adjutant | 规则兜底无 reply 分类 | LLM 故障+pending question 时 reply 误路由 |
| 4c | world_model | find_actors 缺 mobility 过滤 | 按 mobility 选单位需手动 post-filter |
| 7a | ws_server | 消息频率未限 1Hz | 前端可能收到 10 倍设计量的消息 |
| 5d | adjutant | NotificationManager 是死代码 | 通知缺 icon/severity 元数据 |
| 11b | 错误恢复 | GameAPI 持续断连无告警升级 | Jobs 无限暂停无通知 |
| 10.2 | 前端 | command_cancel 无 UI 入口 | 用户无法从 UI 取消任务 |

### ⚠️ 低风险 (P2)

| # | 模块 | 问题 | 影响 |
|---|---|---|---|
| 1c | main.py | 构造顺序偏差 | 仅影响可读性 |
| 3a | kernel | cancel vs cancel_task 命名 | 仅命名差异 |
| 3c | kernel | actor 事件额外路由给 Task Agent | 扩展行为 |
| 4a | world_model | actor 刷新 100ms vs "每 tick" | 10Hz 下等效 |
| 6e | game_api | per-recv 超时非 per-request | 实际场景无影响 |
| 10.7 | DiagPanel | 时间戳格式不一致 | 调试面板用绝对时间可能更好 |
| 10.9 | 前端 | ASR/TTS 基础框架缺失 | 设计要求但非核心功能 |
| 13c | resource | 无显式降级模式信号 | 隐式降级工作但缺持续感知 |
| 14d | adjutant | 多问题拆分回复未实现 | 走 design fallback 行为 |
| 14h | adjutant | format_task_message 死代码 | 功能等效但抽象未启用 |
