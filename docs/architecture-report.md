# THE-Seed-OpenRA: Architecture Analysis & Next-Gen Agent Proposal

> 2026-02-26 | AI Copilot System Review

---

## Executive Summary

THE-Seed-OpenRA 的 AI 副官系统已经拥有可工作的基础设施：NLU 网关处理 60-80% 的安全指令（<300ms），DeepSeek 代码生成覆盖剩余情况。但经过实战测试，暴露了根本性的架构问题：**AI 惰性强、响应慢、无主动性、缺乏战略意识**。本文分析现有系统，并提出基于分层 Agent 架构的下一代方案。

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

| Component | File | Lines | Role |
|-----------|------|-------|------|
| Entry & WebSocket | `main.py` | 1328 | 入口、命令路由、服务启动 |
| NLU Gateway | `agents/nlu_gateway.py` | 909 | 意图分类 + 安全门控 |
| Command Router | `the-seed/.../command_router.py` | 1163 | 规则匹配 + 模板代码生成 |
| Intent Model | `nlu_pipeline/artifacts/intent_model_runtime.json` | 9.4MB | sklearn TF-IDF + LogReg |
| Gateway Config | `nlu_pipeline/configs/runtime_gateway.yaml` | 100 | 安全意图、门控阈值 |
| Executor | `the-seed/the_seed/core/executor.py` | 232 | LLM 代码生成 + 执行 |
| CodeGen | `the-seed/the_seed/core/codegen.py` | 320 | System prompt + DeepSeek 调用 |
| Model Adapter | `the-seed/the_seed/model/model_adapter.py` | 109 | OpenAI 兼容客户端 |
| Game State | `adapter/openra_env.py` | 177 | 游戏状态 → 文本摘要 |
| Intel Service | `openra_api/intel/service.py` | ~1000 | 情报缓存与分析 |
| MacroActions | `openra_api/macro_actions.py` | 505 | 高级 API 封装 |
| AttackJob | `openra_api/jobs/attack.py` | 191 | 持续攻击行为 |
| ExploreJob | `openra_api/jobs/explore.py` | 480 | 智能探索行为 |
| Enemy Agent | `agents/enemy_agent.py` | 619 | 自主对手 AI |
| Combat Agent | `agents/combat/combat_agent.py` | ~600 | 战术微操（流式 LLM） |
| Strategy Agent | `agents/strategy/strategic_agent.py` | ~400 | 战略指挥官 |
| Economy Engine | `agents/economy/engine.py` | ~200 | 算法式经济规划 |

### 1.3 Latency Breakdown

#### NLU 路径（60-80% 的指令）

| Step | Latency | % |
|------|---------|---|
| Intent classification (sklearn) | 10-30ms | 15% |
| Router matching (regex + FlashText) | 5-15ms | 8% |
| Code execution (API call) | 50-200ms | 77% |
| **Total** | **65-245ms** | |

#### LLM 路径（20-40% 的指令）

| Step | Latency | % |
|------|---------|---|
| Game state query (IntelService, cached 0.25s) | 50-150ms | 3% |
| Prompt construction | 5-10ms | <1% |
| **DeepSeek API call** | **1500-7000ms** | **85%** |
| Code execution | 50-200ms | 5% |
| **Total** | **1605-7360ms** | |

### 1.4 NLU Coverage

**安全意图 (safe_intents):**

| Intent | Min Confidence | Coverage |
|--------|---------------|----------|
| `deploy_mcv` | 0.70 | 展开基地车 |
| `produce` | 0.74 | 建造/生产/训练 |
| `explore` | 0.70 | 侦察/探索 |
| `mine` | 0.70 | 采矿 |
| `query_actor` | 0.72 | 查询状态 |
| `stop_attack` | 0.68 | 停火/撤退 |

**高风险意图 (需要严格门控):**

| Intent | Min Confidence | Additional Gates |
|--------|---------------|-----------------|
| `attack` | 0.93 | 需显式攻击动词 + 目标实体 |
| `composite_sequence` | 0.90 | 需连接词 + 步骤意图白名单 |

**LLM Fallback 触发条件:**
- 意图不在安全列表
- 置信度低于阈值
- 路由器匹配失败
- 路由器代码执行失败（有二次 fallback）
- 命令过长 (>80字符)

