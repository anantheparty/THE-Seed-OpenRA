# Wang — Progress Log

## [2026-03-29 03:15] DONE — Session bootstrap and MCP issue diagnosis
- Set up agent directory (docs/wang/)
- Discovered MCP server agent-chat is not connected (mcpPresent: false)
- Root cause: agent-up script doesn't properly attach MCP stdio server to Claude process
- Established curl REST API fallback for communication
- Saved workaround to memory and agents.md
- Notified ac-topleader (msg_86232) about systemic MCP missing issue for remote agents
- ac-topleader confirmed bug (msg_86233): agent-up writes .mcp.json but claude CLI launch doesn't pass --mcp-config flag. Fix tracked.
- ac-topleader (msg_86237): MCP fix landed on master (c21f6d6). New agents will get MCP attached automatically. Current session still uses curl fallback.

## [2026-03-29 03:25] DONE — MCP reconnected after restart
- MCP server agent-chat now connected and all 5 tools working natively
- Root cause correction: MCP was manually disabled by user in MCP dialog, NOT a systemic agent-up bug
- Sent correction to ac-topleader (msg_86246) — false alarm apology

## [2026-03-29 05:50] DONE — Phase 0 系统调查与架构分析
- 完成代码库全面探索（Agent 发起 Explore）
- 产出 `docs/wang/architecture_analysis.md`：现状摘要、缺失清单、5个决策点、分层架构提案、代码映射表
- 分配 yu 3个深度调查任务（Job系统、IntelService、Agent接口）
- yu 完成报告 `docs/wang/yu_investigation_report.md`，核心发现：
  - 两套 Intel 栈并行且不统一
  - CombatAgent.company_states 是 ExecutorInstance 最佳参考
  - EconomyEngine.decide() 天然是 Planner 模式
  - Job 系统是执行基座，不是 Kernel
- 分配 yu 跟进任务：专家契约 ABC 草稿 + Intel 合并分析
- 更新架构分析文档整合 yu 发现

## [2026-03-29 06:00] DONE — yu 交付专家契约 + Intel 合并分析
- expert_contracts_draft.py: 统一 bind→start→tick→status→release 生命周期
- intel_merge_analysis.md: 两栈对比、facade 方案、4 阶段迁移
- wang/yu 对齐 4 个设计问题（proposal-first, abort vs release, ConcurrencyPolicy, str resource ID）

## [2026-03-29 06:10] DONE — 用户方向修正，重大调整
用户拍板：
- Kernel 无循环，被动仲裁器，Task 自己拥有循环
- 全部可重写，不需要增量兼容
- WorldModel/IntelService 都是观察型专家系统，需要改造
- 需要加入大量传统 RTS AI 技术（BT/FSM/GOAP/影响力图等）— roadmap 此处太草率
- 需要一个好的看板/dashboard
- 核心痛点深化："包围"例子——LLM 只会坐标偏移，不会真正的地形分析/兵力分配/时间协调
- 已更正 architecture_analysis.md 中的 3 个决策（Kernel/WorldModel/重写）
- 已分配 yu 进行传统 RTS AI 深度互联网调研 (msg_86301)

## [2026-03-29 07:40] DONE — 传统 RTS AI 调研整合 + 架构 v2
- yu 完成 rts_ai_research.md：覆盖影响力图、BT/FSM/GOAP/HTN、编队AI、势场、多层架构、LLM混合
- 核心发现：成功 RTS AI 都是 Strategy→Tactics→Micro 三层混合架构，没有单一形式化方法获胜
- 发现 roadmap 结构性盲区：专家系统缺乏明确的层级关系
- 产出 architecture_v2.md：
  - 三层执行架构（Strategy→Tactics→Micro）
  - Tactical Method 概念（有阶段状态的战术执行单元，解决"包围"问题）
  - 4层影响力图系统（terrain/vision/threat/support）
  - 8个战术方法模板
  - 完整系统全景图和开发优先级

## [2026-03-29 07:55] DONE — 补全遗漏需求 + 完善 plan
- 用户指出 plan 维护不够、遗漏看板设计等多个需求
- 重写 plan.md：5 大块 18 项任务（代码整理/看板设计/架构细化/需求记录/技术选型）
- 产出 user_requirements.md：6 大类需求 + 7 项已更正的过时假设 + 6 个开放问题
- 分配 yu 两个新任务：web-console 审计 + 全代码资产盘点 (msg_86307)
- 更正过时假设：增量兼容→全面重写，tick-driven→被动仲裁，IntelService保留→可重写

## [2026-03-29 08:00] DONE — 看板审计 + 代码资产盘点
- yu 完成 dashboard_audit.md: 现有控制台是厨房水槽，需要三区分离(Ops/Tasks/Diag)
  - 发现硬 bug: 前端 8090 vs 后端 8092 端口不一致
  - 看板需要后端先支持 task_snapshot/task_event 一等公民对象
- yu 完成 code_asset_inventory.md: 全量文件盘点
  - Keep: GameAPI、models、NLU 管线
  - Reference: jobs、agents、intel 双栈、tactical_core
  - Delete: 6+ standalone launchers/demos
  - 最高优先级重写: main.py (1327 行单体)
- 用户补充确认: 看板双模式(用户+调试) + 结构化日志系统

## [2026-03-29 08:15] DONE — 文档整合收敛
用户反馈：文档太多太散，要收敛成真正有用的文档。
- 7 份调查/调研文档归档至 archive/
- 所有架构决策、对象模型、接口约定收敛进唯一主文档 design.md
- 活跃文档从 12 → 6 个
- 新增用户决策：对手AI不纳入（另一个副官实例）、Task类型沿用roadmap、GameAPI不改
- Constraint 定位为"活跃修饰器"（不绑资源、无阶段、影响其他Task的执行策略）

## [2026-03-29 08:40] DONE — design.md 大幅重写，场景驱动
用户反馈：design.md "伪人感重"，堆砌关键词没有思考实际执行流。
核心问题：
  - Directive.target 不能是已解析实体（Interpreter 不查游戏状态）
  - Interpreter → Kernel 之间缺少 Resolver + Decomposer
  - 三层不是每个 Task 必经管线，是 Expert 内部可选工具
重写内容：
  - §1 改为"命令处理流水线"，逐步标注每步的输入/输出/实现/是否LLM
  - §2 对象模型增加 ResolvedTarget，Directive.target 改为未解析文本
  - §3 Kernel 简化为事件触发列表
  - §4 Expert 系统：明确"不是每个 Expert 都走三层"
  - §6 新增完整场景模拟："探索地图，找到敌人基地"全流程 + 时间线
  - §8 看板技术栈选定 Vue 3
  - 删除独立的"三层执行架构"章节，融入 Expert 系统描述

## [2026-03-30 00:20] DONE — design.md 全面重写 (Round 2)
用户指示：采纳 yu 的所有审查意见，修到能推演为止。
yu 审查发现 7 个 blocker（归档 archive/design_review.md）。
全部处理：
  1. 运行时矛盾→单线程 GameLoop 10Hz + per-expert tick_interval
  2. Action 路径→定义 Action/ActionResult/ActionExecutor（含 execute_batch 逻辑）
  3. 资源请求→ResourceRequest（predicates/mandatory/allow_wait/allow_preempt）
  4. 条件→SuccessCondition/FailureCondition 带 check() 方法
  5. 事件→Event dataclass + 检测算法（快照 diff）
  6. 取消/抢占→cancel_by_directive + 优先级抢占 + Expert.abort() 合约
  7. Schema→TaskSpec.blocked_by, ExecutionJob 扩展, Outcome 扩展, Constraint 扩展
  新增：NormalizedActor, Expert 生命周期, 启动顺序, WorldModel 分层刷新, ActionExecutor 去重
  新增场景边缘情况：侦察兵死亡→补充/失败, 取消命令流程, 资源抢占流程
  交给 yu 做 Round 2 审计 (msg_86417)

