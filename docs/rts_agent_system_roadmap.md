# RTS Agent System Roadmap

## 0. 目标重述

本项目的目标不是做一个“自己玩得很强”的全自动 RTS AI，也不是做一个“LLM 写 Python 控游戏”的实验玩具。

目标是构建一个：

- **以用户指令为中心**
- **时间敏感**
- **可并行调度**
- **可控且可解释**
- **由 AI 赋能传统游戏 AI**
- **可逐步吸收游戏常识**
- **支持多游戏适配**

的 **Directive-Driven Multi-Expert Game Agent System**。

核心思想：

1. LLM 不直接持续驾驶游戏。
2. LLM 主要负责把人话转成 **Directive / TaskSpec**，以及少量高不确定决策。
3. 真正执行由 **Kernel + Experts + World Model + Adapter** 完成。
4. 游戏“自然性”不主要来自 prompt，而来自 **共享世界模型 + 专家系统 + 仲裁逻辑**。
5. 复杂性不塞进 Kernel，而是分散到有限数量的 **领域专家** 中。

---

## 1. 顶层结构

系统最终形态建议收敛为 5 个核心部分：

### 1. Interpreter
负责把用户输入解释成系统可承接的 `Directive` 或 `TaskSpec`。

特点：
- 0~1 次主 LLM 调用
- 必要时 1 次澄清
- 不持续参与执行
- 不负责管理生命周期

### 2. Kernel
系统内核，负责：
- 任务对象存在
- Job 生命周期
- 资源绑定
- 并发与抢占
- 专家激活 / 退场
- 状态汇总
- 任务结果判定

Kernel 只处理跨领域共性，不下沉到 scout/combat/economy 的具体策略细节。

### 3. Shared World Model
系统统一数据面，维护：
- 游戏抽象状态
- 任务状态
- 资源占用
- 地图语义
- 敌情假设
- 编组与局势摘要

所有专家必须共享这套世界模型，不能各自维护私有世界观。

### 4. Expert Systems
有限数量的领域专家，而不是每个命令一个专家。

建议分为三类：
- **Information Expert**
- **Planner / Advisor Expert**
- **Execution Expert**

其中执行类专家需要运行时实例化。

### 5. Game Adapter
最底层适配 OpenRA 或未来其他游戏。
负责：
- 受控 API
- 事件回传
- 状态采样
- 动作翻译

---

## 2. 统一对象模型

### 2.1 Directive
解释层输出的上游意图对象。

用途：
- 表示用户到底想让系统做什么
- 还不是最终执行对象

字段方向：
- kind
- target
- constraints
- priority_hint
- authority
- ambiguity

### 2.2 TaskSpec
Kernel 归一后的任务规格。

用途：
- 描述某个任务的语义边界
- 定义允许的自动补全范围
- 定义成功 / 失败条件的大方向

TaskSpec 是执行前的正式任务定义。

### 2.3 ExecutionJob
真正进入调度系统的运行单元。

用途：
- 表示一次具体执行
- 带状态、资源占用、时间信息
- 可并行、可抢占、可取消

### 2.4 ExecutorInstance
某个执行专家的运行时实例。

用途：
- 接管一个 Job
- 维护专家内部状态机 / BT / HSM
- 推进具体执行过程

一个静态执行专家定义可以实例化出多个 ExecutorInstance。

### 2.5 Constraint
持续性约束对象。

用途：
- 压制或调制其他任务
- 不直接执行动作，但影响执行策略

例如：
- do_not_chase_far
- economy_first
- defend_when_attacked

### 2.6 Outcome
任务终态对象。

用途：
- 明确说明任务是 success / partial / failed / superseded / aborted
- 给上层和用户回报正式结论

---

## 3. 专家体系设计

### 3.1 专家分型

#### A. Information Expert
职责：
- 读 world model
- 产出估计、假设、评分、告警、摘要
- 不直接改游戏状态
- 不直接启动执行

实现方式：
- heuristic estimator
- hypothesis updater
- scoring function
- 小模型
- 规则系统

例子：
- EnemyLocationHypothesisExpert
- ThreatAssessmentExpert
- MapSemanticsExpert
- ResourcePressureExpert

#### B. Planner / Advisor Expert
职责：
- 在某个领域给出候选方案
- 提供局部 plan / proposal / recommendation
- 不直接绑定资源、不直接下发最终动作

实现方式：
- candidate generation
- utility scoring
- local search
- rule planner
- 必要时单次 LLM proposal

例子：
- ReconRoutePlanner
- CombatModeAdvisor
- ExpansionPlanner
- ProductionAdvisor