### 1.5 DeepSeek 调用详情

**每次调用的上下文量:**
- System Prompt: ~4500 tokens（API 文档、规则、5个完整示例）
- User Prompt: ~1500-2500 tokens（指令 + 游戏状态 + API 签名 + 历史）
- **Total: ~6000-7000 tokens input**
- Output: ~100-300 tokens（Python 代码片段）

**关键限制:**
- **无 streaming**: 用户等待全部生成完毕，0 中间反馈
- **无缓存**: 相同指令每次都重新调用 API
- **无多轮对话**: 每次调用只有 `[system, user]` 两条消息
- **阻塞执行**: `produce_wait()` 等阻塞操作冻结整个管线
- **无并行**: 一个命令处理完才能处理下一个
- **历史极浅**: 只保留最近5条命令的文本摘要

---

## Part 2: Problem Diagnosis

### 问题 1: Codegen-as-Copilot 是错误的抽象

**现象**: LLM 生成的代码经常"只报不做"。

```python
# 用户说"造坦克"，LLM 检测到断电后：
if info.Power < 0:
    __result__ = {"success": False, "message": "断电中，先建造电厂恢复电力"}
    # ← 既没建电厂，也没造坦克
```

**根因**: 让 LLM 生成 Python 代码把它置于"程序员"角色——程序员发现前置条件不满足时，自然倾向于返回错误而不是"越权"修复。Prompt 中的规则"一条指令只做一件事"和"不要试图做太多"进一步强化了这种惰性。

**正确的抽象应该是**: LLM 做决策（"需要先建电厂再造坦克"），算法做执行。决策和执行分离。

### 问题 2: 无主动性

**现象**: AI 永远等用户下达命令，从不主动行动。断电了不会自己建电厂，矿车闲置不会自己采矿，被攻击不会自动反击。

**根因**: 系统设计为"用户说 → AI 做"的 request-response 模式。没有后台自主决策循环。

**对比**: 现有的 `EnemyAgent` 已经实现了45秒一次的自主策略循环——它每45秒观察局势、用 LLM 决定下一步、然后执行。人类玩家的 copilot 完全没有这个能力。

### 问题 3: 每次调用无状态

**现象**: AI 看不到大局。它不知道自己5分钟前刚建过兵营，不知道刚才的攻击失败了因为兵力不足。

**根因**: DeepSeek 每次调用只收到：
- 固定的 system prompt（API 文档）
- 当前瞬时游戏状态
- 最近5条命令的文本摘要（极简）

没有：
- 战略记忆（"我们的战略是快攻"）
- 目标追踪（"正在积累10辆重坦准备进攻"）
- 失败记忆（"上次进攻因为防空被打回来了"）

### 问题 4: 单模型瓶颈

**现象**: "造步兵"（简单）和"分析局势制定战略"（复杂）走同一条管线，用同一个模型，延迟相同。

**当前分布:**
```
T0 (regex/NLU):    60-80% 命令    <300ms     ← 已实现
T1 (DeepSeek):     20-40% 命令    2-7s       ← 全部 fallback 走这里
```

**理想分布:**
```
T0 (regex/NLU):    60% 命令       <100ms     ← 已有
T1 (语义缓存):     15% 命令       <50ms      ← 缺失
T2 (小模型):       15% 命令       200-500ms  ← 缺失
T3 (大模型):       8% 命令        1-3s       ← 当前 DeepSeek
T4 (顶级模型):     2% 命令        3-8s       ← 战略级决策
```

### 问题 5: 无错误恢复

**现象**: NLU 路由代码执行失败 → fallback 到 LLM → LLM 也可能失败 → 用户看到错误信息，只能重试。

**当前流程:**
```
NLU 代码执行失败
  └→ route_failure_fallback enabled → LLM 重试 (2-7s)
      └→ LLM 也失败 → 返回 success=False
          └→ 用户看到错误，手动重试
```

**理想流程:**
```
NLU 代码执行失败
  └→ 分析失败原因（断电？缺前置？没钱？）
      └→ 自动修复前置条件
          └→ 重试原始操作
              └→ 返回修复+执行的结果
```

---

