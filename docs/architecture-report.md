# THE-Seed-OpenRA: Architecture Analysis & Next-Gen Agent Proposal

> 2026-03-01 | AI Copilot System Review (v2)

---

## Executive Summary

THE-Seed-OpenRA 的 AI 副官系统已经拥有可工作的基础设施：NLU 网关处理 60-80% 的安全指令（<300ms），DeepSeek 代码生成覆盖剩余情况。但经过实战测试，暴露了根本性的架构问题：**AI 惰性强、响应慢、无状态、缺乏连续性**。

新方案的核心理念：**不是替玩家打游戏的自主 AI，而是一个"会写工具的副官"**。Agent 接收玩家指令后持续监视到执行完毕，过程中自己编写和迭代工具来提高效率，维护持久记忆来理解上下文。底层只提供原子 API，所有智能行为都由 agent 自己构建。

---

## Part 1: Current System Analysis

### 1.1 Command Pipeline

```
用户输入 (WebSocket)
  │
  ▼
DashboardBridge._handle_client_message()          [main.py]
  │
  ├─ 离线检测: OpenRA 未运行 → 直接拒绝 (不调用 LLM)
  │
  ▼
Phase2NLUGateway.run()                             [agents/nlu_gateway.py]
  │
  ├─ 快速过滤: 空命令/超长/安全模式/blocked_regex → LLM fallback
  │
  ├─ Tier 1: PortableIntentModel.predict_one()     [sklearn, 9.4MB, <30ms]
  │   → intent + confidence
  │
  ├─ Tier 2: CommandRouter.route()                  [command_router.py, 1163行]
  │   → matched + intent + score + 生成代码
  │
  ├─ 安全门控: safe_intents 白名单 / attack 高门槛 / composite 门控
  │
  ├─ [NLU 路径] 直接执行路由器生成的代码            ~100-300ms ✓
  │
  └─ [LLM 路径] → SimpleExecutor.run()              ~2-7s ✗
        │
        ├─ _observe() → IntelService → 游戏状态文本    ~50-150ms
        ├─ CodeGenNode.generate()                      ~1500-7000ms (DeepSeek API)
        │   ├─ System Prompt: ~4500 tokens (API文档+规则+示例)
        │   └─ User Prompt: ~1500-2500 tokens (指令+状态+历史)
        ├─ _execute_code() → exec(code)                ~50-200ms
        └─ _record_history() → 保留最近5条
```

### 1.2 Component Map

| Component | File | Role |
|-----------|------|------|
| Entry & WebSocket | `main.py` | 入口、命令路由、服务启动 |
| NLU Gateway | `agents/nlu_gateway.py` | 意图分类 + 安全门控 |
| Command Router | `the-seed/.../command_router.py` | 规则匹配 + 模板代码生成 |
| Intent Model | `nlu_pipeline/artifacts/intent_model_runtime.json` | sklearn TF-IDF + LogReg |
| Gateway Config | `nlu_pipeline/configs/runtime_gateway.yaml` | 安全意图、门控阈值 |
| Executor | `the-seed/the_seed/core/executor.py` | LLM 代码生成 + 执行 |
| CodeGen | `the-seed/the_seed/core/codegen.py` | System prompt + DeepSeek 调用 |
| Model Adapter | `the-seed/the_seed/model/model_adapter.py` | OpenAI 兼容客户端 |
| Game State | `adapter/openra_env.py` | 游戏状态 → 文本摘要 |
| Intel Service | `openra_api/intel/service.py` | 情报缓存与分析 |
| MacroActions | `openra_api/macro_actions.py` | 高级 API 封装 |
| AttackJob | `openra_api/jobs/attack.py` | 持续攻击行为 |
| ExploreJob | `openra_api/jobs/explore.py` | 智能探索行为 |
| Enemy Agent | `agents/enemy_agent.py` | 自主对手 AI |
| Combat Agent | `agents/combat/combat_agent.py` | 战术微操（流式 LLM） |

### 1.3 Latency Breakdown

| Path | Steps | Total Latency |
|------|-------|--------------|
| NLU 路径 (60-80%) | sklearn(30ms) → router(15ms) → exec(200ms) | 65-245ms |
| LLM 路径 (20-40%) | state(150ms) → prompt(10ms) → DeepSeek(1.5-7s) → exec(200ms) | 1.6-7.4s |

