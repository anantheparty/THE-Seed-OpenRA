# 实现任务列表

## 技术选型

| 组件 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 现有代码库 |
| LLM SDK | openai / anthropic Python SDK | 直接 SDK，无框架 |
| LLM 模型 | Qwen3.5（暂定，待实测） | 便宜快速 |
| 看板前端 | Vue 3 + Vite | 现代、轻量、组件化 |
| 前后端通信 | WebSocket | 实时推送 |
| 游戏通信 | GameAPI (Socket RPC, port 7445) | 保持不变 |
| 数据序列化 | Python dataclass + JSON | 简单直接 |
| 异步 | asyncio | Task Agent 并行需要 |

## 任务列表

### Phase 0: 清理 + 基础设施

| # | 任务 | 依赖 | 产出 | 规模 |
|---|---|---|---|---|
| 0.1 | 删除可删代码（standalone launchers/demos/旧 console.html） | 无 | 干净代码库 | 小 |
| 0.1b | 移除 the-seed 子库：迁出 NLU 规则资产到 `nlu_pipeline/rules/`，然后删除子库 | 0.1 | the-seed 移除 | 中 |
| 0.2 | 定义数据模型 dataclass — 所有模型带 timestamp 字段 | 无 | `models/` | 中 |
| 0.3 | 搭建项目新目录结构 | 0.1 | 目录骨架 | 小 |
| 0.4 | **LLM 模型抽象层**（统一接口，可一行换模型） | 无 | `llm/provider.py` | 中 |
| 0.5 | **Benchmark 框架**（每步耗时记录，可查询可导出） | 无 | `benchmark.py` | 中 |

### Phase 1: 核心运行时

| # | 任务 | 依赖 | 产出 | 规模 |
|---|---|---|---|---|
| 1.1 | WorldModel v1（统一查询接口，分层刷新，事件检测） | 0.2 | `world_model.py` | 大 |
| 1.2 | GameLoop（10Hz 主循环） | 1.1, 0.5 | `game_loop.py` | 中 |
| 1.3a | Kernel: Task 生命周期（create/destroy/status） | 0.2, 1.1, 0.5 | `kernel.py` | 中 |
| 1.3b | Kernel: 资源分配 + 抢占（ResourceNeed 匹配、优先级、自动补充） | 1.3a | `kernel.py` | 中 |
| 1.3c | Kernel: 事件路由（Event → 相关 Job/Task Agent，路由规则：actor_id 匹配 Job.resources，全局事件广播） | 1.3a | `kernel.py` | 中 |
| 1.3d | Kernel: pending question timeout + cancel | 1.3a | `kernel.py` | 小 |
| 1.3e | Kernel: 预注册自动响应规则（BASE_UNDER_ATTACK → auto Task） | 1.3a | `kernel.py` | 小 |
| 1.3f | 错误恢复策略（LLM 超时→用 default_if_timeout / GameAPI 断连→Job pause+重连 / WorldModel 刷新失败→用上次快照+告警） | 1.3a, 1.4 | 各组件 | 中 |
| 1.4 | Task Agent agentic loop（multi-turn tool use + event queue + review_interval + **context packet 带 timestamp**） | 0.2, **0.4**, 0.5 | `task_agent.py` | 中 |
| 1.5 | Task Agent tools 实现 | 1.3, 1.4 | `task_tools.py` | 中 |
| 1.6 | WebSocket 后端（server + handler + serializer） | 1.2 | `ws_server.py` | 中 |
| 1.7 | 全局 timestamp 传播（所有 payload + **LLM context packet** 带 timestamp） | 0.2, 1.4 | 各 model 改动 | 小 |
| 1.8 | review_interval 调度（GameLoop 检查 Task Agent wake 时机） | 1.2, 1.4 | 集成在 game_loop | 小 |

### Phase 2: 第一个 Expert

| # | 任务 | 依赖 | 产出 | 规模 |
|---|---|---|---|---|
| 2.1 | Expert 基类 + Job 基类 | 0.2, 0.5 | `expert_base.py` | 中 |
| 2.2 | ReconExpert + ReconJob | 2.1, 1.1 | `experts/recon.py` | 中 |
| 2.3 | 端到端测试 T1 + **benchmark 验证**（确认全链路耗时可查） | 1.*, 2.2, 0.5 | 测试+benchmark 报告 | 中 |

### Phase 3: 更多 Expert

**每个 Expert 实现前必须：(1) 调研真实 RTS AI (2) 写独立设计文档（FSM 状态、算法、边缘情况）(3) 审核通过后再写代码。**

| # | 任务 | 依赖 | 产出 | 规模 |
|---|---|---|---|---|
| 3.0a | 各 Expert 设计文档：ReconExpert（搜索策略/威胁规避/多探索点评分）、CombatExpert（FSM 状态/engagement 算法/包围/撤退）、EconomyExpert（建造序列/资源监控/队列管理）、MovementExpert（寻路/编队/到达检测）、DeployExpert（放置校验/位置评估） | 2.1 | `docs/expert_designs/` | 大 |
| 3.0b | 测试策略定义（mock GameAPI vs live game？单元测试框架选型） | 0.3 | `docs/test_strategy.md` | 小 |
| 3.0c | BT/FSM/ST 配置框架（数据驱动行为定义，Expert 共用） | 2.1, 3.0a | `expert_framework/` | 中 |
| 3.1 | EconomyExpert 调研+实现 | 3.0 | `experts/economy.py` + 配置 | 中 |
| 3.2 | MovementExpert 调研+实现 | 3.0 | `experts/movement.py` + 配置 | 中 |
| 3.3 | CombatExpert 调研+实现 | 3.0 | `experts/combat.py` + 配置 | 大 |
| 3.4 | DeployExpert 调研+实现 | 3.0 | `experts/deploy.py` + 配置 | 小 |
| 3.5 | 端到端测试 T2-T8 + benchmark | 3.1-3.4, 0.5 | 测试+benchmark 报告 | 大 |