## Part 3: Next-Gen Proposal — Hierarchical Agent Architecture

### 3.1 Architecture Overview

```
┌───────────────────────────────────────────────────────────────────┐
│                     Agent Orchestrator                            │
│                   (Behavior Tree / FSM)                           │
├───────────┬───────────┬──────────────┬───────────────────────────┤
│  Layer 0  │  Layer 1  │   Layer 2    │         Layer 3           │
│ Reactive  │  Command  │  Tactical    │       Strategic           │
│ <100ms    │  <500ms   │  200ms-1s    │       30-60s cycle        │
│           │           │              │                           │
│ 算法驱动   │ NLU+缓存   │ 小模型       │      大模型               │
│ Job系统    │ 路由器     │ 本地推理      │    DeepSeek/Claude       │
│ 自动修复   │ 语义缓存   │ 新指令处理    │    战略规划               │
│ 反射响应   │ 模板展开   │ 并行竞速      │    目标管理               │
└───────────┴───────────┴──────────────┴───────────────────────────┘
         ▲                                         │
         │              Directives                  │
         └──────────────────────────────────────────┘
```

### 3.2 Layer 0 — Reactive Layer (算法驱动, <100ms)

**已有基础:**
- `AttackJob`: 持续攻击，自动寻敌、分配目标
- `ExploreJob`: 智能探索，射线采样 + 粘性目标
- `EconomyEngine`: 建造序列规划、电力管理、单位生产

**需要新增:**
- **AutoPowerManager**: 电力 < 0 时自动插入电厂生产（算法，无 LLM）
- **AutoHarvester**: 矿车闲置自动采矿
- **ThreatResponder**: 基地被攻击时自动调集防御力量
- **ProductionQueue**: 按照 Layer 3 的指令持续生产，不需要用户每次下令

**实现方式:**
```python
class ReactiveLayer:
    """每100ms tick一次，处理紧急事务"""

    def tick(self, intel: IntelModel):
        # 电力修复（优先级最高）
        if intel.power.surplus < 0 and not self.building_power:
            self.queue_produce("电厂", priority="urgent")

        # 矿车管理
        idle_harvesters = [h for h in intel.harvesters if h.activity == "idle"]
        for h in idle_harvesters:
            api.harvester_mine([h])

        # 威胁响应
        if intel.threats_near_base:
            nearby_units = self.get_defenders()
            for unit in nearby_units:
                jobs.assign_actor_to_job(unit, "attack")
```

### 3.3 Layer 1 — Command Router (NLU + 语义缓存, <500ms)

**保留现有:**
- `Phase2NLUGateway` (intent classification + safety gates)
- `CommandRouter` (regex matching + template code generation)

**新增: 语义缓存 (Semantic Cache)**

```python
class SemanticCache:
    """基于句向量的命令缓存"""

    def __init__(self):
        # all-MiniLM-L6-v2: 22MB, <10ms on CPU
        self.embedder = SentenceTransformer('all-MiniLM-L6-v2')
        self.cache: List[CacheEntry] = []  # (embedding, command, code, game_phase)

    def lookup(self, command: str, game_state: GameState) -> Optional[str]:
        emb = self.embedder.encode(command)
        for entry in self.cache:
            sim = cosine_similarity(emb, entry.embedding)
            if sim > 0.92 and entry.game_phase == game_state.phase:
                # 参数替换: "造3个重坦" cache hit "造5个重坦" → 替换数量
                code = self.adapt_parameters(entry.code, command)
                return code
        return None

    def store(self, command: str, code: str, game_state: GameState):
        emb = self.embedder.encode(command)
        self.cache.append(CacheEntry(emb, command, code, game_state.phase))
```

**预期效果:**
- 生产类命令: "造X个Y" 高度重复，缓存命中率 >80%
- 查询类命令: "看看状态" "查下兵力" 几乎必中
- 探索/采矿: 模式固定，近100%命中
- 预估总覆盖: **NLU (65%) + Cache (20%) = 85% 命令 <500ms**

### 3.4 Layer 2 — Tactical Agent (小模型, 200ms-1s)

**角色**: 处理 Layer 1 无法匹配的新颖命令。