## [2026-03-30 00:50] DONE — design.md Round 3 修复
yu R2 审计发现 6 个剩余 gap（原 7 blocker 已关闭）。
全部修复：
  1. Outcome.result/JobStatus 对齐 → 统一用 "succeeded"，新增 PARTIAL
  2. 场景全字段补全 → ResourceRequest/Action/Outcome 所有字段填入具体值
  3. mid-task 资源 → Expert.start() 接收 resource_callback，ResourceRequest.wait_timeout_s
  4. Cancel → 新增 CancelSelector(directive_id/intent_match/job_id)，Kernel.cancel(selector)
  5. Dashboard 入站 → command_submit/cancel/clarification/mode_switch 四个 WS 事件
  6. Action resource_key → 替代 actor_id，支持 "actor:57"/"queue:Infantry"/"global"
交给 yu Round 3 审计 (msg_86419)

## [2026-03-30 01:35] DONE — design.md 审计收敛：Zero Blockers
5 轮迭代审计完成：
  R1: 7 blocker (架构矛盾) → 全关
  R2: 6 blocker (合约缺失) → 全关
  R3: 4 blocker (局部 gap) → 全关
  R4: 2 blocker (接口不一致) → 全关
  R5: 0 blocker ✅
design.md 可作为实现基础。

## [2026-03-30 02:00] DONE — 架构大转向：LLM agent + 大脑小脑模型
用户反馈推动三次架构转变：
  1. 合并 Interpreter/Resolver/Decomposer → CommandProcessor（后被 Adjutant 取代）
  2. 每个 Task = LLM agent 实例（大脑）+ Expert/Job 自主执行（小脑）
  3. 三级架构：Kernel(无LLM) / Task Agent(LLM) / Job(传统AI)
关键设计：
  - Job 直接调 GameAPI，无 ActionExecutor 中间层
  - 声明式资源模型（死了补、降级、不轻易 fail）
  - Expert 是写死的代码模块，Job config 是 per-Expert 强 schema
  - 大脑小脑协作：ExpertSignal + context packet 注入 + default_if_timeout
  - Task Agent 框架：raw SDK 自建 ~200 行
精简数据模型：14+ → 7 个

## [2026-03-30 03:00] DONE — 场景审计 7 轮全部通过 (A-I)
11 个测试场景全部 zero blockers（T1-T11）
拆出独立 test_scenarios.md，每步详细系统状态

## [2026-03-30 03:30] DONE — Adjutant 玩家交互层
设计 + 2 轮审计 zero blockers
TaskMessage/PlayerResponse schema、pending question timeout（Kernel 持有）、多问题路由规则

## [2026-03-30 04:00] DONE — 实现任务列表
implementation_plan.md：35+ 任务，7 Phase，4 里程碑
yu 审计后补充：WS 后端、timestamp 传播、review_interval 调度、Adjutant 路由测试

## [2026-03-30 04:15] DONE — the-seed 子库移除评估
用户决定移除 the-seed 框架子库（隔离增加复杂度，重写后不需要）
保留 OpenCodeAlert 游戏子库
yu 正在审计 the-seed 里是否有需要迁移的内容

## [2026-03-30 11:27] DONE — Phase 0 yu 侧全部完成
yu 完成全部 3 个任务：
- 0.5 benchmark (a325814) — xi 审计 PASS ✅
- 0.1 cleanup (6735e1e) — xi 审计 PASS ✅
- 0.1b the-seed removal (a4e7805) — xi 审计 PASS ✅
  迁移 NLU 规则到 nlu_pipeline/rules/，创建本地兼容包替代子库，.gitmodules 仅留 OpenCodeAlert
注意：benchmark commit 中混入了其他已 staged 文件（共享 index），未 rewrite history

## [2026-03-30 11:34] DONE — Phase 0 关闭 ✅
yu 交叉审计 xi 的 0.2/0.3/0.4：发现 2 blocker + 1 should-fix
- Blocker 1: Job.config 无 expert_type 绑定 → xi 加 EXPERT_CONFIG_REGISTRY + validate_job_config()
- Blocker 2: AnthropicProvider 多轮 tool-use 不完整 → 延后 Phase 1，加注释说明
- Should-fix: configs 用 enum 替换裸 str → xi 修复
xi 修复 commit 1f5a7ce → yu 回归审计 zero blockers (commit 461a83a)
wang 概念漂移检查通过

**Phase 0 全部 6 个任务完成，交叉审计通过：**
- 0.1 cleanup (yu/xi) ✅
- 0.1b the-seed removal (yu/xi) ✅
- 0.2 data models (xi/yu) ✅
- 0.3 directory structure (xi/yu) ✅
- 0.4 LLM abstraction (xi/yu) ✅
- 0.5 benchmark (yu/xi) ✅

## [2026-03-30 15:27] DONE — Phase 1 Batch 1 启动
分配：yu: 1.1 WorldModel / xi: 1.4 Task Agent，并行无文件冲突。

## [2026-03-31 00:00] DONE — Task 1.4 审计通过
xi 完成 Task Agent (d321a3e)，yu 审计发现 2 blocker（events 丢失 + defaults 空操作）+ 1 should-fix。
xi 修复 (ccbf442)，yu 回归审计 zero blockers。13 tests pass。
yu 的 1.1 WorldModel 仍在开发中。

## [2026-03-31 23:00] DONE — Phase 0-7 全部完成 + Live E2E Rounds 1-4
- Phase 1-7 全部开发完成（详见 live_test_log.md）
- 4 轮 Live E2E 测试，发现并修复 15+ 真实问题
- Round 4 结果：T5 ✅ / T9 ✅ / T1 ✅ / T2 ✅ / T7 ⚠️ / 0 ERROR ✅
- 关键修复：BASE_UNDER_ATTACK 误触发、LLM Expert 参数、actor category、建筑分配、GameAPI 长连接
- 用户反馈："行为正确但交互不正确，没有收到任何提示" → 分配 yu 修复

## [2026-04-01 06:10] DONE — Yu 修复 command feedback + defend_base reflex
- commit 2660436: RuntimeBridge._emit_adjutant_response() — 命令响应走 query_response 通道（聊天可见）
- commit 398902b: Kernel defend_base 即时反射 — BASE_UNDER_ATTACK 直接创建 CombatJob(HOLD)，不等 LLM
- 全量测试通过：14 kernel + 7 world_model + 10 adjutant + 7 ws + 6 game_control + E2E T1-T11
- 准备 Round 5 live E2E 验证

## [2026-04-01 06:30] DONE — Round 5 部分测试 + 系统深度分析
- T5 部署基地车 ✅ — MCV→建造厂成功
- T9 查询战况 ✅ — 副官返回完整中文简报（经济/军事/地图/建议）
- 命令反馈 ✅ — query_response 通道回显正常
- 发现 Issue 8（P0）：LLM 用 "PowerPlant" 但游戏要 "powr" → 所有建造失败
- 发现 Issue 9：production queue 查询返回 COMMAND_EXECUTION_ERROR
- 发现 Issue 10：WS world_snapshot 可能超 1MB 帧限制
- 发现 Issue 11：后端重启后 GameAPI 持久连接失效
- 产出 `docs/wang/system_analysis_v1.md`：完整架构 gap 分析 + 改进 roadmap
- 派 Yu 接手 live 测试 + 修复 Issue 8/9 + 前端修复

