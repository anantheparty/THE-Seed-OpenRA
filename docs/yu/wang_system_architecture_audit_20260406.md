# Wang 最近系统设计与报告深度审计

日期：2026-04-06  
作者：yu

## 0. 审计目标

本报告不是对某一份文档做字面 review，而是站在系统设计师视角，对 Wang 最近一组设计报告、当前代码现实、以及近两年 LLM 结合 RTS/多智能体控制的代表性论文做一次合并审计，回答三个问题：

1. 现在这套系统方向到底对不对？
2. 现有设计能不能做成一个像样的东西？
3. 如果能，最应该收敛成什么架构；如果不能，具体哪里必须改？

结论先给：

- 这套系统**可以做成一个像样的、可持续演进的 OpenRA Red Alert 智能副官/半自动指挥系统**。
- 但前提不是继续放任“每个 Task 一个自由规划 LLM 脑”无限生长，而是要把系统**收敛为单一高层规划脑 + 持久能力管理器 + 确定性执行 Expert + 信息 Expert** 的层级结构。
- Wang 最近几轮设计，尤其是 `architecture_crisis.md`、`capability_task_design.md`、`adjutant_redesign.md`、`optimization_tasks.md`，整体方向是在不断逼近这个正确解；只是当前代码还停留在**混合过渡态**，尚未真正收口。

## 1. 审计输入

### 1.1 Wang 最近的关键文档

我重点审阅了这些文档：

- `docs/wang/design.md`
- `docs/wang/system_report_v2.md`
- `docs/wang/architecture_crisis.md`
- `docs/wang/capability_task_design.md`
- `docs/wang/adjutant_redesign.md`
- `docs/wang/optimization_tasks.md`
- `docs/wang/r7_audit.md`
- `docs/wang/r8_audit.md`

这些文档代表的不是同一个静态设计，而是一条明显的演化轨迹：

- 先把系统做出来
- 再在 live/E2E 中发现“多 Task Agent 议会”问题
- 再引入 Capability、Runtime Facts、Information Expert、Task->Player 通信、NLU 前置路由
- 再逐步把自由 LLM 收缩到更合理的位置

### 1.2 当前代码现实

我核查了当前实现的核心模块：

- `adjutant/adjutant.py`
- `adjutant/runtime_nlu.py`
- `kernel/core.py`
- `world_model/core.py`
- `task_agent/agent.py`
- `task_agent/context.py`
- `task_agent/handlers.py`
- `experts/base.py`
- `experts/knowledge.py`
- `experts/info_base_state.py`
- `experts/info_threat.py`

重要事实：

- 代码已经**明显超出了旧设计阶段**。
- 当前系统不是“完全还是旧多 Task 架构”，也不是“已经变成单 Commander”。
- 它实际上是一个**混合过渡态**：
  - 上层仍然有多个 TaskAgent
  - 但已加入 NLU 前置、rule fast-path、UnitRequest、info experts、runtime facts、task message、smart wake 等补丁
  - 这些补丁的共同方向，都是在**削弱多脑自由规划的破坏性**

### 1.3 外部研究参考

我参考了近两年与 LLM/VLM + RTS 直接相关的代表性论文与技术报告，重点看其架构模式而不是只看指标：

- Large Language Models Play StarCraft II: Benchmarks and A Chain of Summarization Approach  
  https://arxiv.org/abs/2312.11865
- SwarmBrain: Embodied agent for real-time strategy game StarCraft II via large language models  
  https://arxiv.org/abs/2401.17749
- Harnessing Language for Coordination: A Framework and Benchmark for LLM-Driven Multi-Agent Control  
  https://arxiv.org/abs/2412.11761
- Hierarchical Expert Prompt for Large-Language-Model: An Approach Defeat Elite AI in TextStarCraft II for the First Time  
  https://arxiv.org/abs/2502.11122
- VLMs Play StarCraft II: A Benchmark and Multimodal Decision Method  
  https://arxiv.org/abs/2503.05383
- Self-Evolving Multi-Agent Framework for Efficient Decision Making in Real-Time Strategy Scenarios  
  https://arxiv.org/abs/2603.23875

