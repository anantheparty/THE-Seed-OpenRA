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

按依赖关系排序。前置任务完成后才能开始后续。

### Phase 0: 清理 + 基础设施

| # | 任务 | 依赖 | 产出 | 预估规模 |
|---|---|---|---|---|
| 0.1 | 删除可删代码（standalone launchers/demos/旧 console.html） | 无 | 干净代码库 | 小 |
| 0.2 | 定义数据模型 dataclass（Task, Job, ResourceNeed, Constraint, Event, ExpertSignal, NormalizedActor, TaskMessage, PlayerResponse） | 无 | `models/` 包 | 中 |
| 0.3 | 搭建项目新目录结构 | 0.1 | 目录骨架 | 小 |

### Phase 1: 核心运行时

| # | 任务 | 依赖 | 产出 | 预估规模 |
|---|---|---|---|---|
| 1.1 | WorldModel v1（包装现有 IntelService/GameAPI，统一查询接口，分层刷新，事件检测） | 0.2 | `world_model.py` | 大 |
| 1.2 | GameLoop（10Hz 主循环：refresh → detect_events → tick jobs → check timeouts → push dashboard） | 1.1 | `game_loop.py` | 中 |
| 1.3 | Kernel v1（Task 生命周期、资源分配、事件路由、cancel、pending question timeout） | 0.2, 1.1 | `kernel.py` | 大 |
| 1.4 | Task Agent agentic loop（raw SDK multi-turn tool use + event queue + review_interval + context packet builder） | 0.2 | `task_agent.py` | 中 |
| 1.5 | Task Agent tools 实现（start_job, patch_job, abort_job, complete_task, create_constraint, remove_constraint, query_world, cancel_tasks, pause_job, resume_job） | 1.3, 1.4 | `task_tools.py` | 中 |

### Phase 2: 第一个 Expert

| # | 任务 | 依赖 | 产出 | 预估规模 |
|---|---|---|---|---|
| 2.1 | Expert 基类 + Job 基类（start/patch/pause/resume/abort + tick + Signal 发送） | 0.2 | `expert_base.py` | 中 |
| 2.2 | ReconExpert + ReconJob（侦察逻辑：评分选路线、避战、跟踪矿车方向、目标检测） | 2.1, 1.1 | `experts/recon.py` | 中 |
| 2.3 | 端到端测试 T1："探索地图找到敌人基地"全流程 | 1.*, 2.2 | 测试通过 | 中 |

### Phase 3: 更多 Expert

| # | 任务 | 依赖 | 产出 | 预估规模 |
|---|---|---|---|---|
| 3.1 | EconomyExpert + EconomyJob（生产队列、进度跟踪、断钱 waiting） | 2.1 | `experts/economy.py` | 中 |
| 3.2 | MovementExpert + MovementJob（移动、撤退、到达检测） | 2.1 | `experts/movement.py` | 中 |
| 3.3 | CombatExpert + CombatJob（进攻、防守、hold、surround 模式，内部 FSM） | 2.1 | `experts/combat.py` | 大 |
| 3.4 | DeployExpert + DeployJob（部署 MCV/建筑） | 2.1 | `experts/deploy.py` | 小 |
| 3.5 | 端到端测试 T2-T8 | 3.1-3.4 | 测试通过 | 大 |

### Phase 4: Adjutant 交互层

| # | 任务 | 依赖 | 产出 | 预估规模 |
|---|---|---|---|---|
| 4.1 | Adjutant LLM（输入分类：新命令/回复/查询 + 对话路由 + pending question 管理） | 1.3, 1.4 | `adjutant.py` | 中 |
| 4.2 | 查询 LLM（WorldModel 上下文 → 自然语言回答） | 1.1 | 集成在 adjutant | 小 |
| 4.3 | 主动通知系统（Kernel 事件规则 → player_notification） | 1.3 | 集成在 kernel | 小 |
| 4.4 | 端到端测试 T9-T11（并发、空闲、查询） | 4.1-4.3 | 测试通过 | 中 |

### Phase 5: 看板

| # | 任务 | 依赖 | 产出 | 预估规模 |
|---|---|---|---|---|
| 5.1 | Vue 3 项目搭建 + WebSocket 连接 | 无 | `web-console-v2/` | 小 |
| 5.2 | Task 看板面板（卡片列表、状态分列、时效性标注） | 5.1 | Tasks 组件 | 中 |
| 5.3 | 聊天/对话面板（Adjutant 消息、问题展示、回复输入） | 5.1 | Chat 组件 | 中 |
| 5.4 | Operations 面板（VNC + 服务控制 + 健康状态） | 5.1 | Ops 组件 | 中 |
| 5.5 | Diagnostics 面板（结构化日志流、调试模式） | 5.1 | Diag 组件 | 中 |
| 5.6 | 双模式切换（用户面板 / 调试面板） | 5.2-5.5 | 模式切换 | 小 |

### Phase 6: 结构化日志

| # | 任务 | 依赖 | 产出 | 预估规模 |
|---|---|---|---|---|
| 6.1 | 日志框架（分层：kernel/expert/action，结构化字段，timestamp） | 0.2 | `logging_system.py` | 中 |
| 6.2 | 各组件接入日志（Kernel、Task Agent、Job、Adjutant） | 6.1, 1.* | 各文件改动 | 中 |
| 6.3 | 日志回放工具（读取日志文件 → 按时间线重放） | 6.1 | `replay.py` | 中 |

### Phase 7: 集成 + 优化

| # | 任务 | 依赖 | 产出 | 预估规模 |
|---|---|---|---|---|
| 7.1 | main.py 重写（启动序列：GameAPI→WorldModel→Kernel→Adjutant→Dashboard→GameLoop） | 1.*, 4.*, 5.* | `main.py` | 中 |
| 7.2 | LLM 模型实测（Qwen3.5 / DeepSeek / Claude 对比延迟和质量） | 1.4 | 选型报告 | 中 |
| 7.3 | 全量端到端测试 T1-T11 | 7.1 | 测试报告 | 大 |
| 7.4 | 性能优化（prompt caching、context 压缩、tick 频率调优） | 7.3 | 优化记录 | 中 |

## 关键路径

```
0.1-0.3 → 1.1 → 1.2 + 1.3 → 1.4 + 1.5 → 2.1 → 2.2 → 2.3 (第一个端到端)
                                              ↓
                                         3.1-3.4 → 3.5
                                              ↓
                                         4.1-4.3 → 4.4
                                              ↓
                                         5.1-5.6 + 6.1-6.3
                                              ↓
                                           7.1 → 7.3
```

**第一个里程碑：2.3** — 能跑通"探索地图找到敌人基地"全流程。
**第二个里程碑：3.5** — 四种 Expert 都能工作。
**第三个里程碑：4.4** — 玩家交互完整。
**第四个里程碑：7.3** — 全量测试通过。

## 分工建议

| 角色 | 负责 |
|---|---|
| wang | 架构审查、Kernel 设计、Adjutant 设计、测试审核、文档 |
| yu | Expert 实现、WorldModel、GameLoop、看板、日志系统 |
| 共同 | Task Agent agentic loop、端到端测试 |