### Phase 4: Adjutant 交互层

| # | 任务 | 依赖 | 产出 | 规模 |
|---|---|---|---|---|
| 4.1 | Adjutant LLM（分类/路由/pending question + **context 带 timestamp**） | 1.3, 1.4, **0.4** | `adjutant.py` | 中 |
| 4.2 | 查询 LLM（WorldModel → 自然语言回答，**通过 0.4 抽象层**） | 1.1, **0.4** | 集成在 adjutant | 小 |
| 4.3 | 主动通知系统（Kernel 事件规则 → player_notification） | 1.3 | 集成在 kernel | 小 |
| 4.4 | Adjutant 路由测试（回复路由/超时/迟到/多问题） | 4.1 | 测试通过 | 中 |
| 4.5 | 端到端测试 T9-T11 + benchmark | 4.1-4.4, 0.5 | 测试+benchmark 报告 | 中 |

### Phase 5: 看板

| # | 任务 | 依赖 | 产出 | 规模 |
|---|---|---|---|---|
| 5.1 | Vue 3 项目搭建 + WebSocket 连接 | 1.6 | `web-console-v2/` | 小 |
| 5.2 | **对话主界面**（正中间，Adjutant 聊天，所有消息带时效性标注） | 5.1 | Chat 组件（主视图） | 中 |
| 5.3 | Task 看板面板（侧栏，时效性标注） | 5.1 | Tasks 组件 | 中 |
| 5.4 | Operations 面板（侧栏，VNC + 服务控制） | 5.1 | Ops 组件 | 中 |
| 5.5 | Diagnostics 面板（日志流 + benchmark 可视化） | 5.1, 0.5 | Diag 组件 | 中 |
| 5.6 | 双模式切换（用户面板 / 调试面板） | 5.2-5.5 | 模式切换 | 小 |
| 5.7 | 语音 I/O 框架（ASR+TTS，**通过 0.4 抽象层**，可替换多模态模型） | 5.2, **0.4**, 4.1 | `voice/` | 中 |

### Phase 6: 结构化日志

| # | 任务 | 依赖 | 产出 | 规模 |
|---|---|---|---|---|
| 6.1 | 日志框架 + benchmark 集成 | 0.2, 0.5 | `logging_system.py` | 中 |
| 6.2 | 各组件接入日志+benchmark | 6.1, 1.* | 各文件改动 | 中 |
| 6.3 | 日志/benchmark 查询+导出工具 | 6.1 | `replay.py` + `benchmark_report.py` | 中 |

### Phase 7: 集成 + 优化

| # | 任务 | 依赖 | 产出 | 规模 |
|---|---|---|---|---|
| 7.1 | main.py 重写 | 1.*, 4.*, 5.* | `main.py` | 中 |
| 7.2 | LLM 模型实测（对比延迟+质量，**使用 benchmark 框架**） | 1.4, **0.4**, **0.5**, 6.3 | 选型报告 | 中 |
| 7.3 | 全量端到端测试 T1-T11 + 全链路 benchmark 报告 | 7.1, 0.5 | 测试+性能报告 | 大 |
| 7.4 | 性能优化（基于 benchmark 数据：prompt caching、context 压缩、tick 调优） | 7.3 | 优化记录 | 中 |

## 关键路径

```
0.1-0.5 (清理+模型+数据+benchmark)
    ↓
1.1 → 1.2+1.3 → 1.4+1.5 → 1.6-1.8
    ↓
2.1 → 2.2 → 2.3 ★ 里程碑1: 第一个端到端
    ↓
3.0 → 3.1-3.4 → 3.5 ★ 里程碑2: 五种 Expert
    ↓
4.1-4.3 → 4.4 → 4.5 ★ 里程碑3: 玩家交互完整
    ↓
5.1-5.7 + 6.1-6.3
    ↓
7.1 → 7.2 → 7.3 ★ 里程碑4: 全量测试+性能基线
    ↓
7.4 性能优化
```

**里程碑 1 (2.3)：** "探索地图找敌人基地"全流程 + benchmark 可查
**里程碑 2 (3.5)：** 五种 Expert (Recon/Economy/Movement/Combat/Deploy)
**里程碑 3 (4.5)：** 玩家交互完整 (Adjutant 路由 + T9-T11)
**里程碑 4 (7.3)：** 全量测试 T1-T11 + 全链路性能基线

## 分工建议

| 角色 | 负责 |
|---|---|
| wang | 架构审查、Kernel 设计、Adjutant 设计、测试审核、文档 |
| yu | Expert 实现、WorldModel、GameLoop、日志/benchmark |
| xi | 看板前端、Task Agent 实现、语音框架 |
| 共同 | 端到端测试、Expert 设计文档 |

## 跨切面约束

所有 Phase 必须遵守：
1. **模型抽象 (0.4)**：凡用 LLM 的地方必须通过抽象层（1.4, 4.1, 4.2, 5.7, 7.2）
2. **Benchmark (0.5)**：所有里程碑测试必须同时产出性能数据（2.3, 3.5, 4.5, 7.3）
3. **Timestamp**：所有对外 payload 和 LLM context 带 timestamp（1.7 负责传播，各组件遵守）
