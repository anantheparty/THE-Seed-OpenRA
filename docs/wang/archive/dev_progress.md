# 开发进度

## Phase 0: 清理 + 基础设施 ✅ 完成

### Task 0.1: 删除可删代码
- 分配给：yu
- 审计者：xi
- 状态：✅ **完成**（xi 审计通过）
- commit: 6735e1e

### Task 0.1b: 移除 the-seed 子库
- 分配给：yu
- 审计者：xi
- 状态：✅ **完成**（xi 审计通过）
- commit: a4e7805

### Task 0.2: 数据模型 dataclass
- 分配给：xi
- 审计者：yu
- 状态：✅ **完成**（yu 审计通过，回归审计 zero blockers）
- commits: 861d61d (初版 + 漂移修正), 1f5a7ce (config binding + enum 修复)

### Task 0.3: 项目目录结构
- 分配给：xi
- 审计者：yu
- 状态：✅ **完成**（yu 审计通过）

### Task 0.4: LLM 模型抽象层
- 分配给：xi
- 审计者：yu
- 状态：✅ **完成**（yu 审计通过）
- 已知限制：AnthropicProvider 多轮 tool-use 延后 Phase 1

### Task 0.5: Benchmark 框架
- 分配给：yu
- 审计者：xi
- 状态：✅ **完成**（xi 审计通过）
- commit: a325814

## Phase 1: 核心运行时 ✅ 完成

### Task 1.1: WorldModel v1
- 分配给：yu
- 审计者：xi
- 状态：✅ **完成**（xi 审计通过，zero blockers）
- commit: 3b87195
- 涉及文件：`world_model/core.py`, `world_model/__init__.py`, `tests/test_world_model.py`
- 4 tests passing

### Task 1.2: GameLoop（10Hz 主循环）
- 分配给：xi
- 审计者：yu
- 状态：✅ **完成**（集中审计通过，修复后 zero blockers）
- commits: bd0f4c6 (初版), 001feec (事件去重修复)
- 涉及文件：`game_loop/loop.py`, `tests/test_game_loop.py`
- 7 tests passing
- 审计修复：GameLoop 双重事件路由去重

### Task 1.3a: Kernel Task 生命周期
- 分配给：yu
- 审计者：xi
- 状态：✅ **完成**（集中审计通过，修复后 zero blockers）
- commits: dbda05d (初版), 234c72c (route_events + import 修复)
- 涉及文件：`kernel/core.py`, `kernel/__init__.py`, `tests/test_kernel.py`
- 5 tests passing
- 审计修复：route_events 接口对齐 GameLoop、__globals__ 改正常 import

### Task 1.4: Task Agent agentic loop
- 分配给：xi
- 审计者：yu
- 状态：✅ **完成**（yu 审计通过，回归审计 zero blockers）
- commits: d321a3e (初版), ccbf442 (修复 events/defaults/enforcement)
- 涉及文件：`task_agent/agent.py`, `task_agent/context.py`, `task_agent/tools.py`, `task_agent/queue.py`, `tests/test_task_agent.py`
- 13 tests passing

### Task 2.1: Expert 基类 + Job 基类（提前启动）
- 分配给：xi
- 审计者：yu
- 状态：✅ **完成**（集中审计通过，修复后 zero blockers）
- commits: e2a62cb (初版), 001feec (abort 状态保护 + pause/resume), af8d700 (resume 终态保护)
- 涉及文件：`experts/base.py`, `tests/test_expert_base.py`
- 12 tests passing
- 审计修复：abort+revoke 状态覆盖、pause/resume 更新 status、resume 终态保护

### Task 1.3b+1.3c: Kernel 资源分配 + 事件路由
- 分配给：yu
- 审计者：xi
- 状态：✅ **完成**（xi 审计通过，zero blockers）
- commit: 89614ff
- 涉及文件：`kernel/core.py`, `tests/test_kernel.py`
- 9 tests passing

### Task 1.5+1.7: Task Agent tools + timestamp 传播
- 分配给：xi
- 审计者：yu
- 状态：✅ **完成**（集中审计通过，修复后 zero blockers）
- commits: ddd5004 (初版), 99a9291 (constraint 接通修复)
- 涉及文件：`task_agent/handlers.py`, `tests/test_tool_handlers.py`
- 9 tests passing
- 审计修复：constraint handlers 接通 WorldModel + Protocol 声明 + side effect 测试

### Task 1.3d+1.3e: Kernel 超时 + 自动响应
- 分配给：yu
- 审计者：xi
- 状态：✅ **完成**（xi 审计通过，zero blockers）
- commit: 538cd75
- 涉及文件：`kernel/core.py`, `tests/test_kernel.py`
- 12 tests passing