这些工作虽然环境、目标、模型和评估方式不同，但它们在一个问题上高度一致：

**LLM 不能裸奔在 RTS 全链路上。必须上移到规划/协调层，并由结构化知识、层级摘要、快速执行器、角色分工、反应式模块来约束。**

## 2. Wang 最近设计决策的真实走向

### 2.1 `architecture_crisis.md` 的判断基本正确

这份文档的核心论断是：

- 多 Task Agent 模式本身有系统性问题
- RTS 需要一个战略大脑，而不是一个议会

这个判断我认为是**正确的，而且是整个最近设计流的转折点**。

原因不是抽象哲学，而是项目已经在 live/E2E 中反复暴露出以下现象：

- 复合命令被多个 Task brain 自由拆解，漂成大量低质量 job
- 一个 Task 在补前置，另一个 Task 又重复补
- 生产/科技/侦察/进攻在不同 task 脑中没有共享战略上下文
- narrative summary 会压过显式状态
- success/partial/fail 的因果归属变得模糊

如果继续把“每个输入 -> 一个独立 LLM TaskAgent”当成最终架构，这些问题只会随着功能增加继续恶化。

### 2.2 `capability_task_design.md` 不是偏题，而是正确的中间桥梁

这份设计提出：

- 普通 Task 不直接做生产规划
- 单位/生产需求先变成 `UnitRequest`
- Kernel 走 idle 匹配 / bootstrap / 分配
- 引入持久 `EconomyCapability`

这不是对 Commander 方案的背离，而是一个非常合理的**过渡性拆分**：

- 把最容易引发多脑冲突的“经济/生产/共享队列”先中央化
- 同时避免一次性推翻整个系统

当前代码里已经出现了这条线的实装痕迹：

- `kernel/core.py` 中有 `UnitRequest`
- 普通 TaskAgent prompt 已改为优先 `request_units`
- `produce_units` 不再是普通 task 的推荐主路径

这是对的。

### 2.3 `adjutant_redesign.md` 的方向也是正确的

这份设计的核心有三条：

- NLU/rule 优先，简单命令直接执行
- LLM 只负责难命令、追加命令、disposition
- task 之间的 merge/override/interrupt 应由上层统一处理

当前代码也已经部分落地：

- `adjutant/runtime_nlu.py` 已接入旧 NLU 前半段
- `adjutant/adjutant.py` 先尝试 runtime NLU，再尝试 rule，再掉入 LLM 分类

这说明 Wang 最近的判断是连续一致的：

- 不是“再调 prompt 就行”
- 而是要把**语言理解、任务处置、专家执行、共享资源**这几个层次重新分清

### 2.4 `optimization_tasks.md` 其实是在修真正的根因

这份文档最重要的一点是：

**不要再继续用限制 LLM 行为来掩盖信息不足；要给它更好的信息。**

当前代码里最有价值的近期演进，正来自这份设计：

- `world_model.compute_runtime_facts(...)`
- `experts/info_base_state.py`
- `experts/info_threat.py`
- `task_agent/context.py` 中的结构化 context packet
- `send_task_message`
- smart wake
- logging / trace / session persistence

这些改动不是小修小补，而是把系统从“prompt 工程堆出来的 agent”推向“有可操作信息面的 agent system”。

## 3. 当前代码的真实架构状态

### 3.1 现在不是“旧架构”，而是“混合过渡架构”

当前系统最准确的描述是：

**Adjutant + Runtime NLU + Kernel + 多 TaskAgent + Expert jobs + Runtime Facts + Information Experts + UnitRequest/Capability patch**

它有三个层次：

1. 玩家入口层  
   Adjutant + runtime NLU + rule routing + LLM classification/disposition

2. 调度与状态层  
   Kernel + WorldModel + shared resources + pending questions + task messages

3. 执行层  
   Execution Experts / Jobs / Info Experts

### 3.2 仍然存在“多个高层 brain”

这是当前最重要的现实：

- `kernel/core.py:create_task()` 仍然会为每个 task 创建一个 TaskAgent
- 每个 TaskAgent 都有自己的 conversation、wake cycle、LLM 决策循环