### 1.4 Key Limitations

- **无 streaming**: 用户等待全部生成完毕
- **无缓存**: 相同指令每次都重新调用 API
- **无多轮对话**: 每次调用只有 `[system, user]` 两条消息
- **阻塞执行**: `produce_wait()` 冻结整个管线
- **历史极浅**: 只保留最近5条命令文本摘要

---

## Part 2: Problem Diagnosis

### 问题 1: Codegen-as-Copilot 是错误的抽象

LLM 被置于"程序员"角色，发现前置条件不满足时返回错误而不是修复。Prompt 中的"一条指令只做一件事""不要试图做太多"进一步强化了惰性。

正确做法：LLM 做决策，算法/工具做执行。决策和执行分离。

### 问题 2: 命令即 EOS — 无执行监视

Agent 发出命令后立即 EOS（End of Sequence）。"造3辆重坦"→ 下单 → agent 死了 → 造好/失败了没人知道 → 玩家下次说话才发现。

正确做法：agent 从接受命令到执行完毕持续存活，监视进度，出问题就修。

### 问题 3: 每次调用无状态

DeepSeek 每次只收到固定 system prompt + 当前瞬时状态 + 5条历史。没有战略记忆、目标追踪、失败记忆。

### 问题 4: 单模型瓶颈

"造步兵"（简单）和"分析局势"（复杂）走同一条管线，延迟相同。

### 问题 5: 无错误恢复

执行失败 → 返回 success=False → 用户手动重试。没有自动分析原因、修复前置条件、重试的能力。

---

## Part 3: Next-Gen Proposal — Tool-Writing Adjutant

### 3.1 Core Philosophy

**不是替玩家打游戏的自主 AI，而是一个足够听话、足够聪明的副官。**

- 玩家发令 → agent 精准执行，持续监视到完成
- 玩家没要求的事 → 可以建议，不擅自行动
- 第一次遇到新指令 → agent 用 LLM 理解并写工具
- 下次同样指令 → 直接调用已有工具，毫秒级
- 工具效果不好 → agent 自己迭代改进
- 所有知识和经验持久化到记忆中

### 3.2 Architecture

```
┌─────────────────────────────────────────────────┐
│              Event Loop (lightweight)            │
│                                                  │
│  • Player command queue                          │
│  • Game state diff detector                      │
│  • Agent-registered watches                      │
│  • Heartbeat timer                               │
│  • Command completion monitor                    │
│                                                  │
├──────────────── wake-up trigger ─────────────────┤
│                                                  │
│              Agent (LLM kernel)                  │
│                                                  │
│  • Parse command → find/create tool → execute    │
│  • Monitor execution until complete              │
│  • Handle errors → fix → retry                   │
│  • Register watches for async events             │
│  • Update memory with results                    │
│  • Suggest actions to player (not auto-execute)  │
│                                                  │
├─────────────────────────────────────────────────┤
│              Tools Folder (persistent)           │
│                                                  │
│  tools/                                          │
│  ├── wrappers/     # 命令执行工具 (attack, produce...)  │
│  ├── watches/      # 事件监控脚本 (power, threat...)    │
│  ├── state/        # 状态解析器 (自迭代的 state parser) │
│  └── knowledge/    # 游戏知识库 + agent 记忆            │
│                                                  │
├─────────────────────────────────────────────────┤
│              Primitive API (固定)                │
│                                                  │
│  move_units / attack_target / produce_wait       │
│  query_actor / player_base_info / ...            │
│  (只提供原子操作，不包含业务逻辑)                    │
│                                                  │
└─────────────────────────────────────────────────┘
```

### 3.3 Command Lifecycle — No Premature EOS

这是与当前系统最大的区别。Agent 在收到玩家命令后，不是"生成一段代码 → 执行 → EOS"，而是**持续监视直到指令完整结束**：