#### C. Execution Expert
职责：
- 接管 Job
- 绑定资源
- 推进任务执行
- 管理内部控制状态
- 汇报执行结果

实现方式：
- FSM
- HSM / State Tree
- BT subtree
- 固定控制器
- 局部小模型辅助

例子：
- ReconExecutor
- CombatExecutor
- EconomyExecutor
- DefenseExecutor

### 3.2 执行专家必须实例化

静态专家定义本身不能直接跑任务。

必须区分：

- `ReconExecutor`：静态定义
- `ReconExecutorInstance#12`：运行时实例

理由：
- 支持并行
- 隔离状态
- 隔离资源绑定
- 隔离局部黑板
- 支持不同任务同时运行

### 3.3 并发策略

并发的是 **ExecutionJob** 和 **ExecutorInstance**，不是专家定义本身。

每类执行专家都必须定义并发策略：

- 是否允许多实例
- 最大并发数
- 是否可共享某些资源
- 是否允许实例间抢占
- 是否允许 merge

例子：
- ReconExecutor：允许少量多实例
- CombatExecutor：允许多实例，但要按 squad 划分资源域
- EconomyExecutor：通常更适合单实例

---

## 4. Kernel 设计边界

Kernel 不是全知全能 AI。

Kernel 负责：

- TaskSpec → ExecutionJob
- Job admission
- Resource arbitration
- Constraint application
- Expert activation / deactivation
- Lifecycle progression
- Event aggregation
- Outcome judgment

Kernel 不负责：

- 具体侦查策略
- 具体接敌微操
- 路径风险建模细节
- 生产细节策略
- 领域知识长期膨胀

Kernel 必须始终保持“薄但强”。

---

## 5. 生命周期总线

一个任务从输入到结束，建议收敛为：

1. User input
2. Interpreter 形成 Directive
3. Kernel 归一成 TaskSpec
4. Kernel 创建 ExecutionJob
5. 激活相关 Experts
6. 由 Execution Expert 实例接管
7. 执行过程中 Information / Planner Experts 提供支持
8. Kernel 基于事件与约束判断终态
9. 产生 Outcome
10. 汇报用户 / 上层

### Job 大状态建议

- pending
- admitted
- binding
- running
- waiting
- blocked
- succeeded
- partial_succeeded
- failed
- superseded
- aborted

### Execution Expert 内部状态
由各专家自己定义，不进入 Kernel 共性层。

---

## 6. FSM / ST / BT 的位置

### 6.1 Kernel
不用复杂 FSM，只保留任务级状态。

### 6.2 Information Expert
通常不用 FSM 为主。
更适合：
- 估计器
- 打分器
- 规则更新器
- 假设更新器

### 6.3 Planner / Advisor Expert
通常不用 FSM 为主。
更适合：
- proposal generation
- utility scoring
- candidate ranking
- local planner

### 6.4 Execution Expert
这里才是 FSM / ST / BT 的主战场。

#### FSM 适合
- 阶段型任务
- 状态较少
- 转移清晰

#### ST / HSM 适合
- 多层级任务
- 子状态嵌套
- 复杂恢复与中断

#### BT 适合
- 条件驱动强
- fallback 多
- retry / selector / sequence 明显

结论：
- **FSM / ST / BT 都放在执行专家内部，不放在 Kernel。**

---

## 7. World Model 设计

World Model 是整个系统“自然性”的真正来源之一。

### 7.1 世界模型内容

#### A. 通用运行态
- 当前活跃 jobs
- 当前 executor instances
- 当前 constraints
- 资源占用
- 时间与优先级状态

#### B. 游戏抽象状态
- 区域控制
- 兵力编组
- 生产能力
- 资源态势
- 前线位置
- 危险区域
- 已知敌情

#### C. 地图语义
- 出生点
- 扩张点
- chokepoints
- 重要视野点
- 关键区域标签

#### D. 敌情假设
- 敌方主基地候选位置
- 敌方兵力分布假设
- 科技路径猜测
- 局部 threat posterior

### 7.2 设计原则
- 所有专家共享同一世界模型
- 世界模型不等于原始游戏状态
- 世界模型要抽象、可查询、可派生
- 不把关键长期记忆塞进 prompt

---

## 8. “AI 赋能游戏 AI”的结构定位

本系统不是：
- LLM 直接控游戏
- LLM 多次连环解释
- 传统行为树自己偷玩

而是：

### LLM 的作用
- 解释用户意图
- 选择相关专家域
- 补充任务边界
- 少数高不确定节点给出建议
- 赛后解释与复盘

### 游戏 AI 的作用
- 领域知识
- 世界模型
- 专家系统
- 状态控制
- 持续执行
- 稳定反应