也就是说，**多脑问题并没有在代码层被根除**。

只是系统已经用很多手段在尽量降低它的伤害：

- 简单命令先不进 LLM
- 生产请求从 task 中抽离
- 注入 runtime facts
- 暴露 other active tasks
- 增加 info subscriptions
- 加 send_task_message
- 加 smart wake

这说明 Wang 的系统现在处在一个非常典型的阶段：

- 正在靠补丁把“错误的大框架”维持到可用
- 同时不断长出“正确的大框架”的局部器官

### 3.3 `TaskAgent` 已经变得比旧 trace 好很多，但还不是最终解

当前 `TaskAgent` prompt 已经比早期状态强很多：

- 明确了工具使用规则
- 引入 runtime facts 优先级
- 引入 open-world task milestone
- 引入 wait / duplicate job / retry guard
- 引入 `send_task_message`
- 区分 normal vs capability prompt

这意味着：

- Wang 最近关于“问题主要在 prompt + context + runtime semantics，而不是模型本身”的判断，是对的

但这不等于：

- 只要继续磨 prompt，就能把多 TaskAgent 架构磨成最终解

我不这么看。

### 3.4 `WorldModel` 和 Information Experts 的方向非常值得保留

当前 `world_model.compute_runtime_facts(...)` 已经开始输出真正有用的结构化事实：

- `has_construction_yard`
- `power_plant_count`
- `barracks_count`
- `refinery_count`
- `war_factory_count`
- `radar_count`
- `tech_level`
- `mcv_count`
- `mcv_idle`
- `harvester_count`
- `failed_job_count`
- `same_expert_retry_count`
- `buildable`
- `feasibility`
- `enemy_intel`

再叠加 `BaseStateExpert` 和 `ThreatAssessor`，这条路非常符合外部研究趋势：

- LLM 不应从 raw state 现推 doctrine
- 结构化事实、派生状态、领域知识应该先被编译出来

这部分是我认为**最值得继续投入**的方向之一。

### 3.5 当前代码里的关键证据

为了避免本报告只停留在抽象判断，这里列几个最能说明问题的代码事实：

1. `kernel/core.py:create_task()` 仍然是“每个 task 一个 agent”
   - 每创建一个 task，就构造一个新的 `TaskAgent`
   - 然后把 `runtime_facts_provider`、`active_tasks_provider` 注进去
   - 这说明系统当前仍默认把 task 当作一个独立决策脑的容器

2. `adjutant/runtime_nlu.py` 已经把旧 NLU 前半段接回来了
   - 支持 `deploy_mcv / produce / explore / mine / stop_attack / query_actor`
   - 也支持受控的 `composite_sequence`
   - 说明“简单命令先别进 LLM”已经不是概念，而是主链的一部分

3. `task_agent/agent.py` 的 prompt 已经明显向“结构化 runtime”倾斜
   - 明确声明 `runtime_facts > signals > query_world > world_summary`
   - normal agent 被引导优先 `request_units`
   - 已支持 `send_task_message`
   - 但它仍然是一个非常重的 system prompt，说明很多系统语义还没真正下沉

4. `task_agent/context.py` 已开始把上下文编译成更决策友好的形式
   - 有 `runtime_facts`
   - 有 `other_active_tasks`
   - 有 `info_subscriptions`
   - 有 compact world summary
   - 这是对的，但还没有把“phase / success guard / repeated failure signature”全部做成显式事实

5. `task_agent/handlers.py` 现在已经有正式的 `send_task_message`
   - 这说明“task 应能通过 Adjutant 与玩家结构化对话”这条旧漂移，已经开始回正

换句话说，当前代码并不是“Wang 的文档写得激进，代码还完全没动”；相反，代码已经朝这些设计方向走了不少，只是还没有完成最后的架构收敛。

## 4. 结合外部研究后的核心判断

## 4.1 近年 LLM+RTS 工作的共同结论

代表性工作虽然实现细节不同，但共同结构非常一致：

### A. TextStarCraft II / Chain of Summarization

`2312.11865` 的核心不是“让 LLM 直接打星际”，而是：