```
Player: "造3辆重坦"
  │
  Agent wakes up
  │
  ├─ 1. 理解意图 → 找到/写好 produce wrapper
  ├─ 2. 检查前置 → 电力不足？先造电厂
  ├─ 3. 下单生产 → produce("重型坦克", 3)
  ├─ 4. 注册 watch → monitor_production("重型坦克", 3)
  ├─ 5. 向玩家反馈 → "开始生产3辆重坦，预计X秒"
  │
  │  ... agent 不 EOS，等待 watch 触发 ...
  │
  ├─ 6. watch: 第1辆完成 → 反馈 "1/3 完成"
  ├─ 7. watch: 断电了 → 自动造电厂 → 反馈 "发现断电，已补电厂"
  ├─ 8. watch: 全部完成 → 反馈 "3辆重坦已就绪，要派去哪里？"
  │
  Agent EOS (命令完整执行完毕)
```

实现方式：agent 的一次"存活"不是一个 LLM API call，而是一个 **command session**。session 内 agent 可以多次调用 LLM、执行代码、等待事件。外层 event loop 负责在 watch 触发时重新唤醒 agent 并传入上下文。

### 3.4 Tool Self-Authoring

Agent 自己写工具存到 tools 文件夹，下次直接用：

**Wrappers** — 命令执行工具：
- 第一次"全军出击" → agent 写 `tools/wrappers/full_attack.py`
- 第二次 → 直接调用，跳过 LLM

**Watches** — 事件监控：
- Agent 注册 "当电力<0时通知我"
- 外层 event loop 每 tick 运行 watch 脚本，命中则唤醒 agent
- Agent 自己决定添加/修改/删除哪些 watch

**State parsers** — 状态理解：
- 当前 `openra_env.py` 是基础版状态格式
- Agent 可以写自己的 state summarizer，更好地理解局势
- 迭代改进：觉得信息不够就加字段，太冗余就精简

**Knowledge base** — 游戏知识 + 操作记忆：
- 游戏机制：建造树、单位克制、经济节奏
- 操作记忆：玩家习惯、常用命令、历史执行结果
- 失败教训：上次进攻因为什么失败

### 3.5 Event-Driven Wake-up

Agent 不需要持续运行。轻量的 event loop 负责检测状态变化，按条件唤醒 agent：

| 触发源 | 优先级 | 说明 |
|--------|--------|------|
| 玩家命令 | 最高 | 立即唤醒，开始 command session |
| Agent 注册的 watch | 高 | 生产完成、断电、被攻击 |
| Command session 内的轮询 | 中 | 等待执行结果 |
| 心跳 tick | 低 | 每30-60秒的常规检查 |

Watch 脚本由 agent 自己编写，event loop 只负责定期执行并检测返回值。这意味着 agent 可以自定义自己关心什么。

### 3.6 NLU Layer — 快速路由保留

现有 NLU 路由仍然有价值，作为 agent 的"快车道"：

- 60-80% 的标准命令仍走 NLU → router → 模板代码 → 直接执行
- 但执行后不 EOS，而是进入 agent 的 command session 监视
- NLU 无法匹配的命令才走 agent LLM 路径
- 随着 agent 积累更多自写工具，LLM 调用比例持续降低

### 3.7 Agent Kernel

初期使用 Claude Code 作为内核：
- 天然支持工具调用、文件读写、代码生成
- 持久记忆（MEMORY.md 模式）
- 后续可替换为其他 LLM 内核，接口不变

关键接口：
- 输入：玩家命令 + 游戏状态 + 工具清单 + 记忆
- 输出：工具调用 / 新工具代码 / watch 注册 / 玩家反馈
- 存活：command session（非单次 API call）

---

## Part 4: Acceleration Strategy

### 原则：工具缓存优先，LLM 兜底

Agent 自写工具本身就是最好的加速——写过的工具不需要 LLM 就能直接执行。随着使用时间增长，越来越多命令被工具覆盖。

### 延迟分层

| 路径 | 延迟 | 场景 |
|------|------|------|
| 已有工具直接执行 | <100ms | agent 写过的 wrapper |
| NLU + 模板路由 | 100-300ms | 标准格式命令 |
| Agent LLM 理解 + 写工具 | 1-5s | 首次遇到的新指令 |
| 复杂决策（战略建议） | 5-15s | 需要深度分析局势 |

### 其他加速手段