**方案 A: 本地小模型**
- Qwen2.5-3B 或 Phi-3-mini (3.8B)
- llama.cpp 部署，CPU 推理 200-500ms
- 精简 prompt（只有 API 签名 + 指令，无完整文档）

**方案 B: 快速 API 模型**
- DeepSeek V3 with streaming + 更短的 prompt
- 输出第一个 token 后立即开始流式显示

**方案 C: 并行竞速**
```python
async def handle_command(command):
    # 同时发起小模型和大模型请求
    small_task = asyncio.create_task(small_model.generate(command))
    large_task = asyncio.create_task(large_model.generate(command))

    # 谁先返回有效结果就用谁
    done, pending = await asyncio.wait(
        [small_task, large_task],
        return_when=asyncio.FIRST_COMPLETED
    )

    result = done.pop().result()
    for task in pending:
        task.cancel()
    return result
```

### 3.5 Layer 3 — Strategic Agent (大模型, 30-60s cycle)

**角色**: 自主运行的战略大脑，不等用户指令。

**设计参考**: 现有 `EnemyAgent` 的自主循环 + `StrategicAgent` 的指挥模式。

```python
class StrategicAgent:
    """每30-60秒运行一次，制定/更新整体战略"""

    def tick(self):
        # 1. 收集完整情报
        intel = intel_service.get_intel(force=True)
        memory = self.strategic_memory  # 持久化的战略记忆

        # 2. LLM 战略决策（非代码，而是结构化指令）
        directives = self.llm_decide(intel, memory)
        # 输出示例:
        # {
        #   "phase": "mid_game",
        #   "strategy": "双矿快攻",
        #   "economy": {"target_refineries": 2, "target_factories": 2},
        #   "military": {"composition": {"重坦": 0.6, "防空车": 0.2, "步兵": 0.2}},
        #   "priority": "build_army",
        #   "attack_when": "army_value > 2000",
        #   "thoughts": "敌方防空弱，可以考虑出飞机"
        # }

        # 3. 更新 Layer 0 的行为参数
        reactive_layer.set_production_targets(directives["military"])
        reactive_layer.set_economy_targets(directives["economy"])

        # 4. 更新战略记忆
        memory.update(directives, intel)
```

**战略记忆 (Strategic Memory):**

```python
class StrategicMemory:
    """跨多次决策持久化的战略信息"""

    game_phase: str              # opening / early / mid / late
    current_strategy: str        # "快攻" / "经济发展" / "防守反击"
    goals: List[str]             # ["积累10辆重坦", "控制中路矿区"]
    completed_goals: List[str]
    failed_attempts: List[str]   # ["第一次进攻因防空失败"]
    enemy_profile: Dict          # 对手的风格和弱点
    key_events: List[str]        # 重要事件时间线
```

### 3.6 Agent Protocol & Orchestration

**统一 Agent 接口:**

```python
class BaseAgent(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def tick(self, ctx: TickContext) -> None: ...

    @abstractmethod
    def status(self) -> Dict[str, Any]: ...

    @abstractmethod
    def reset(self) -> None: ...
```

**Agent Manager:**

```python
class AgentManager:
    """中央管理器：生命周期、通信、资源分配"""

    agents: Dict[str, BaseAgent]

    def register(self, name: str, agent: BaseAgent): ...
    def start_all(self): ...
    def stop_all(self): ...
    def tick_all(self): ...  # 按优先级调度

    # 单位所有权管理
    def assign_units(self, agent: str, actor_ids: List[int]): ...
    def release_units(self, agent: str, actor_ids: List[int]): ...
```

---

## Part 4: Acceleration Techniques

### 4.1 Semantic Cache

**技术选型:**
- Embedding model: `all-MiniLM-L6-v2` (22MB, <10ms CPU)
- 相似度阈值: cosine > 0.92
- 参数自适应: 提取数量/单位名替换

**预期效果:**
- 命中率: ~67%（基于生产环境研究数据）
- 命中延迟: <20ms
- 特别适合: 重复性高的 RTS 指令

### 4.2 Speculative Pre-computation

**思路**: 在用户操作间隙，根据当前局势预测下一步可能的指令，提前生成代码。