- 先把复杂状态做文本化
- 再做单帧/多帧摘要
- 微操仍靠规则脚本

可迁移结论：

- LLM 应该站在**摘要层**和**决策层**
- 不是直接吃全量原始状态，更不是直接做高频控制

### B. SwarmBrain

`2401.17749` 明确采用了双层结构：

- Overmind Intelligence Matrix：高层战略脑
- Swarm ReflexNet：快速反应式状态机

可迁移结论：

- “慢脑做战略，快脑做反应”不是可选项，是 RTS 场景里的必需结构
- 这和 Wang 的“Commander / Capability / Expert”收敛方向高度一致

### C. HEP / Hierarchical Expert Prompt

`2502.11122` 的价值不在“赢了 Elite”，而在于它再次证明：

- 光靠泛化 prompt 不够
- 必须把专家知识和分层决策协议显式注入

可迁移结论：

- 你们最近做的 `experts/knowledge.py`、runtime facts、information experts，方向是对的
- 但还应该继续从“prompt 装知识”走向“expert 自己输出知识性结论”

### D. VLMs Play StarCraft II / AVA

`2503.05383` 的重要结构是：

- 感知增强
- retrieval-augmented knowledge
- dynamic role-based task distribution

可迁移结论：

- 多角色可以存在
- 但多角色必须由更高层统一分工，而不是多个并列自由 Task brain 互相撞

### E. HIVE / LLM-driven multi-agent control

`2412.11761` 更接近你们的真实产品目标，因为它不是完全自主 RTS bot，而是面向人机协作与大规模协调。

可迁移结论：

- 如果产品目标是“一个强的人类副官/指挥助手”，那么比起全自动最强 bot，更该重视：
  - 可解释性
  - 低延迟
  - 用户对系统状态的可见性
  - 指令到动作的稳定映射

### F. SEMA 2026

`2603.23875` 再次把一个现实讲得很清楚：

- RTS 场景里 LLM 最大矛盾就是 `speed-quality trade-off`
- 解决方法不是盲目多 agent
- 而是 observation pruning、hierarchical domain knowledge、memory、hybrid fast path

这和你们现在的 runtime facts / NLU fast-path / info experts / smart wake 完全同向。

## 4.2 从这些研究反看本项目：Wang 的直觉大体是对的

把外部工作和本项目并排看，Wang 最近的设计里有三个关键判断是成立的：

1. 不能让高层意图解析全部掉进自由 LLM  
2. 不能让生产/共享资源由多个 task 脑各自乱抢  
3. 必须给 agent 更结构化、更具语义的世界信息  

这三点都与近年工作高度一致。

## 5. 现有设计是否可行

### 5.1 如果目标是“像样的 OpenRA 智能副官”，可行

如果目标定义为：

- 能理解中文指令
- 能稳定执行开局建造、爆兵、侦察、简单防守/进攻
- 能给出可读反馈
- 能在 live 游戏里长期可维护地工作

那么这套系统**是可行的**。

原因：

- OpenRA RA 的动作空间相对可控
- 你们已经有了不错的传统执行层
- 旧 NLU 已经回收
- WorldModel、Expert、Kernel、logging、UI 这些工程基础已经不是从零开始
- 最近的硬化工作已经证明，很多“像样产品需要的细节”是能被逐步收掉的

### 5.2 如果目标是“靠当前多 TaskAgent 架构做出稳定战略脑”，不可行

如果不做进一步收敛，而继续沿着当前混合架构把功能堆上去，那么我认为它**无法长期稳定**。

原因不是局部 bug，而是结构问题：

- 多个自由规划 brain 的战略一致性天然差
- success/partial/fail 的因果归属会越来越难判断
- 共享资源和共享队列会持续成为冲突热点
- 用户很难理解“系统到底是谁在做决定”

它可以继续通过更多 patch 维持一段时间，但成本会越来越高。

### 5.3 如果目标是“全自动高水平 RTS AI”，短期不可行

如果目标升级成：

- 几乎不依赖人
- 做中高水平全自动战略博弈
- 在长局里持续压制电脑/玩家

那当前以 LLM 为主的上层决策方式还不够。

缺的不是一个 prompt，而是：

