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