```python
class SpeculativeEngine:
    def on_idle(self, intel: IntelModel):
        # 开局刚展开基地车 → 预计算 "建电厂" "建兵营" "建矿场"
        if intel.stage == "opening" and intel.has_construction_yard:
            for cmd in ["建造电厂", "建造兵营", "建造矿场"]:
                self.precompute(cmd, intel)

        # 刚建完矿场 → 预计算 "建车间" "造矿车"
        if intel.recent_event == "refinery_built":
            self.precompute("建造车间", intel)
```

### 4.3 Multi-Model Tiering

| Tier | Technology | Latency | Hit Rate | Cumulative |
|------|-----------|---------|----------|------------|
| T0 | Regex + NLU (existing) | <100ms | 60% | 60% |
| T1 | Semantic Cache (new) | <50ms | 15% | 75% |
| T1.5 | Template Expansion (existing router) | <100ms | 10% | 85% |
| T2 | Small Local Model (new) | 200-500ms | 10% | 95% |
| T3 | DeepSeek API (existing) | 1-3s | 4% | 99% |
| T4 | Claude/GPT-4o (new, rare) | 3-8s | 1% | 100% |

**95% 的命令在 500ms 内响应。**

### 4.4 Streaming Response

**现状**: `model_adapter.py` 使用 `client.chat.completions.create()` 同步调用，用户等待全部生成。

**改进**: 使用 `stream=True`，逐 token 返回：
- 生成开始即发送 "AI 正在执行..." 状态
- 代码生成中可以流式显示思考过程
- 代码完成后立即执行，不等额外 token

### 4.5 Non-blocking Execution

**现状**: `produce_wait("重坦", 5)` 阻塞 5 分钟。

**改进**: 分离"下达命令"和"等待完成"：
```python
# 非阻塞：立即返回，后台等待
task_id = api.produce("重坦", 5, auto_place_building=True)
__result__ = {"success": True, "message": "已下达生产5辆重坦的命令"}

# Layer 0 在后台轮询完成状态
```

---

## Part 5: Implementation Roadmap

### Phase 1: Quick Wins (1-2 weeks)

**目标: 85% 命令 <500ms**

| Task | Impact | Effort |
|------|--------|--------|
| 修复 codegen prompt（禁止报错不做） | 消除 AI 惰性 | 0.5天 |
| 扩展 CommandRouter 覆盖范围 | 减少 LLM fallback | 1天 |
| 实现 SemanticCache | 15% 命令秒回 | 2-3天 |
| DeepSeek 调用增加 streaming | 感知延迟降低50% | 1天 |
| 非阻塞 produce（立即返回 ack） | 长生产不冻结 | 1天 |

### Phase 2: Proactive Gameplay (1-2 weeks)

**目标: AI 能自主管理基本事务**

| Task | Impact | Effort |
|------|--------|--------|
| AutoPowerManager（算法） | 自动修复断电 | 1天 |
| AutoHarvester（算法） | 矿车不再闲置 | 0.5天 |
| ThreatResponder（算法） | 被攻击自动防御 | 1-2天 |
| 集成 EconomyEngine 到人类端 | 基础经济自动化 | 2-3天 |
| 错误自动恢复循环 | 失败自动重试 | 1-2天 |

### Phase 3: Full Autonomy (2-3 weeks)

**目标: 战略级自主 AI**

| Task | Impact | Effort |
|------|--------|--------|
| Layer 3 StrategicAgent（30s 循环） | 战略意识 | 3-5天 |
| StrategicMemory | 跨对局学习 | 2-3天 |
| 小模型部署 (Qwen2.5-3B) | Layer 2 加速 | 2-3天 |
| 并行竞速（小模型 vs 大模型） | 降低尾延迟 | 1-2天 |
| Speculative Pre-computation | 常见操作秒回 | 2天 |

### Phase 4: Polish (2-3 weeks)

**目标: 完善体验**

| Task | Impact | Effort |
|------|--------|--------|
| Behavior Tree 统一编排 | 清晰的决策层次 | 3-5天 |
| Agent Manager + Protocol | 统一生命周期 | 2-3天 |
| Dashboard 实时状态展示 | 可观测性 | 2-3天 |
| 多轮对话支持 | "造坦克""几辆？""3辆" | 2-3天 |

---