- 更强的地图/威胁建模
- 位置与路径结构
- 开局/对局阶段 model
- 更系统的 counter/tech planning
- 更强的 memory/retrospective learning
- 更严格的世界验证与因果守卫

所以更现实的产品定位仍应是：

**强人类副官 / 半自动指挥系统**  
而不是  
**立即追求像 AlphaStar 那样的全自动竞技代理**

## 6. 当前设计中最关键的优点

### 6.1 NLU 前置 + 直接执行是对的

简单命令不该进 managed-task LLM。

这条现在终于基本走对了：

- `RuntimeNLURouter`
- rule fast-path
- shorthand 支持
- composite safe sequence 支持

这是整个系统稳定性的根。

### 6.2 把知识融进 Expert，而不是只写 prompt，是对的

你们现在的 `experts/knowledge.py`、`EconomyExpert` 的结构化输出，是一条非常值得继续走的路。

未来应该继续扩成：

- EconomyExpert
- ReconExpert
- CombatExpert
- Capability planners

共同输出：

- `roles`
- `impacts`
- `recovery_package`
- `downstream_unlocks`

这样 Planner/Commander 才不必每次从 raw state 重新发明 doctrine。

### 6.3 Information Experts 是正确方向

当前的 `BaseStateExpert` 和 `ThreatAssessor` 还很初级，但其方向非常对：

- persistent
- 低成本
- 派生语义
- 可订阅

这是把系统从“模型猜”推进到“系统知道”的关键。

### 6.4 可观测性开始成形

现在已有：

- task trace
- runtime session log
- per-task log files
- diagnostics sync/replay

这件事非常关键。没有它，就没有真正的系统迭代能力。

## 7. 当前设计中最关键的缺口

### 7.1 最大缺口不是某个 Expert，而是“谁是唯一战略脑”没有最终定型

这是当前最核心的问题。

现在系统同时存在三种趋势：

- 继续给每个 task 一个脑
- 用 capability 修补其中一部分
- 又想引入 Commander

如果不正式定型，系统会一直处于过渡态。

我的判断是：

**最终应收敛到单一高层规划脑。**

这个脑可以叫：

- Commander
- Strategic Adjutant
- Planning Core

名字不重要，原则重要：

- 全局战略排序只能有一处
- 不能继续让多个 task 脑并列争夺高层解释权

### 7.2 现在的 Task 概念仍然过重

当前系统里 Task 同时承担了：

- 用户可见工作项
- LLM brain 生命周期容器
- job 聚合器
- 对话上下文边界

这四种职责不应该永远绑在一起。

更合理的收敛是：

- Task：用户可见工作项 / 计划项
- Capability Manager：持久领域控制器
- Commander：全局规划
- Job：Expert 的具体执行实例

也就是说：

**Job 是 Expert 的实例**  
**Task 不应再天然等价于“一个独立脑”**

### 7.3 Production/Economy 仍然只是部分中央化

`UnitRequest` 和 `EconomyCapability` 方向是对的，但还不够完整。

未来至少还会出现类似中央化需求：

- Recon capability
- Combat capability
- Queue manager
- Base recovery manager
- Awareness manager

否则多 task 脑之间的冲突只会从经济迁移到其他域。

### 7.4 Prompt 现在仍然承担了过多系统职责

当前 `TaskAgent` prompt 已经很长，而且在承担：

- 行为约束
- 游戏知识
- 完成规则
- retry policy
- info communication policy
- queue/verification policy

这说明一个现实：

很多系统逻辑仍然没有被下沉到更稳定的结构层。

更长期正确的方向是：

- prompt 只保留角色与高层规则
- 领域知识下沉到 Expert/knowledge
- 运行时状态下沉到 runtime facts/info experts
- 关键 completion guard 下沉到 Kernel/Expert semantics

### 7.5 Commander 方案本身也不能做成“一个超大 prompt 万能脑”

这里必须提醒一个风险。

即使最终走 Commander，也不代表：

- 一个超长 prompt 的万能 LLM 就能解决一切

正确的 Commander 应该是：

