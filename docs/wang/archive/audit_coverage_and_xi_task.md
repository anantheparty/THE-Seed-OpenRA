# 审计覆盖地图 + Xi 补全任务

## 活跃代码范围（main.py 实际引用的模块）

| 模块 | 行数 | 审计深度 | 说明 |
|------|------|----------|------|
| main.py | 908 | ⚠️ 浅 | RuntimeBridge 组装、WS 事件路由、启动/关闭链 |
| kernel/core.py | 1,283 | ⚠️ 中 | start_job 顺序审过，但 cancel/preemption/event routing/pending_question 未审 |
| world_model/core.py | 1,105 | ⚠️ 中 | world_summary 和 GAME_RESET 审过，但分层刷新/事件检测完整性/query types 未审 |
| task_agent/ (6 files) | 1,600 | ✅ 深 | SYSTEM_PROMPT、_build_messages、tools、handlers、context、bootstrap |
| adjutant/ (4 files) | 1,550 | ⚠️ 浅 | rule_match 审过，但 query 处理/pending_question 流程/NLU runtime/notifications 未审 |
| experts/ (10 files) | 2,550 | ✅ 深 | base/economy/recon/combat/deploy/knowledge/planners 全部审过 |
| game_loop/loop.py | 323 | ❌ 未审 | tick 逻辑、事件转发、Job 调度 |
| ws_server/server.py | 263 | ❌ 未审 | WS 协议、消息类型、客户端管理 |
| openra_api/game_api.py | 1,435 | ❌ 未审 | TCP 协议、命令格式、响应解析 |
| queue_manager.py | 204 | ❌ 未审 | 队列清理、就绪建筑超时、模式 |
| llm/provider.py | 324 | ❌ 未审 | LLM 抽象层、provider 切换、timeout 处理 |
| models/ (4 files) | 400 | ✅ 深 | Task/Job/ExpertSignal/Constraint 数据模型 |
| unit_registry.py | 300 | ⚠️ 浅 | 接口审过，但解析逻辑/错误处理未审 |
| game_control.py | 177 | ❌ 未审 | 游戏进程生命周期控制 |
| logging_system/ | 530 | ✅ 深 | slog/persistence/benchmark bridge |
| benchmark/ | 331 | ✅ 深 | BenchmarkStore/Timer |
| **web-console-v2/src/** | **1,200** | **❌ 未审** | Vue 组件 + WS composable + 状态管理 |

**统计：** 活跃代码 ~13,300 行 Python + ~1,200 行 Vue/JS。已深审 ~5,400 行 (37%)，中度审 ~3,000 行 (20%)，未审 ~6,100 行 (42%)。

---

## design.md 各节审计状态

| 节 | 内容 | 审计状态 | 具体遗漏 |
|---|------|----------|----------|
| §0 | 三级架构 | ✅ | — |
| §1 | 流程（三条路径 + 主动通知） | ⚠️ | 主动通知链未验证（WorldModel event → Kernel rule → player_notification） |
| §2 | 运行时（GameLoop 10Hz + 分层刷新） | ❌ | tick intervals 未验证、分层刷新频率未验证 |
| §3 | 数据模型 | ⚠️ | ResourceNeed.predicates 匹配逻辑未审、Constraint enforcement 传播未审 |
| §4 Kernel | 职责 | ⚠️ | cancel/cancel_tasks 未审、冲突仲裁/抢占未审、event routing 规则逐条未验证、被动事件自动响应(defend_base)只看了触发不看全链 |
| §4 TaskAgent | 职责 | ✅ | — |
| §4 Expert | 三种类型 | ✅ | — |
| §4 WorldModel | 职责 | ⚠️ | 派生分析（威胁评估/区域控制）未验证、资源匹配 predicates 未审 |
| §4 GameAPI | 底层 | ❌ | TCP 协议实现未审 |
| §5 | 大脑-小脑协作 | ⚠️ | context injection 时机（full/delta/compression）未验证、default_if_timeout 实现未审 |
| §6 Adjutant | 副官 | ⚠️ | query 处理未审、pending_question timeout（Kernel 持有定时器）未验证、多问题优先级排序未审、迟到回复处理未审 |
| §7 | 看板 + 日志 | ⚠️ | log 系统审完，但前端组件对 design.md 要求（两种模式、五种出站消息）的合规性未审 |
| 错误恢复 | 四种故障策略 | ❌ | LLM default_if_timeout、GameAPI 断连 pause/resume、WorldModel stale 处理、Job 异常捕获 — 均未在代码中验证 |
| §9 | 场景推演 | ❌ | 作为参考用过，但未逐步代码验证 |

---

## 遗漏的模块/维度（Xi 应审计的）

### 模块级

1. **main.py (908 行)** — Runtime 组装层
   - RuntimeBridge 怎么把 Kernel/Adjutant/WS/GameLoop 组装
   - WS 入站消息路由 (command_submit/command_cancel/mode_switch)
   - 启动顺序是否符合 design.md §2
   - 关闭序列是否正确释放资源

2. **game_loop/loop.py (323 行)** — 主循环
   - tick 是否按 design.md §2 顺序执行（refresh → events → route → job tick → push）
   - Job tick_interval 是否正确（Combat 0.2s, Recon 1.0s, Economy 5.0s）
   - GameLoop 与 asyncio 的协作方式

3. **kernel/core.py — 未审部分**
   - `cancel_tasks(filters)` 实现
   - 冲突仲裁：多 Task 争同一资源时的优先级逻辑
   - 抢占：高优先级夺低优先级资源的完整流程
   - 事件路由规则逐条验证：
     - UNIT_DIED → actor 匹配 Job.resources？
     - ENEMY_DISCOVERED → 广播所有 Task Agent？
     - BASE_UNDER_ATTACK → 触发 defend_base + 广播？
     - ECONOMY_SURPLUS/FRONTLINE_WEAK → 只推通知不路由？
   - `pending_question` 超时：Kernel 持有定时器？超时发 default_option？
   - `defend_base` 自动响应的完整链

4. **world_model/core.py — 未审部分**
   - 分层刷新间隔：actor 每 tick、economy 500ms、map 1s？
   - `detect_events()` 覆盖了哪些事件类型？
   - ResourceNeed predicates 匹配 (`find_actors(predicates={category: vehicle, mobility: fast})`)
   - `runtime_state()` 返回的内容是否完整

5. **adjutant/ — 未审部分**
   - `adjutant.py`: query 路径（LLM + WorldModel → 直接回答，不进 Kernel）
   - `adjutant.py`: pending_question 流程（注册/超时/迟到回复）
   - `runtime_nlu.py` (429 行): NLU 运行时处理完整逻辑
   - `notifications.py` (151 行): 通知管理

6. **openra_api/game_api.py (1,435 行)** — GameAPI 协议
   - TCP 持久连接管理
   - 请求帧格式 (apiVersion/requestId/command/params/language)
   - 响应解析
   - 断连重连机制
   - 是否有命令超时

7. **ws_server/server.py (263 行)** — WebSocket 后端
   - 五种出站消息类型 (world_snapshot/task_update/task_list/log_entry/player_notification/query_response)
   - 三种入站消息类型 (command_submit/command_cancel/mode_switch)
   - 客户端生命周期管理

8. **queue_manager.py (204 行)** — 队列管理
   - 就绪建筑超时检测
   - auto_place / warn / off 模式
   - 事件发射 (queue_ready_stuck/queue_auto_placed)

9. **llm/provider.py (324 行)** — LLM 抽象
   - provider 切换机制
   - timeout 处理
   - retry 逻辑
   - token 计数

10. **前端 (web-console-v2/src/, 1,200 行)**
    - ChatView.vue: 消息渲染、命令提交、pending_question 展示
    - TaskPanel.vue: Task 卡片、Job 子卡片、pending_question 按钮
    - DiagPanel.vue: 日志过滤、benchmark 展示
    - OpsPanel.vue: 操作面板功能
    - App.vue: 布局、WS 连接状态
    - useWebSocket.js: 重连机制、消息分发

### 维度级

11. **错误恢复 (design.md 错误恢复表)**
    - LLM API 超时/失败 → default_if_timeout 实现？retry 几次？连续失败 → 通知玩家？
    - GameAPI 断连 → Job pause？GameLoop 重连？恢复后 Job resume？
    - WorldModel 刷新失败 → 用上次快照？标记 stale？连续失败告警？
    - Job 未处理异常 → 捕获 → Signal(failed)？TaskAgent 收到后处理？

12. **Constraint 系统**
    - create_constraint/remove_constraint 完整流程
    - enforcement=clamp vs escalate 的传播机制
    - Job 读取匹配 scope 的 Constraint？
    - global scope 影响所有当前和未来 Job？

13. **ResourceNeed 声明式资源模型**
    - predicates 匹配逻辑（category/mobility）
    - Kernel 持续满足：死了补、新造自动分配
    - 不足降级运行
    - "只有 Task Agent 有权判断没希望了取消"

14. **Adjutant 对话管理 (design.md §6 完整流程)**
    - pending_question 注册/呈现/超时/default
    - 多问题优先级排序
    - 迟到回复处理
    - "继续" 路由回 TaskA vs 新命令

15. **主动通知链 (design.md §1)**
    - WorldModel 事件 → Kernel rule → player_notification
    - ENEMY_EXPANSION/FRONTLINE_WEAK/ECONOMY_SURPLUS → 推送通知不路由
    - 前端通知展示

---

## Xi 审计任务说明

**目标：** 逐一审计上述 15 个遗漏项（10 个模块 + 5 个维度），对照 design.md 验证代码实现是否一致。

**输出格式：** 每个审计项输出：
```
### [编号] 模块/维度名
**design.md 要求：** <引用 design.md 原文>
**代码实现：** <代码位置 + 关键逻辑>
**合规：** ✅ 一致 / ⚠️ 部分漂移 / ❌ 未实现
**漂移细节：** <如有，描述具体差异>
**风险：** <对系统的影响>
```

**优先级：** 先审 Kernel 未审部分 (3) + 错误恢复 (11) + Adjutant 对话管理 (14)，这三块直接影响 P0 任务的设计决策。

**注意：**
- 只读审计，不改代码
- 审计输出写到 `docs/xi/full_audit_report.md`
- 发现的漂移/问题同步到 `docs/wang/optimization_tasks.md` 的对应任务中