### Task 1.6+1.8: WS 后端 + review_interval
- 分配给：xi
- 审计者：yu
- 状态：✅ **完成**（集中审计通过，修复后 zero blockers）
- commits: 2728463 (初版), 8e594c4 (Kernel.tick + review wake), 0ba9207 (race-free wake)
- 涉及文件：`ws_server/server.py`, `game_loop/loop.py`, `task_agent/queue.py`, `tests/test_ws_and_review.py`
- 审计修复：GameLoop 接通 Kernel.tick()、review wake race condition 根治

### Task 1.3f: 错误恢复策略
- 分配给：yu (Kernel/WorldModel/GameLoop) + xi (Task Agent)
- 状态：✅ **完成**（交叉审计通过，zero blockers）
- commits: 3846615 (yu: WorldModel stale + GameLoop 断连恢复 + Job 异常捕获), 2dc268f+72c3c21+9882e5c (xi: LLM 连续失败 + 错误隔离 + player warning), 4cfaa3a (yu: Kernel callback wiring)
- 审计修复：player warning 路径打通 agent→Kernel→Adjutant，Kernel factory 传 message_callback

## Phase 2: 前端 + Xi Live 集成 ✅ 完成 (2026-03-20)

Xi 负责前端 Dashboard 和 Live 集成修复，共 7 commits。

- feat(xi): OpsPanel 游戏控制按钮 + WS 路由 (d2035c2)
- feat(xi): 状态持久化 + LLM 超时 fallback (f7fc09b)
- feat: 游戏重启工具链 (471fbba)
- fix(xi): Expert config schemas 注入 Task Agent system prompt (513b112)
- fix: 收紧 BASE_UNDER_ATTACK 检测阈值 (0698b48)
- fix(xi): OpsPanel VNC 占位符 — 防递归 iframe (0a3f6f6)
- fix(xi): ChatView 移除 task_update 冗余订阅 (6863002)

## Phase 3: 基础设施硬化 ✅ 完成 (2026-03-21)

解决 Live 环境暴露的基础架构问题，共 12 commits。

- **GameLoop 阻塞修复**: 同步 GameAPI socket 调用导致 asyncio 饥饿，改用 asyncio.to_thread() (e011507)
- **Legacy 清理**: openra_api Phase 0 遗留代码移除 (49a1d44)
- **日志治理**: OpenCodeAlert GameAPI 日志洪水 (98aa835)，DiagPanel level filter + LLM error details (1d16e0a)
- **Benchmark**: log filtering 优化 (1d16e0a)
- **Adjutant 挂起调查**: 根因定位并记录 (9f8325f)
- 其余为文档更新和杂项

## Phase 4: Live E2E 测试 Rounds 3-5 ✅ 完成 (2026-03-22)

开始 Live 实弹测试，暴露大量集成问题，共 15 commits。

**关键修复:**
- feat: GameAPI 持久 TCP 连接复用 (1c0f106) — 之前每次查询新建连接
- fix: WorldModel actor 类型分类修正 (1946ed4)
- fix: 建筑队列自动放置 (4e46381) — Economy job 误判 API 返回
- fix: 生产名称别名规范化 (59b7b1d) — LLM 说 "PowerPlant"，API 要 "powr"
- fix: 聊天缓存清理 + 命令反馈可见 (6673174, eba4078)
- fix(xi): 建筑类别规则 + 部署虚假成功 caveat (c1fb50b)
- fix(xi): Recon 软资源约束 — 接受任意可用单位 (5afa7bd)
- fix: 阻止建筑被分配给 soft actor jobs (e16ff54)
- fix: 立即启动基地防御 jobs (b176f16)

**测试结果:** Round 3 部分通过 → Round 4 T5/T9 通过 → Round 5 建造链核心修复

## Phase 5: Round 6 深度调试 + 架构 Hardening ✅ 完成 (2026-03-23)

建造/经济链 12+ bug 一次性歼灭，同时完成 6 项架构改进，共 19 commits。高峰日。

**建造链 Bug Stack (7 层):**
1. fix: OpenCodeAlert 建筑放置语义更新 (4255084)
2. fix: 检测停滞的建筑放置 (86104d5)
3. fix: 确定性关闭 bootstrapped build tasks (5bcb137)
4. fix: 建筑放置前不算完成 (deefd20)
5. fix: 已就绪建筑计入经济任务 (7d4b69b)
6. fix: 释放终态 task/job 资源（修复 queue:Building 泄漏）(72b535f)
7. fix: MCV 部署排除出 BASE_UNDER_ATTACK 检测 (c072c31)