- 有全局记忆和优先级状态
- 只做战略/任务级决策
- 消费 info experts / capability summaries
- 调用确定性 capability/job interfaces
- 有严格的 phase policy 和 success guard

不是“把现在每个 TaskAgent 的复杂性全部堆进一个更大的 prompt”。

### 7.6 当前 TaskAgent 的真正问题更像“信息与语义设计问题”，不只是模型问题

结合最近对单条 task trace 的逐轮分析，我认为必须明确一点：

- 现在很多错判，**不能简单归因为 LLM 本身差**
- 更真实的根因通常是：
  - prompt 约束不够硬
  - context 不够决策友好
  - signal/event 的因果语义不够干净
  - job/expert/task 的角色边界对 LLM 不够清晰

具体体现包括：

1. prompt 重复注入问题  
   当前 TaskAgent 每轮 wake 都会重新注入固定 system prompt，再叠加历史对话和新的 `[CONTEXT UPDATE]`。这不是 bug，但会让 raw log 很长，也意味着：
   - 重复信息很多
   - token 压力很大
   - 真正关键的增量信息容易被淹没

2. 初始信息仍然不足  
   当前 runtime facts 虽然已经比早期强很多，但对于某些任务，LLM 依然拿不到足够直接的决策事实，例如：
   - 当前是否已经“功能性建基地”
   - 某类动作的重复失败次数和失败签名
   - 某一步是否被明确验证成功
   - 当前局势处于哪个可操作阶段

3. signal 顺序和语义仍然需要更强约束  
   即使近期已经修过一轮 `job_started` 和 `resource_lost` 的顺序问题，系统整体仍然存在一个结构性风险：
   - LLM 容易把 movement 成功误读成 deploy 接近成功
   - 容易把 retry 误读成 progress
   - narrative summary 仍可能压过显式状态

4. Job / Expert / Task 三者的概念还不够统一
   对代码来说：
   - Expert 是类型
   - Job 是 Expert 的实例
   - Task 是用户工作项/归属容器
   但对 LLM 和用户可见层来说，这三者的界面仍然不够清晰，导致：
   - 用户看不懂一个 task 里到底有哪些专家在做什么
   - LLM 也不总能稳定地把“一个 job 的局部结果”和“整个 task 的完成条件”区分开

这说明后续真正该做的，不是只继续换模型，而是继续把：

- completion guard
- phase policy
- failure signature
- capability ownership
- expert output semantics

这些东西从“LLM 自己体会”变成“系统显式表达”。

## 8. 我建议的最终收敛架构

## 8.1 建议目标形态

建议最终收敛成五层：

### 第 1 层：Adjutant / NLU Front Door

职责：

- 玩家唯一语言入口
- ACK / query / cancel / command / reply 分类
- 旧 NLU 前半段统一前置
- 简单安全命令直接执行
- 否则转给 Commander

这层不做深规划，只做语言入口和路由。

### 第 2 层：Commander（唯一高层规划脑）

职责：

- 维护全局战略意图
- 维护当前阶段
- 决定 new / merge / override / interrupt
- 决定是否创建/更新 task
- 决定调用哪个 capability

这层不直接做高频执行。

### 第 3 层：Capability Managers（持久领域管理器）

至少应包括：

- EconomyCapability
- ReconCapability
- CombatCapability
- QueueManager
- 未来可能还有 BaseRecovery / Awareness / DefenseCapability

职责：

- 领域内持续优化
- 吸收同类需求
- 管共享资源与共享队列
- 对 Commander 暴露稳定接口和领域状态摘要

### 第 4 层：Execution Experts / Jobs

例如：

- DeployExpert / DeployJob
- EconomyExpert / EconomyJob
- ReconExpert / ReconJob
- CombatExpert / CombatJob
- MovementExpert / MovementJob

职责：

- 执行具体动作链
- 上报结构化 signal
- 不承担全局规划

### 第 5 层：Information Plane

由以下部分构成：

- WorldModel
- runtime facts
- information experts
- knowledge base
- logs / trace / evaluation

这层负责“让系统知道自己在什么状态”。

## 8.2 Task 在最终形态里应是什么

我建议最终把 Task 定义为：

- 用户可见工作项
- Commander 的计划项
- capability/job 的归属容器