### 系统最终效果
LLM 赋能游戏 AI，而不是替代游戏 AI。

---

## 9. 推荐的专家域划分

先收敛到有限数量，不要超过 8 个。

建议第一版专家域：

1. **Recon Domain**
2. **Movement / Positioning Domain**
3. **Combat Domain**
4. **Economy / Production Domain**
5. **Defense / Emergency Domain**
6. **Expansion / Territory Domain**
7. **Composition / Policy Domain**

说明：
- 表面上 20+ 命令，底层通常会投影到这 6~7 个专家域
- 先不要按命令建专家
- 按领域能力建专家

---

## 10. 第一阶段推荐支持的任务类型

只做 4 类任务语义，不要更多。

### A. Instant
一次性动作 / 查询

### B. Managed
持续任务  
例如侦查、防守、回撤、移动占位

### C. Background
后台持续任务  
例如生产、维修、队列管理

### D. Constraint
约束对象  
例如别追太深、优先经济、被打就回防

---

## 11. 演进路线

### Phase 0：结构定型
目标：
- 拍板统一对象模型
- 拍板专家分型
- 拍板 Kernel 边界
- 拍板 World Model 范围

交付：
- 架构文档
- 对象定义
- 专家 taxonomy
- 并发策略定义

### Phase 1：最小运行骨架
目标：
- 打通 Directive → TaskSpec → ExecutionJob → Outcome 主链
- 接入一个最小 Game Adapter
- 支持少量任务类型

交付：
- Interpreter v0
- Kernel v0
- World Model v0
- 一个 Execution Expert 示例

### Phase 2：多专家结构落地
目标：
- 引入 Information / Planner / Execution 三类专家
- 建立统一 contract
- Kernel 能按类型激活专家
- 支持并行 Job

交付：
- expert contracts
- executor instance framework
- arbitration hooks
- concurrency policy

### Phase 3：RTS Domain Substrate
目标：
- 把游戏“常识”从 prompt 迁移到系统
- 建立地图语义 / 单位画像 / 假设系统

交付：
- map semantics
- unit role/profile system
- threat / hypothesis system
- domain doctrine modules

### Phase 4：核心专家域扩展
目标：
- 不再靠单一示例驱动设计
- 逐步补齐少量核心专家域

优先顺序建议：
1. Recon
2. Economy / Production
3. Defense
4. Movement / Positioning
5. Combat
6. Expansion
7. Composition / Policy

### Phase 5：LLM 从“解释器”升级到“编组器”
目标：
- 不仅理解单条命令
- 还能够为一个 directive 激活合适专家组合
- 但仍然不接管持续执行

交付：
- expert activation hints
- directive decomposition
- high-level proposal interface

### Phase 6：局部学习模块
目标：
- 在某些专家域内部，用小模型 / RL / imitation 增强局部能力
- 不改变整体控制结构

适合的落点：
- recon route scoring
- local combat mode choice
- retreat safety scoring
- production priority hints

---

## 12. 研发分工建议

### 你
负责：
- Kernel 边界
- 对象模型
- 专家 taxonomy
- 激活 / 仲裁逻辑
- World Model 抽象
- 系统哲学

### 成员 A
负责：
- Adapter / Tooling / 可视化 / 状态面板 / 测试资产
- Job trace、World Model debug view、专家监控页

### 成员 B
负责：
- Interpreter / Expert contracts / Information & Planner experts / 小模型实验
- 后续局部学习模块

---

## 13. 风险与反模式

### 风险 1：Kernel 持续膨胀
现象：
- 每加一个任务就往 Kernel 塞领域细节

规避：
- 所有领域知识优先下沉到 Expert 或 World Model

### 风险 2：专家自治失控
现象：
- 专家自己偷偷扩张目标
- 专家既分析又执行又下战略

规避：
- 严格分型
- 严格 contract
- Kernel 掌握 admission 和 outcome judgment

### 风险 3：LLM 调用爆炸
现象：
- 一个任务多次调用 LLM
- 多并发任务导致 token 爆炸和状态不一致

规避：
- 单次主解释
- 仅在关键 deliberation point 调用
- 优先小模型 / 规则 / experts

### 风险 4：World Model 变成原始状态转储
现象：
- 没有抽象
- 所有专家仍然各自重做理解

规避：
- 明确维护抽象层
- 明确 map semantics / hypotheses / groupings / threat zones

---

## 14. 一句话收束

最终想要的不是一个“更会写代码的 LLM Agent”，而是一个：

> **由 LLM 负责语义编组、由 Kernel 负责调度与仲裁、由共享世界模型承载游戏抽象、由多领域专家系统持续执行的 RTS AI 操作系统。**