## [2026-04-01 07:00] DONE — 系统漂移分析 + 全链路调试方案
- 对照 design.md 逐项验证实现，产出 drift map（11 项完全实现、3 项有意简化、3 项 gap、5 项偏移）
- 关键发现：UnitRegistry 缺失是 Issue 8 根因，不是单纯的名称 bug
- 从 OpenCodeAlert yaml 提取完整单位注册表（建筑/车辆/步兵，含阵营/前置/成本）
- 产出 `docs/wang/system_drift_and_test_guide.md`：
  - Part 1: 设计 vs 实现漂移地图
  - Part 2: 当前阻断点
  - Part 3: 测试矩阵（在测什么、测什么没测）
  - Part 4: 全链路调试方案（4 层观测点、诊断决策树、日志关键词）
  - Part 5: 系统成熟度评估
- Yu 修复 Issue 8/9 + 建筑 queue 堵塞（3 commits），已派 Round 6 全链路测试
- 设 10min CronCreate 提醒监督 Yu 进展

## [2026-04-01 07:45] DONE — Yu Round 6 修复 8 个 live bug
Yu 在 Round 6 live 测试中发现建筑/经济链路有一叠 7 个相互关联的 bug：
1. dff8cb4: 终止 Task 后资源泄漏（queue:Building 不释放）
2. 21b15e5+65dde1c: auto-place 假成功（游戏侧+Python 侧双修）
3. 7af8a42: 简单建造命令 prompt 映射不足，改为 bootstrap 直接映射
4. a4f08fc: bootstrap 建造成功后 TaskAgent 重入 LLM 漂移到侦察
5. 777e8f0: EconomyJob 在建筑放置前就标记完成
6. e7daa12: 已有就绪建筑不计入完成数
7. 8b84985: blocked 状态不通知用户（低电力等）
8. 72d24cd: MCV 部署误触发 BASE_UNDER_ATTACK

验证结果：建造兵营全链路修复（不再漂移/卡队列），低电力正确显示警告。
尚未完成：需要干净游戏开始才能测试完整开局→侦察→战斗链

## [2026-04-01 08:51] DONE — Yu Round 6 最终报告 + Live Hardening 建议
Yu 交付 `docs/wang/live_round6_final_report.md`，核心结论：
- 底层运行时已稳定，主要问题从"会不会坏"转到"workflow 是否合理"
- 开局链全部验证通过：部署 ✅ 兵营 ✅ 矿场 ✅ 步兵 ✅ 侦察 ✅
- 本轮共修 12 个 live bug（建造链 8 个 + 反馈链 2 个 + runtime 2 个）
- 最大未闭环问题："攻击敌人"无目标时 LLM 过度动作（造车/扩产/漂移）
- 识别 5 项设计漂移需要正式记录
- 建议插入 Live Hardening 短周期再继续扩功能

## [2026-04-01 10:02] DONE — Live Hardening 前 5 项完成
按 queue 自动推进，Yu 连续完成：
1. ✅ ProductionAdvisor (79277e1) — query_planner 不再是 stub
2. ✅ Adjutant 规则路由 (d45688e) — 简单命令 0 LLM 延迟
3. ✅ 前端 WS 鲁棒性 (e5f4a9d) — 大帧限制+重连清理+状态指示
4. ✅ 系统可观测性 (ce400f8) — LLM reasoning 可见+组件过滤+benchmark 限流
5. ✅ UnitRegistry (7594501) — 从 yaml 加载，补齐 design.md 启动链
6. ✅ Live 测试自动化 (6499ac5) — test_live_e2e.py runner with phase-based CLI

## [2026-04-01 12:25] DONE — Round 7 全链验证 + Recon 收口修复
Round 7 结果（Hardening 后首次干净开局验证）：
- 建造兵营 ✅ succeeded，兵营 1→2
- 生产3个步兵 ✅ succeeded，步兵 0→3
- 探索地图 ✅ 功能正确（单位移动），但 40s 内未终态 → 已修收口条件
- 战况如何 ✅ 完整中文简报
- 矿场建造 ✅（earlier confirmed）
- 部署无 MCV ✅ 即时提示

Round 7 期间修复：
- 3c9787d: game reset 检测（外部重启游戏后自动清理旧 runtime）
- 4e94f81: deploy 无 MCV 快速返回
- 5455fc8: rule-routed task monitor-only（不再 LLM 漂移）
- 946e7e7: Recon 收口条件（超时→partial + waypoint 保持）
- 多个 UI 改进（可读任务编号、task trace、全局清空）

## [2026-04-02] DONE — NLU 完整接入 (由用户直接指挥 Yu)
- 1d64301: 旧 NLU 前半段接入 Adjutant（shorthand + 复合序列）
- 0925062: 完整 NLU runtime 集成（deploy_mcv/produce/explore/mine/stop_attack/query_actor）
- 不使用旧 SimpleExecutor，执行层全部走当前 Adjutant/Kernel/Expert 链
- 24 adjutant + 10 routing + 8 game_control + 9 ws 测试通过

## [2026-04-04 00:00] DONE — 文档结构深度整理
- docs/wang/: 40+ 文件 → 15 个活跃文件，其余归档到 archive/
- 移入 archive: 23 个 xi_* 审计文件、7 个已完成调查文件、4 个 benchmark 数据文件、1 个重复 feedback 文件
- 根目录: FIXES_SUMMARY.md + FIX_LLM_STATS.md → docs/archive/（过时的 2026-01 修复记录）
- 保留 15 个活跃文档：design.md、dev_progress.md、implementation_plan.md、research docs、system analysis 等

## [2026-04-04 00:30] DONE — 开发进度深度整理
- 分析最近 100 个 git commit (2026-03-20 ~ 2026-04-04)
- dev_progress.md 补充 Phase 2-9 完整进度（之前只有 Phase 0-1）
- 100 commits = 50 fix + 20 feat + 19 docs + 7 chore + 3 test + 1 refactor
- 覆盖：Xi Live集成 → 基础设施硬化 → Live测试Rounds 3-7 → 架构Hardening → Expert知识集成 → NLU完整接入

## [2026-04-04 01:00] DONE — 系统问题与 Agent 设计缺口审计
- 研读 Yu 的 task_agent_prompt_runtime_report.md（Task #001 深度分析）
- 对比 design.md + 当前代码，识别 10 个设计缺口（3 Critical / 3 High / 4 Medium）
- 关键发现：
  - Phase Policy 完全缺失（所有非 rule-routed 任务都会漂移）
  - Task→Player 对话工具未实现（design.md §6 核心能力）
  - Context 无结构化 runtime facts，LLM 靠猜
  - Conversation history 无限增长（Task #001 膨胀到 79K 字符）
  - DeployExpert fire-and-forget 无验证
  - complete_task 无 hard guard
- 已修复 10 项 vs 仍未修 10 项
- 输出：docs/wang/system_issues_and_design_gaps.md

## [2026-04-04 02:00] DONE — Log 系统审计与设计漂移定位
- 审计 logging_system、benchmark、Diagnostics 基础设施 → 评分 8.5/10
- Log 基础架构优秀：统一 slog API、JSONL 持久化、按任务分流、benchmark 分离
- 深度分析 session-20260401T190855Z runtime 日志 (14,277 条)
- 定位 8 处设计漂移，核心证据：
  - Task #001 "展开": 40 次 LLM 调用 (设计期望 2)，8 个 Job (期望 1)，虚假 succeeded
  - 89% wakes 是定时轮询 (设计要求"事件驱动，收到 Signal 才醒来")
  - resource_lost 在 6/8 jobs 中先于 job_started (信号顺序反直觉)
  - 0 条 task→player 消息 (设计要求通过 Adjutant 结构化通信)
  - Conversation 从 4.6K 膨胀到 79K 字符 (设计要求"压缩摘要")
- 输出：docs/wang/log_audit_and_design_drift_report.md