而不是：

- 默认就要配一个独立 LLM brain

也就是说：

- 有的 task 只是 rule-routed direct task
- 有的 task 只是 capability 持续处理的请求
- 只有少数复杂 task 需要临时 sub-agent

但默认不应再是“task = 独立 LLM”

## 9. 具体改进建议

### 9.1 设计层

1. 正式冻结一个“目标架构版本”
   - 当前 `system_report_v2.md`
   - `architecture_crisis.md`
   - `capability_task_design.md`
   - `adjutant_redesign.md`
   共同组成的是一条演化流，而不是一个单一静态规范。
   - 建议 Wang 输出一份新的 `design_v_next.md`
   - 明确哪些是目标、哪些是过渡、哪些已废弃

2. 正式确认 Commander 是否进入下一阶段实现
   - 我的建议：要
   - 否则系统会一直停留在“多脑 + 补丁”的不稳定均衡

### 9.2 代码层

1. 保留并继续强化 `RuntimeNLURouter`
2. 继续把知识从 prompt 下沉到 Experts / knowledge
3. 扩展 info experts，不要只停在 `base_state` 和 `threat`
4. 把 `QueueManager`、`AwarenessManager` 这类单例 runtime manager 规范化
5. 逐步把 `TaskAgent` 从“通用规划脑”降成：
   - capability 内部脑
   - 或少数复杂子任务脑

### 9.3 Prompt / Context 层

1. 减少“大而全 prompt”
2. 明确区分：
   - Commander prompt
   - Capability prompt
   - Execution/Task prompt
3. 继续扩 `runtime_facts`
4. 继续做订阅式 info experts
5. 避免让 LLM 从 raw signals 自己猜完成条件

### 9.4 评估层

必须把评估分成三类：

1. NLU/route correctness
2. workflow correctness
3. live human experience

目前系统的很多进步，来自 live hardening，而不是 mock E2E。这是对的，应该保持。

## 10. 最终判断

### 10.1 这套系统能不能做成像样的东西？

能。

但不是按照“每个 Task 一个自由规划 LLM”这个方向继续堆下去。

### 10.2 Wang 最近的设计判断对不对？

大体上是对的，而且越来越接近正确解。

尤其这些判断我认为是成立的：

- 多 Task Agent 架构有系统性问题
- 经济/生产必须中央化
- 简单命令必须走 NLU/rule fast-path
- 信息质量比“限制模型自由”更重要
- information experts 和 runtime facts 是正确投资方向

### 10.3 我认为最应该做的事是什么？

不是再补 20 个散装 patch。

而是：

1. 正式定型目标架构：Commander + Capability + Experts + Information Plane
2. 把当前过渡系统里的正确部件保留下来
3. 有计划地消解“task=brain”这个旧假设

### 10.4 如果不这么做，会怎样？

系统短期还能用，甚至还能继续修出不少亮点；
但长期会越来越依赖：

- prompt 补丁
- route 例外
- completion 特判
- queue/资源善后
- 特定 live case 的临时硬化

最终维护成本会越来越高，战略一致性仍然不稳定。

### 10.5 如果这么做，最有希望达到什么水平？

我认为可达到的、现实且值得追求的目标是：

**一个可长期维护、可解释、对玩家有用、在 OpenRA RA 中有明显“会指挥、会执行、会反馈”体验的智能副官系统。**

这已经是一个很像样的东西。

不是论文 demo，也不是只会聊天的壳，而是真正能在 live 游戏里持续工作的系统。

## 11. 附：我对 Wang 最近决策的总体评价

如果用一句话概括：

**Wang 最近的系统设计，不是跑偏了，而是在痛苦地从“多 agent 幻觉”回到“RTS 需要中心化指挥 + 领域能力 + 确定性执行”的正确轨道上。**

当前最需要的不是更多概念，而是：

- 明确目标架构
- 减少过渡态的双重逻辑
- 用几轮强约束重构把系统真正收口

这个项目现在离“做不出来”并不近；离“做成样子”也不远。  
真正的分水岭在于：接下来是继续 patch，还是正式完成一次架构收敛。