**架构 Hardening (Live Hardening 六件套):**
1. feat: ProductionAdvisor — 首个 Planner Expert 实现 (f95b049)
2. feat: Adjutant rule routing — 简单命令绕过 LLM (c82a6ab)
3. fix: WebSocket 重连鲁棒性 (231a9d1)
4. feat: Runtime 可观测性改进 (92b96ad)
5. feat: yaml-backed UnitRegistry (3ff52bc)
6. fix: ExpertSignal 阻塞信息对玩家可见 (27ca008)

## Phase 6: Round 7 全链验证 ✅ 完成 (2026-03-24 ~ 2026-03-26)

Hardening 后首次干净开局全链验证，共 20 commits。

**测试基础设施:**
- test: Live E2E runner 脚本 (aa903a8, 6b5ba34, d5f8824)
- test: query_response WS envelope 回归锁 (7e992b7)

**关键修复:**
- fix: rule-routed tasks monitor-only — 阻止 LLM 漂移 (c2defff)
- fix: 游戏重启后清除旧 runtime (770c2e6)
- fix: 无 MCV 时 deploy 立即短路 (076db78)
- fix: Recon 收口条件（超时→partial + waypoint 保持）(edf659d)
- fix: 诊断历史 sync replay (0dfab33)

**前端改进:**
- feat: task trace debug panel (6864c9b)
- feat: Expert jobs 显示在 task 卡片 (f4b3dfe)
- fix: 任务标签可读化 + 最新优先 (2a08783, c94fd23)
- fix: 全局清空操作统一 (cd6219b, 6330bc5)

**Round 7 结果:** 兵营 ✅ | 步兵 ✅ | 探索 ✅ | 查询 ✅ | 矿场 ✅ | 部署无MCV ✅

## Phase 7: Expert 知识集成 + 队列管理 ✅ 完成 (2026-03-27 ~ 2026-03-28)

将 OpenRA 游戏知识注入 Expert 决策，共 7 commits。

- feat: 经济知识集成 (f7a3d8b) — Expert 使用 UnitRegistry 成本/前置条件数据
- feat: 侦察 + 战斗知识集成 (4d35097)
- feat: 队列管理器 + 队列清理 (76aa5fd, 026ba25)
- feat: Task trace LLM context 暴露 (217541e)

## Phase 8: NLU 完整接入 + 稳定性 ✅ 完成 (2026-04-02)

用户直接指挥 Yu 完成旧 NLU 系统接入，共 19 commits。

**NLU 集成 (核心):**
- feat: 旧 NLU 前半段接入 Adjutant — shorthand + 复合序列 (1d64301)
- feat: 完整 runtime NLU 集成 — deploy_mcv/produce/explore/mine/stop_attack/query_actor (0925062)
- fix: rule-route 扩展 MCV 命令 (bd292b9)

**稳定性改进:**
- feat: 运行日志按 session/task 持久化 + 索引 (7802e22, 4c1db5c)
- feat: 仪表盘清除 runtime session state (6016daf)
- fix: 依赖缺失 fail fast (0a16a77, 2c8ccae)
- fix: world refresh 错误详情 + 节流 (9306e27, d9d2488)
- fix: 过时 deploy feedback 避免 (ffdab7f)
- fix: OpenCodeAlert player context 恢复 (a79b858)
- fix: produce 默认 auto-place (da43f32)

**测试:** 24 adjutant + 10 routing + 8 game_control + 9 ws 测试通过

## Phase 9: 文档整理 (2026-04-04, 进行中)

- docs/wang/ 从 40+ 文件精简到 15 个活跃文件 (46f5d1b)

---

## 统计摘要 (最近 100 commits, 2026-03-20 ~ 2026-04-04)

| 类型 | 数量 | 说明 |
|------|------|------|
| fix | 50 | bug 修复（建造链、LLM漂移、资源泄漏、前端等）|
| feat | 20 | 新功能（UnitRegistry、rule routing、NLU、观测性）|
| docs | 19 | 文档（测试报告、研究、进度追踪）|
| chore | 7 | 杂项（配置、gitignore、依赖）|
| test | 3 | 测试（live runner、WS envelope）|
| refactor | 1 | 重构（legacy cleanup）|

| 维度 | 数据 |
|------|------|
| 时间跨度 | 16 天 |
| 总变更 | 135 files, +309917 -6855 lines |
| 高峰日 | 03-23 (19 commits), 04-02 (19 commits) |
| 测试轮次 | Round 3 → 4 → 5 → 6 → 7 全部完成 |
| 已修复 bug | 50+ (建造链7层、LLM漂移、GameLoop阻塞、资源泄漏等) |