## [2026-04-04 03:00] DONE — 文档二次精简 + 优化任务文档
- docs/wang/ 从 17 文件精简到 6 文件（agents/design/dev_progress/optimization_tasks/plan/progress）
- 归档：10 个旧研究/分析文件 + 2 个审计报告（发现已整合进任务文档）
- 调研 OpenRA 专家知识集成现状：P0 全部完成，缺 soft strategy（开局模板、科技前置、放置策略）
- 输出：optimization_tasks.md — 10 个任务（T1-T10），含问题/目标/具体改动/验收/依赖关系

## [2026-04-04 04:00] DONE — 审计覆盖地图 + Xi 补全任务
- 绘制完整审计覆盖地图：活跃代码 ~13,300 行 Python + ~1,200 行 Vue/JS
- 已深审 37% (task_agent/experts/models/logging)，中度审 20%，未审 42%
- 识别 15 项遗漏：10 个模块级 + 5 个维度级
  - 模块：main.py、game_loop、kernel(cancel/preemption/event routing)、world_model(分层刷新/事件检测)、adjutant(query/pending_question/NLU)、game_api、ws_server、queue_manager、llm/provider、前端
  - 维度：错误恢复、Constraint 系统、ResourceNeed 声明式模型、Adjutant 对话管理、主动通知链
- 输出：audit_coverage_and_xi_task.md

## [2026-04-04 06:00] DONE — 任务分配启动，T1 派给 Xi
- 执行顺序：T1→T2→T3→T10→T5→T4→T6→T11→T12→T8→T7→T9→T13→T14→T15
- T1 (Runtime Facts) 已分配给 Xi，调研后实装
- Wang 负责进度监督，不做逐行审计

## [2026-04-04 06:32] DONE — T1 完成，T2 分配
- Xi 完成 T1 (commit e443829)：5 文件 265 行，5 个新测试
  - world_model/core.py: compute_runtime_facts() — 建筑检测/tech_level/mcv/harvester/can_afford
  - kernel/core.py: _sync_world_runtime 扩展 job_stats
  - task_agent/context.py + agent.py: runtime_facts 注入 + provider 模式
  - SYSTEM_PROMPT 增加 runtime_facts 优先级说明
  - 17 WorldModel + 21 TaskAgent 测试全部通过
- T2 (Task→Player 通信) 已分配给 Xi (msg_87740)

## [2026-04-04 06:55] DONE — T2 完成，T3 分配
- Xi 完成 T2：6 文件 163 行变更
  - task_agent/tools.py: send_task_message 第 12 个 tool
  - task_agent/handlers.py: handle_send_task_message + KernelLike 协议扩展
  - main.py: _publish_task_messages 改用 task_message WS 类型
  - ws_server/server.py: 新增 send_task_message → broadcast
  - ChatView.vue: info/warning 色边框气泡 + question 选项按钮卡片（修复 Xi 审计 10.5）
  - agent.py SYSTEM_PROMPT: send_task_message 使用指南
- 注意：Xi 忘了 commit，已提醒
- T3 (Deploy 验证) 已分配给 Xi (msg_87748)

## [2026-04-04 07:00] DONE — T3 完成，T10 分配
- Xi 完成 T3 (commit a682d90)：两阶段 deploy 验证
  - deploy.py: phase 1 deploy → phase 2 verifying（每 0.5s tick 查新 CY）
  - 成功：新 CY 出现 → SUCCEEDED with yard_actor_id
  - 失败：5s 超时 → FAILED with reason
  - game_api_protocol.py: GameAPILike 新增 query_actor + get_actor_by_id
  - 14 个测试全通过
- T2 补 commit (955b136)
- T10 (LLM Provider Timeout/Retry) 已分配给 Xi (msg_87753)

## [2026-04-04 07:05] DONE — T10 完成，T5 分配
- Xi 完成 T10 (commit 15f934d)：provider 层 timeout + retry
  - _call_with_retry helper：per-attempt asyncio.wait_for + 指数退避 (1s/2s)
  - 429/500/502/503 重试，400/401/404 fast-fail，TimeoutError 立即传播
  - QwenProvider + AnthropicProvider 均接入
  - 发现：adjutant 已有外层 wait_for，但 provider 自足仍有价值
  - 12 个新测试 + task_agent 21 tests 全通过
- T5 (Signal 顺序) 已分配给 Xi (msg_87757)

## [2026-04-04 07:10] DONE — T5 完成，T4 分配
- Xi 完成 T5 (commit 63cf028)：1 行修复
  - kernel/core.py start_job(): slog("job_started") 移到 _rebalance_resources() 之前
  - 确认 _rebalance_resources() 不依赖 slog 调用，无副作用
  - 新增顺序验证测试，17 kernel tests 全通过
- T4 (Conversation 压缩) 已分配给 Xi (msg_87759)

## [2026-04-04 07:15] DONE — T4 完成，T6 分配
- Xi 完成 T4 (commit 4c865ef)：三层压缩
  - 滑动窗口：conversation_window=6 轮，超出静默丢弃
  - Signal 去重：连续相同 kind → 保留最后一条 + ×N
  - Tool result 截断：query_world data >5 项截断，任意 >2000 chars 硬截断
  - 9 个新测试，30 tests 全通过，12 轮后 total_chars < 20K
- T6 (Smart Wake) 已分配给 Xi (msg_87763)

## [2026-04-04 07:28] DONE — T6 完成，T11 分配
- Xi 完成 T6 (commit 0d2217a)：smart wake
  - _last_job_snapshot 对比：无 signal + 无 event + job 状态未变 → 跳过 LLM
  - trigger label 精化：event/review/timer 三种
  - 5 个新测试，35 tests 全通过
- T11 (Adjutant 降级路由) 已分配给 Xi (msg_87765)

## [2026-04-04 07:32] DONE — T11 完成，T12 分配
- Xi 完成 T11 (commit 20f1d0d)：rule_based_classify 新增 reply 检测
  - 精确匹配 option (confidence=0.9) + 模糊匹配常见词 (confidence=0.6)
  - 无 pending_question 时不误判
  - 5 个新测试，29 adjutant tests 全通过
- T12 (WS 频率控制) 已分配给 Xi (msg_87767)

## [2026-04-04 07:34] DONE — T12 完成，T8 分配
- Xi 完成 T12 (commit 78377d0)：WS 限频
  - world_snapshot + task_list：距上次 <1s 跳过
  - 其他消息类型不限频
  - 4 个新测试，13 ws tests 全通过
- T8 (OpenRA 知识补全) 已分配给 Xi (msg_87769)

## [2026-04-04 07:39] DONE — T8 完成，T7 分配
- Xi 完成 T8 (commit 9c5810f)：OpenRA soft strategy
  - knowledge.py: 开局序列(Allied/Soviet)、科技前置、反制推荐、放置策略
  - planners.py: 空基地 → build_opening，counter_recommendation 集成
  - 8 个新测试，14 knowledge+planner tests 全通过
  - 反制关系和科技前置标注 TODO 待实际验证
- T7 (Information Expert) 已分配给 Xi (msg_87771)

## [2026-04-04 07:44] DONE — T7 完成，T9 分配
- Xi 完成 T7 (commit 034879b)：2 个 Information Expert
  - BaseStateExpert: base_established / base_health_summary / has_production
  - ThreatAssessor: threat_level(4级) / threat_direction / enemy_composition_summary
  - 注册机制：WorldModel.register_info_expert() → compute_runtime_facts 合并到 info_experts key
  - 异常防护：单个 expert 崩溃不影响整体
  - 14 个新测试全通过
- T9 (Adjutant 可观测性) 已分配给 Xi (msg_87774)