## Part 6: References & Prior Art

### 6.1 SwarmBrain (StarCraft II)

**架构**: 双层系统
- **Overmind Intelligence Matrix**: LLM 宏观策略（建造顺序、进攻时机、资源分配）
- **Swarm ReflexNet**: 状态机微操（无 LLM 延迟）

**核心教训**: **永远不要把 LLM 调用放在实时战斗的关键路径上**。用状态机和预计算处理时间敏感的操作。

> Source: https://github.com/ramsayxiaoshao/SwarmBrain

### 6.2 Vox Deorum (Civilization V)

**架构**: LLM 负责大战略 + 算法负责执行
- 2,327 次验证的游戏模拟
- LLM 通过 RESTful API 与游戏通信（类似我们的 GameAPI）

**核心教训**: 战略决策和战术执行必须解耦。LLM 输出的是"方针"而不是"操作"。

> Source: https://github.com/CIVITAS-John/vox-deorum

### 6.3 TextStarCraft II

**关键技术**: Chain of Summarization
- 将复杂游戏状态压缩为结构化文本摘要
- Token 减少 60%+，同时提升决策质量

**核心教训**: 游戏状态的文本化格式极其重要。当前 `openra_env.py` 的格式可以进一步优化。

> Source: https://github.com/histmeisah/Large-Language-Models-play-StarCraftII

### 6.4 Existing Codebase Assets

**可复用的组件:**

| Component | Where | Reuse |
|-----------|-------|-------|
| `EnemyAgent` 自主循环 | `agents/enemy_agent.py` | Layer 3 参考架构 |
| `CombatAgent` 流式 LLM | `agents/combat/combat_agent.py` | Streaming 实现参考 |
| `EconomyEngine` 算法规划 | `agents/economy/engine.py` | Layer 0 经济管理 |
| `StrategicAgent` 指挥框架 | `agents/strategy/strategic_agent.py` | Layer 3 指挥接口 |
| `IntelService` 情报缓存 | `openra_api/intel/service.py` | 所有层共享 |
| `JobManager` 行为管理 | `openra_api/jobs/base.py` | Layer 0 持续行为 |

---

## Appendix A: Current vs Proposed Comparison

| Dimension | Current | Proposed |
|-----------|---------|----------|
| Command latency (common) | 100-300ms (NLU) / 2-7s (LLM) | <500ms (95%的命令) |
| Proactive actions | None | 自动修电/采矿/防御 |
| Strategic awareness | None (每次调用无状态) | 30-60s 战略循环 + 持久记忆 |
| Error handling | 报错给用户 | 自动修复 + 重试 |
| Streaming | None | 实时反馈 |
| Model usage | Single (DeepSeek all) | Tiered (regex → cache → small → large) |
| Autonomy | Reactive only | Reactive + Proactive + Strategic |
| Feedback | "AI 正在分析" → 结果 | 流式思考 + 执行进度 + 战略态势 |

## Appendix B: Agent Ecosystem Map

```
                         ┌──────────────────┐
                         │  Agent Manager    │
                         │  (Orchestrator)   │
                         └────────┬─────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            │                     │                     │
    ┌───────▼───────┐    ┌───────▼───────┐    ┌───────▼───────┐
    │  Layer 0      │    │  Layer 1-2    │    │  Layer 3      │
    │  ReactiveAgent│    │  CommandAgent │    │  StrategyAgent│
    │               │    │               │    │               │
    │ • AutoPower   │    │ • NLU Gateway │    │ • 30s cycle   │
    │ • AutoHarvest │    │ • Sem. Cache  │    │ • LLM decide  │
    │ • ThreatResp  │    │ • Small Model │    │ • Memory      │
    │ • AttackJob   │    │ • DeepSeek    │    │ • Directives  │
    │ • ExploreJob  │    │               │    │               │
    │ • EconEngine  │    │               │    │               │
    └───────┬───────┘    └───────┬───────┘    └───────┬───────┘
            │                     │                     │
            └─────────────────────┼─────────────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │     Shared Infrastructure   │
                    │                             │
                    │  GameAPI ─ IntelService     │
                    │  MacroActions ─ JobManager  │
                    │  DashboardBridge            │
                    └─────────────────────────────┘
```