- **语义缓存**：相似命令复用已有工具（嵌入相似度匹配）
- **Streaming**：LLM 生成过程实时反馈给玩家
- **非阻塞执行**：生产类操作立即返回 ack，后台监视
- **多模型分层**：简单命令用小模型，复杂决策用大模型

---

## Part 5: Migration & Roadmap

### 需要迁移的内容

| 现有组件 | 迁移方向 |
|----------|---------|
| `openra_api/jobs/attack.py` | → `agent/tools/wrappers/attack.py`（由 agent 管理） |
| `openra_api/jobs/explore.py` | → `agent/tools/wrappers/explore.py` |
| `agents/economy/engine.py` | → `agent/tools/wrappers/economy.py` |
| `openra_api/macro_actions.py` | 保留为 Primitive API |
| `openra_api/game_api.py` | 保留为 Primitive API |
| `openra_api/intel/service.py` | 保留，agent 通过 API 调用 |
| `adapter/openra_env.py` | 保留基础版，agent 可写增强版 |

### 阶段

**Phase 1: 搭框架**
- 建立 agent 文件夹结构（tools/, watches/, state/, knowledge/）
- 实现 event loop + watch 机制
- 实现 command session 生命周期（不提前 EOS）
- Claude Code 作为初始 LLM 内核接入
- 现有 NLU 路由作为快车道保留

**Phase 2: 迁移工具**
- 将现有 job 系统迁移为 agent 可管理的工具
- Agent 获得读写 tools 文件夹的能力
- 实现基础 watch（生产完成、断电、被攻击）
- 玩家反馈闭环（执行进度、完成通知）

**Phase 3: 自迭代**
- Agent 根据执行效果自动改进工具
- 积累游戏知识库
- 实现 state parser 自迭代
- 语义缓存 + streaming 加速

**Phase 4: 打磨**
- 多模型分层（简单/复杂命令分流）
- Dashboard 实时展示 agent 状态和工具列表
- 多轮对话支持（"造坦克" → "几辆？" → "3辆"）
- 内核可替换（Claude → 其他 LLM）

---

## Part 6: References

| Project | Key Insight | Relevance |
|---------|-------------|-----------|
| **SwarmBrain** (StarCraft II) | 双层：LLM 宏观策略 + 状态机微操。不把 LLM 放实时关键路径 | 工具缓存 vs LLM 兜底的思路一致 |
| **Vox Deorum** (Civ V) | LLM 输出方针不输出操作。决策和执行解耦 | Agent 做决策，工具做执行 |
| **TextStarCraft II** | Chain of Summarization 压缩状态。Token 减 60%+ | State parser 自迭代的参考 |
| **Claude Code** | Agent 拿原子工具，按需写代码/工具，迭代改进，维护记忆 | 整体架构的直接参考 |

### 现有可复用组件

| Component | Reuse |
|-----------|-------|
| `EnemyAgent` 自主循环 | Event loop + tick 模式参考 |
| `CombatAgent` 流式 LLM | Streaming 实现参考 |
| `IntelService` 情报缓存 | Agent 的状态数据源 |
| `JobManager` + `AttackJob` + `ExploreJob` | 迁移为 agent 工具 |
| `MacroActions` + `GameAPI` | 保留为 Primitive API |
| NLU Gateway + CommandRouter | 保留为快速路由层 |

---

## Appendix: Current vs Proposed

| Dimension | Current | Proposed |
|-----------|---------|----------|
| 定位 | LLM 代码生成器 | 会写工具的副官 |
| 命令生命周期 | 生成代码 → 执行 → EOS | 接受命令 → 监视 → 完成 → EOS |
| 延迟 (常见命令) | 100ms-7s | <100ms (已有工具) |
| 延迟 (新命令) | 2-7s | 1-5s (写工具，下次秒回) |
| 主动性 | 无 | 可建议，不擅自行动 |
| 状态 | 每次无状态 | 持久记忆 + 知识库 |
| 错误处理 | 报错给用户 | 自动修复 + 重试 |
| 工具 | 手写 job + 模板 | agent 自写 + 自迭代 |
| 反馈 | "执行成功/失败" | 实时进度 + 完成通知 + 建议 |