## [2026-04-04 08:38] DONE — T9 完成，T13 分配
- Xi 完成 T9 (commit d7739a8)：Adjutant 可观测性
  - 每条玩家输入 3-4 条结构化日志（NLU/Rule/LLM 三条路径）
  - NotificationManager 启用：通知携带 icon + severity 元数据
  - 删除 _notification_offset 死代码
  - 3 个新测试，32 adjutant tests 全通过
- T13 (Constraint 完整实现) 已分配给 Xi (msg_87780)

## [2026-04-04 09:58] DONE — E2E Bug 修复（8 个问题，3 commits）
- Xi 完成 E2E 测试发现的 8 个 bug：
  - Bug1 (10a952b): DiagPanel trace 区域 flex:1 min-height:250px，不再挤
  - Bug3 (3502621): Agent 首次 wake 发"正在分析..."，create_job 发"正在部署..."
  - Bug4 (10a952b): TTS 前端先检查 Content-Type，非 audio/* 静默跳过
  - Bug5 (22403b4): Task.label 顺序编号 001/002，Kernel 维护 _task_seq
  - Bug6 (3502621): llm_succeeded 增加 response_text_preview + tool_calls_detail
  - Bug7 (3502621): LLM 分类新增 cancel 类型 + active_tasks 注入 + _handle_cancel()
  - Bug8 (10a952b): DiagPanel 显示选中 task 的 log_path
- 待跟进：#2 (debug toggle 额外消息) 和 #9 ("发展科技"无动作) 需用户确认

## [2026-04-04 10:10] DONE — E2E 问题根因分析 + 架构 v2 方向确定
- #9 根因定位：bootstrap early return 跳过 LLM，62 次 wake 0 次 LLM 调用
- 用户确认架构方向：
  1. Bootstrap 预创建 Job 可以保留，但 LLM 必须参与每轮 wake
  2. Adjutant 路由时也需要世界信息（不然会在 0 兵时 bootstrap ReconExpert）
  3. 订阅机制：Info Expert 按任务类型注入，必订基础世界信息
  4. 并行 tool call：类 Claude Code 循环
  5. Expert = 独立 tool
- 紧急修复 1 (Bootstrap LLM 参与) 已分配给 Xi (msg_87815)

## [2026-04-04 10:44] DONE — Expert-as-tool 重构完成，Agent Loop 分配
- Xi 完成架构改造1 (commit ee29698)：Expert-as-tool
  - 5 个独立 tool 替换 start_job：deploy_mcv/scout_map/produce_units/move_units/attack
  - SYSTEM_PROMPT 精简 ~40%，config schema 信息移入 tool definitions
  - 6 个新 handler 测试 + 现有测试更新
- 架构改造2 (Agent Loop 并行 tool call) 已分配给 Xi (msg_87824)

## [2026-04-04 10:50] DONE — Agent Loop 完成，订阅机制分配
- Xi 完成架构改造2 (commit 516682e)：并行 tool call
  - _execute_tools 改用 asyncio.gather，异常独立隔离
  - 2 个新测试：并行执行验证 + 异常隔离验证
- 架构改造3 (订阅机制) 已分配给 Xi (msg_87827)

## [2026-04-04 11:00] DONE — 订阅机制完成，架构改造全部完成
- Xi 完成架构改造3 (commit db72dee)：订阅机制
  - Task.info_subscriptions 字段 + Adjutant 按 Expert 类型设置初始订阅
  - context.py 按订阅过滤 info_experts 数据
  - update_subscriptions tool：LLM 可动态增减
  - 4 个新测试全通过
- 架构改造 5 commits 全部完成：Bootstrap 修复 → 名称彻查 → Expert-as-tool → 并行 tool call → 订阅机制

## [2026-04-04 10:34] DONE — 紧急修复 1+2 + 名称彻查完成
- 修复1 (b0b27ef): 去掉 bootstrap early return，LLM 每轮 wake 都运行
  - 4 处 early return 改为不阻断：bootstrap 副作用保留，LLM 不被跳过
  - 防重复创建 job 的显式检查
  - 3 个旧测试更新 + 1 个新测试
- 修复2 (7e01e7d): Adjutant rule 路由前检查世界状态
  - _check_rule_preconditions(): ReconExpert 0 兵时发警告
  - Task+Job 仍创建，让 LLM 自行决定
- 名称修正 (7e01e7d): 对照 Copilot.yaml 彻查 9 处
  - "指挥中心" → "基地"（3 处 + 1 处注释）
  - "高级发电厂" → "大电厂"（3 处）
  - "车厂" → "坦克厂"/"车间工厂"（2 处）

## [2026-04-04 09:15] DONE — T13 完成，T14 分配
- Xi 完成 T13 (commit 064f350)：Constraint 完整实现
  - ESCALATE: Signal(CONSTRAINT_VIOLATED) 发送给 Task Agent
  - ReconJob: defend_base (CLAMP 过滤候选 / ESCALATE 上报)
  - EconomyJob: economy_first (CLAMP 阻止非经济生产 / ESCALATE 通知)
  - MovementJob: do_not_chase (CLAMP 跳过移动 / ESCALATE 通知后仍执行)
  - DeployJob: 确认无适用 constraint，跳过
  - 10 个新测试 + 现有 38 tests 无回归
- T14 (前端取消+阿里云语音) 已分配给 Xi (msg_87783)

## [2026-04-04 09:23] DONE — T14 完成，T15 分配
- Xi 完成 T14 (commit f549b63)：前端取消 + 阿里云语音
  - TaskPanel: 活跃任务 ✕ 取消按钮 → command_cancel
  - ASR: voice/asr.py + /api/asr，paraformer-realtime-v2，录音→DashScope→文本
  - TTS: voice/tts.py + /api/tts，cosyvoice-v1 longxiaochun，默认关闭
  - 前端: 麦克风按钮 + pulse 动画 + 识别结果填入输入框
  - 12 个测试全通过
  - .env key: QWEN_API_KEY 作为别名读取
- T15 (零散修复) 已分配给 Xi (msg_87796) — 最后一个任务

## [2026-04-04 09:28] DONE — T15 完成，全部 15 个优化任务完成 🎉
- Xi 完成 T15 (commit c3de178)：6 项零散修复
  - 11b: GameAPI 断连 >30s → player_notification 告警升级
  - 4c: find_actors() 新增 mobility 过滤参数
  - 5d: format_task_message() 保留 + 注释说明（T2 已替代主路径）
  - 6e/14a/14d: 注释说明
  - 所有测试通过

**=== T1-T15 全部完成 ===**
15 个 commits，执行时间约 2.5 小时：
- T1  (e443829): Runtime Facts 注入
- T2  (955b136): Task→Player 通信工具
- T3  (a682d90): DeployExpert 结果验证
- T10 (15f934d): LLM Provider Timeout/Retry
- T5  (63cf028): Signal 日志顺序修正
- T4  (4c865ef): Conversation History 压缩
- T6  (0d2217a): Smart Wake
- T11 (20f1d0d): Adjutant 降级路由修复
- T12 (78377d0): WS 消息频率控制
- T8  (9c5810f): OpenRA 知识补全
- T7  (034879b): Information Expert 实现
- T9  (d7739a8): Adjutant 可观测性
- T13 (064f350): Constraint 完整实现
- T14 (f549b63): 前端取消+阿里云语音
- T15 (c3de178): 零散修复

## [2026-04-04 05:00] DONE — Xi 审计整合 + optimization_tasks.md v3
- 完整阅读 Xi 审计报告（15 项 50 子项，~672 行）
- 统计：✅ 31 (62%) | ⚠️ 14 (28%) | ❌ 5 (10%)
- 新增 5 个优化任务整合 Xi 发现：
  - T10: LLM Provider Timeout & Retry (P0，Xi 9b+9c，adjutant 路径无保护)
  - T11: Adjutant 降级路由修复 (P1，Xi 14b，rule_based_classify 无法产出 reply)
  - T12: WS 消息频率控制 (P1，Xi 7a，可能 10x 设计量)
  - T13: Constraint 系统清理 (P2，Xi 12b+12c，escalate 死代码 + 4/5 Expert 忽略)
  - T14: 前端功能补全 (P2，Xi 10.2+10.9，取消按钮 + ASR/TTS stub)
  - T15: 零散修复 (P2，Xi 4c/5d/6e/11b/14a/14d)
- 更新已有任务 T2/T9 整合 Xi 发现（10.5 pending_question 文本模式、5d NotificationManager 死代码）
- 优化任务总数：9 → 15 (T1-T15)
- 已回复 Xi 确认审计收到 (msg_87657)

## [2026-04-04 09:50] DONE — 架构改造2：并行 tool call via asyncio.gather

commit 516682e。`_execute_tools` 改为 `asyncio.gather(*coros, return_exceptions=True)`。
`_EXPERT_TOOL_NAMES` frozenset 替换了旧的 `"create_job"` 引用。
新增 test_execute_tools_parallel + test_execute_tools_exception_isolation，均通过。
所有架构改造（改造1 ee29698 + 改造2 516682e）全部完成。

## [2026-04-04 10:05] DONE — 架构改造3：订阅机制 — Task.info_subscriptions

commit db72dee。Task 新增 info_subscriptions 字段。Adjutant 路由时按 expert_type 设置。
context.py 按订阅过滤 runtime_facts["info_experts"]。update_subscriptions tool 允许 LLM 动态调整。
"production" subscription 是 placeholder（无对应 InfoExpert）。4 个新测试，全部通过。
三次架构改造（b0b27ef + ee29698 + 516682e + db72dee）全部完成。

## [2026-04-04 10:55] DONE — BUG1：Expert-as-tool handler 未注册

commit 0fbb4ff。根因：kernel._build_tool_executor() 手动维护旧的 11 个 handler 列表，
Expert-as-tool 重构新增的 6 个工具从未加入。修复：改用 TaskToolHandlers.register_all()
作为唯一注册源。签名从 task_id: str 改为 task: Task。删除冗余的 _tool_update_subscriptions。

## [2026-04-04 11:10] DONE — BUG7：去掉 EconomyJob PRODUCTION_QUEUE 独占锁

commit 5d0823c。根因：get_resource_needs() 声明 PRODUCTION_QUEUE → Kernel 串行分配 → 并发建造任务 queue_unassigned 卡等。
修复：return []，删除 queue_unassigned 检查，_cleanup_queue_on_abort 改为 no-op（共享队列不能按 unit_type 取消）。
顺手清理：原代码有 slog 未导入的存量 bug，no-op 后消除。13 个 Economy 测试通过。

## [2026-04-04 11:20] DONE — BUG2：多轮 tool call 内上下文不刷新

commit 2cd2f3b。根因：build_context_packet() 只在 wake 入口调一次，多轮 loop 中 LLM 看不到中间状态。
修复：tool 执行后、continue 前注入 fresh context_to_message()，只加到 messages 不加到 conversation。
测试：dynamic_jobs_provider 验证 turn-2 messages 含新 job 的 context。

## [2026-04-04 11:30] DONE — BUG3：虚假成功/失败 — 基于世界观察而非 Job 状态

commit 6cca834。三处信息增强：
1. SYSTEM_PROMPT 新增完成判定规则（Job status 优先，不能仅凭世界观察）
2. context.py 每个 Job 新增 status_zh 中文标签（等待中尚未生效/已成功完成/已中止）
3. handle_complete_task 在无 Job succeeded 时返回 job_status_warning（不阻止）
KernelLike 新增 jobs_for_task。3 个新测试通过。

## [2026-04-04 11:30] DONE — BUG4：LLM 任务范围越界（scope creep）

commit e89ebbd。信息方案：注入 other_active_tasks 让 LLM 看到并发任务列表。
- kernel: _other_active_tasks_for(task_id) 返回同级活跃任务
- context.py: ContextPacket.other_active_tasks 字段
- agent.py: ActiveTasksProvider + SYSTEM_PROMPT 新增 "Multi-task scope discipline"
- 5 个新测试，44/44 通过。

## [2026-04-04 11:35] DONE — BUG5：顺序依赖任务并发执行

commit c8bd02f。信息方案：增强 cannot_produce 信号的前置条件信息。
- knowledge.py: 步兵 e1-e6 前置条件 → 兵营(barr)，新增 display_name_for()
- economy.py: BLOCKED 信号 "当前无法生产 e1：缺少前置建筑（兵营）"
- SYSTEM_PROMPT: "Prerequisite waiting discipline" — 有其他任务处理前置就等待，无则报告
- 3 个新测试通过。

## [2026-04-04 11:40] DONE — BUG6：LLM 响应时间 vs 决策时效

commit c285d11。根因：job signal 从 asyncio.to_thread 调用 Event.set() 线程不安全，唤醒不及时。
修复：game_loop _tick_jobs 中检测 Job terminal 状态变化 → 从事件循环线程调 trigger_review()。
Job 完成到 Agent 唤醒延迟从 ~10s 降至 <100ms。Smart Wake 对持久 WAITING 确认有效。
11/11 game_loop + 4/4 smart_wake 测试通过。

**=== E2E BUG 全部修复 ===**
7 个 commits（BUG1 + BUG7 + BUG2-6），执行时间约 50 分钟：
- BUG1 (0fbb4ff): Expert-as-tool handler 注册断裂
- BUG7 (5d0823c): PRODUCTION_QUEUE 独占锁移除
- BUG2 (2cd2f3b): 多轮 tool call 上下文刷新
- BUG3 (6cca834): 虚假成功/失败（信息增强）
- BUG4 (e89ebbd): Scope creep（sibling tasks 注入）
- BUG5 (c8bd02f): 前置条件等待信号增强
- BUG6 (c285d11): Job 完成即时唤醒 Agent

## [2026-04-04 14:30] DONE — ReconJob 探索算法重写（随机射线，参考 ExploreJob）

commit 3f0e990。原 ReconJob 用 3-5 个固定百分比坐标，完全不参考 IsExplored 网格。
重写为 ExploreJob 的随机射线算法：
- 从 WorldModel 获取 IsExplored 二维数组，用 Bresenham 线采样路径未探索比例
- 每个 actor 独立 _ScoutState（golden-angle 分散、stuck 检测、visited 集合）
- 逐圈扩大搜索半径 + 阈值递减
- scout 间 repulsion 防止挤在一起
- 支持多 actor（get_resource_needs count 可配置）
保留原 ReconJob 的 signal/constraint/retreat/tracking 框架。

## [2026-04-04 14:55] DONE — Adjutant 对话上下文增强（任务结果注入）

commit 40fdfe5。根因：_build_context 注入的 context 缺少任务执行结果，LLM 无法理解抽象跟进指令。
三处信息增强：
1. AdjutantContext.recent_completed_tasks 字段 + _recent_completed buffer（最多 5 条）
2. notify_task_completed(label, raw_text, result, summary) — 写入 dialogue_history + _recent_completed
3. CLASSIFICATION_SYSTEM_PROMPT 新增 "Dialogue context awareness" 段，指导 LLM 处理模糊/跟进输入
4. main.py Bridge：TASK_COMPLETE_REPORT 消息发布后立即调用 adjutant.notify_task_completed()
顺手修复：MockKernel.create_task 补 info_subscriptions 参数（存量 bug）。
37/37 adjutant 测试通过。

## [2026-04-04 15:00] DONE — CombatJob 攻击逻辑增强（参考 AttackJob）

commit e9e4c93。4 处逻辑增强：
1. _engage_assault：per-unit 最近敌人分配（原来全军打 enemies[0]）
2. _tick_engaging 无敌人路径：非 hold 模式改为 attack-move 推进，_MAX_ADVANCE_TICKS=20 后再 partial
   hold 模式保持原行为（立即 recon-first partial）
3. _advance_toward_threat：按威胁方向推进 + _ADVANCE_OFFSETS 分散阵型
4. _choose_threat_direction：enemy_actors 质心 → map center → fallback (+20,+20)
新增 _step_toward 静态方法（曼哈顿方向，参考 AttackJob._step_towards）。
_advance_ticks 接触敌人时归零。
3 个新测试；test_engaging_clears_area 改为 HOLD 模式；14/14 通过。

## [2026-04-04 15:30] DONE — E2E Round 3 深度分析 + BUG-A/B/C 派发

E2E session-20260404T150421Z 分析（12 tasks，5 completed，7 stuck）：
- 3 个并行调研 agent：NLU 置信度、电厂去重、bootstrap 机制
- 精确定位 bootstrap 不闭环根因：雷达任务 TASK_COMPLETE signal 在 23:11:10 被 LLM 消费，LLM 返回 text 不是 complete_task，此后 100+ wake_skipped
- 派 BUG-A/B/C 给 Xi (msg_87881)

## [2026-04-04 16:00] DONE — BUG-A/B/C 修复验证

commit 5bbaa27，Xi 修复 3 个 bug：
- BUG-A: bootstrap 改为直接查 Job status，不依赖 signal
- BUG-B: _ACKNOWLEDGMENT_WORDS 最高优先级检测
- BUG-C: _QUESTION_RE 在 NLU 入口拦截疑问句
220 行变更，42 测试通过。已验证代码正确。

## [2026-04-05 00:00] DONE — 4 个设计改进方案确定 + 派发

用户确认 4 个设计改进方案：
- D1: composite_sequence 顺序执行（方案 A — Adjutant 层延迟创建，不改 Kernel）
- D2: runtime_facts boolean → count（建筑计数替代布尔）
- D3: feasibility 预处理清单（各 Expert 可行性预判）
- D4: Adjutant 全历史可见性（所有 TaskMessage 写入 dialogue + 分类窗口扩大）
已派给 Xi (msg_87886)，执行顺序 D2→D3→D4→D1。

**BUG-A（bootstrap 不闭环）**：`_maybe_finalize_bootstrap_task` 改为直接查 job status，不再依赖 signal。
根因：TASK_COMPLETE signal 在 LLM wake 被消费后不再重现，smart wake 跳过导致任务永不关闭。
修复：通过 `_jobs_provider` 查 bootstrap job status（SUCCEEDED/FAILED/ABORTED 即触发）。
测试：更新 `test_bootstrap_structure_build_completes_with_llm_running` 使用 stateful jobs_provider；
新增 `test_bootstrap_finalizes_on_job_status_without_signal`。

**BUG-B（ack 误分类）**：`handle_player_input` 最前加 acknowledgment 检测。
`_ACKNOWLEDGMENT_WORDS` frozenset，匹配后立即返回 `type=ack`，不创建任务。
有 pending questions 时跳过 ack 检测（可能是 reply）。2 个新测试。

**BUG-C（NLU 疑问句误路由）**：`_try_runtime_nlu` 最前加 `_QUESTION_RE` 检测。
包含 为什么/怎么/吗$/呢$/什么时候/如何 → 返回 None，NLU 不执行。

## [2026-04-05 02:15] DONE — D1-D4 代码验证通过

Xi 完成 D1-D4 全部改动（提醒 commit）：
- D2: world_model/core.py — 5 个建筑 bool → count，遍历 actor 计数
- D3: world_model/core.py — feasibility 字段，预判 5 个 tool 可行性
- D4: adjutant.py + main.py — notify_task_message，WARNING/INFO 写入 dialogue，分类窗口 5→10
- D1: adjutant.py — _pending_sequence + _advance_sequence 延迟创建，失败取消剩余
adjutant 40/40, world_model 31/31, combat 14/14, task_agent 51/53（2 pre-existing）。
1 个新测试，6 种疑问句均不创建任务。

adjutant: 40/40，bootstrap: 2/2 新测试通过。

## [2026-04-04 00:00] DONE — D2/D3/D4/D1 设计改进全部落地

**D2（runtime_facts boolean → count）**：`world_model/core.py` `compute_runtime_facts()`
- `has_power/barracks/refinery/war_factory/radar` → `power_plant_count/barracks_count/refinery_count/war_factory_count/radar_count: int`
- 改为遍历 actor list 计数（每个 actor 的 `{name, display_name}` 与 name set 求交）
- `tech_level` 判断改用 `count > 0`
- 更新：`experts/info_base_state.py`、`tests/test_world_model.py`、`tests/test_info_experts.py`、SYSTEM_PROMPT

**D3（feasibility 预处理清单）**：`world_model/core.py`
- 新增 `combat_unit_count`（INFANTRY+VEHICLE）到 actor 循环
- `facts["feasibility"]` = `{deploy_mcv, scout_map, produce_units, attack, move_units}`
- 每项为 bool，直接反映当前是否可执行

**D4（Adjutant 全历史可见性）**：
- `adjutant.py` 新增 `notify_task_message(task_id, message_type, content)` — TASK_WARNING/INFO 写入 dialogue
- `_classify_input()` 分类窗口 `[-5:]` → `[-10:]`
- `main.py` TASK_WARNING/INFO 触发 `notify_task_message()`

**D1（composite_sequence 顺序执行）**：`adjutant/adjutant.py`
- `_pending_sequence: list[DirectNLUStep]`、`_sequence_task_id: str | None` 状态
- `_handle_runtime_nlu()` composite_sequence 时只创建第1步任务，其余存入 `_pending_sequence`
- `_advance_sequence(result)` — 成功时启动下一步；失败时取消剩余并记录 dialogue
- `notify_task_completed()` 新增 `task_id` 参数，匹配 `_sequence_task_id` 时调用 `_advance_sequence`
- `clear_dialogue_history()` 同时清空序列状态
- main.py `notify_task_completed()` 调用补充 `task_id=message.task_id`
- 测试：更新 `test_runtime_nlu_routes_safe_composite_sequence_into_multiple_direct_jobs` 验证分步执行

所有核心测试：adjutant 40/40，world_model 31/31，combat 14/14，task_agent 51/53（2 pre-existing failures）。

## [2026-04-05 00:00] DONE — E2E R4 — 7 个 bug 全部修复

**R4-2 [P0]**：删除 `world_summary.map.is_explored` grid（128×128 bool ~28K token），LLM 只需 `explored_pct`。[2d9bca0]

**R4-1 [P0]**：`Kernel.complete_task()` 新增 TASK_COMPLETE_REPORT 消息注册（直接 append 绕过 terminal status guard），修复序列不推进。[06316c4]

**R4-3 [P1]**：EconomyJob 批量下单 — Infantry/Vehicle queue 一次性 `produce(unit_type, remaining)`，Building 保持逐个。测试更新。[cc3f922]

**R4-4 [P1]**：ReconJob `_complete_timeout()` 状态从 SUCCEEDED 改为 FAILED（signal result="partial" 但 status=SUCCEEDED 矛盾）。[167c51d]

**R4-5 [P1]**：EconomyJob `_initial_matching_actor_ids` frozen at init，`_sync_direct_actor_completions` 始终排除初始集，防止延迟同步的已有 actor 被误计为新产出。[db7181a]

**R4-7 [P1]**：SYSTEM_PROMPT 单位映射表扩充 — 建筑 12 种（含 apwr/silo/kenn）、防御 7 种、步兵 7 种、车辆 10 种，每种含中文别名。[a80c42a]

**R4-6 [P2]**：scout_map tool 新增 `scout_count` 参数（integer, default 1），handler 传入 ReconJobConfig。[768e546]

## [2026-04-05 03:00] DONE — SYSTEM_PROMPT 重写 + Context 紧凑格式（Wang 直接改动）

**问题诊断**：从 E2E R4 日志提取真实 LLM 输入输出：
- 简单"矿场"任务：context 117K chars，其中 is_explored grid 占 114K（97%）
- LLM 输出 298 字自言自语分析，零有效行为
- "发展科技"任务 6 轮后 context 超 131K token → BadRequestError
- SYSTEM_PROMPT 62 行教程式，防御性补丁堆叠，输出格式无约束

**用户 6 条设计反馈**（核心洞察）：
1. "raw_text exclusive"过度收缩 → 改为"goal-bounded minimal support actions"
2. 前置条件分两类：A.可局部补齐（造1兵去侦察）B.大前置链（需车厂才能造坦克）
3. 完成判定：goal verified achieved 优先于 job status bookkeeping
4. warning = 战场紧急风险，不是"缺兵营"
5. 空转防护：相同阻塞原因不重复发送
6. 统一决策信息优先级：runtime_facts > signals > query_world > world_summary

**SYSTEM_PROMPT 改造**：62 行 4.5K chars → ~35 行 ~1.8K chars
- 中文指令式，禁止输出思考过程
- "需要行动→tool call / 等待→wait / 通知→send_task_message"
- 完整单位速查表（建筑+步兵+车辆 ID 映射）
- 前置条件 A/B 分类处理
- goal-verified 完成判定

**Context 紧凑格式**：JSON dump → 结构化文本行
- `[任务] 矿场 | 状态:running | id:t_xxx`
- `[Job] j_xxx EconomyExpert → 运行中 unit_type=proc count=1`
- `[世界] 资金4600 资源0 电力80/100 | 我军3(闲置3) 敌军0 | 探索1.0%`
- `[状态] power_plant_count=1 | barracks_count=1 | 可行=[produce_units] | 不可行=[scout_map,attack]`
- 同样信息：117K chars → ~500 chars（**99.6% 压缩**）

**测试**：62/62 task_agent + 17/17 world_model 全部通过（更新 6 个旧格式断言）。
待 Xi 修复 test_tool_handlers.py 后统一 commit。

## [2026-04-05 04:00] DONE — E2E R5 分析完成

Xi 负责部分 commit 6993f5f（`docs/wang/e2e_r5_analysis_xi.md`）。
session-20260404T192621Z，17 tasks，144 LLM calls，0 失败。

正面：
- 经济决策质量高（"继续发展经济"+"爆兵"策略完全合理）
- LLM 自主判断雷达+补电，策略正确
- "爆兵" 2 wakes 产 58 单位，决策密度极高

3 个问题：
- R5-1: ReconJob 30s 超时太短，探索度 <10% 就失败
- R5-2: "大电" 假 succeeded — bootstrap job running 就标记完成
- R5-3: 多 scout_map 并行抢资源无限循环

agent-chat server 未运行，未能回复 wang。等待用户确认下一步。

## [2026-04-05 05:00] DONE — R5 修复 6 个任务全部完成

6 个独立 commit：

**T-R5-1** (94ada7b): 阵营限制单位立即 FAILED + 准确消息
- 根因：e2/e4 苏军专属，2tnk/jeep 盟军专属（非代码 bug，是 OpenRA 游戏规则）
- knowledge.py: _FACTION_RESTRICTED dict + faction_restriction_for()
- economy.py: cannot_produce + 阵营限制 → FAILED（不是 WAITING）
- 消息从"缺少兵营"改为"该单位为苏军/盟军专属"

**T-R5-2** (6668f33): GameAPI 并发 bug — utf-8 + REQUEST_ID_MISMATCH
- utf-8: recv(4096) 拆分多字节字符 → bytearray 累积后解码
- REQUEST_ID_MISMATCH: 错误后 socket buffer 残留 → 关闭 socket 重建连接

**T-R5-3** (74898b1): ReconJob 不再自动超时
- 移除 _max_search_duration_s=30s，改为每 15s 发 progress signal
- 修复 patch_job 不触发 rebalance_resources（3步兵只1个在探索的根因）

**T-R5-4** (c219a3b): Bootstrap job 对 LLM 首轮可见
- re-fetch jobs after bootstrap；标记 [自动创建] 防 LLM 重复创建

**T-R5-5** (7e60ab4): world_refresh 慢刷新诊断
- >100ms 时 log world_refresh_slow + per-layer 毫秒分解

**T-R5-6** (3ea8e53): 语音 ASR 格式不匹配 + JSON 解析
- 后端自动检测 webm/ogg/wav 格式（前端发 webm 但告诉后端 wav）
- 前端 resp.json() 包裹 try-catch 防止非 JSON 响应崩溃

## [2026-04-05] DONE — T-R5-7: log 中文不转义 + EconomyJob 重复建筑 bug 修复

**T-R5-7**: json.dumps ensure_ascii=False（8处 across 5文件）
- logging_system/core.py, benchmark/__init__.py, logging_system/benchmark_tools.py, llm/provider.py, task_agent/agent.py

**R5-8 critical fix**: EconomyJob 同类型建筑第二次立即成功
- 根因：`_last_seen_event_ts = 0.0` 导致新 job 读取全量历史事件，Job2 把 Job1 的 PRODUCTION_COMPLETE 当成自己的
- 修复：`_last_seen_event_ts = time.time()` — 只处理 job 创建之后的事件
- 新增回归测试 test_economy_job_second_identical_building_does_not_see_first_completion

## [2026-04-05] DONE — R6 E2E Expert层深度分析

session-20260405T050701Z（18 tasks, ~10min, 31849 records）深度分析完成：

1. **ReconExpert 0/6 aborted** — 非 T-R5-3 bug。4/6 因 TaskAgent 过早 complete_task(partial)；1/6 误判步兵闲置；1/6 合理去重
2. **expert_logic 7176 次** — 99.6% 来自 ReconExpert。管理 50 个 actor 的 per-actor round-robin 导致，非空转。j_0ed388e0 单 job 贡献 62%
3. **LLM 连续失败 4 tasks** — 不是 LLM 错误。wake_cycle 在 context 构建阶段抛异常，但 exception handler 只写 stderr 不写结构化日志（logger.exception vs slog），异常被吞
4. **ExplorerJob vs ReconJob** — 不能直接替代。核心算法相同（120行重复），但 ReconJob 多了信号/完成检测/战斗感知/约束。建议抽取共享模块
5. **T-R5-1 阵营限制生效** — 6条"盟军专属"blocked信号确认。但 LLM 仍在选 2tnk（阵营感知 prompt gap）

## [2026-04-05] DONE — T-R6-1: ReconJob 探索参数修复 + move 重发机制

修改 experts/recon.py：
- **调参**：_arrival_radius 32→3, _ray_base_radius 60→18, _ray_max_radius 160→60, _ray_radius_step 20→8, _stuck_threshold_ticks 8→10, _ray_repulsion_radius 15→10
- **_arrived 改 Manhattan**：Euclidean→Manhattan 距离匹配 grid 坐标语义
- **move 重发机制**：_last_destinations 去重改为 cooldown 2s 重发，卡住的单位会被重新指挥
- 删除过时注释 "Base radius MUST exceed _arrival_radius (32)"

## [2026-04-05] DONE — T-R6-2: wake_cycle 异常日志盲区修复

修改 task_agent/agent.py：
- `_safe_wake_cycle` exception handler 增加 `slog.error("wake_cycle_error")` 同时写结构化日志
- 包含 error_type, error message, 完整 traceback
- import traceback
