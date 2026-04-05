# 对 Wang `design_v_next` 的纠偏报告

日期：2026-04-06  
作者：yu

## 1. 结论先给

`design_v_next.md` 的问题，不是方向完全错误，而是**执行价值很弱**。

它最大的问题有三条：

1. **重复发明 Commander 概念，抹掉了现有系统里 Adjutant 已经承担的 Commander 职责**
2. **把“目标态愿景”写成“当前应实施的系统分层”，会直接把团队带进过度重构**
3. **没有尊重当前代码现实，导致文档里的主矛盾和真正该修的 runtime 问题错位**

更直接地说：

- 现在系统的问题，不是“还缺一个 Commander”
- 现在系统的问题，是：
  - `Adjutant` 需要继续做厚
  - `Capability` 需要做实
  - `TaskAgent` 需要继续收缩成受限的复杂任务推理器
  - `Information Plane` 需要继续补强 runtime facts / expert outputs / success guard

如果这份文档被当成后续开发主线，团队会被带去做一轮**概念重命名式重构**，而不是解决 demo 和 runtime 里真实存在的问题。

## 2. 当前系统的真实结构

按当前代码，最接近事实的结构是：

```text
Adjutant（前门 + Commander）
  ├─ RuntimeNLU / Rule Routing
  ├─ query / reply / cancel / disposition
  ├─ capability merge
  └─ 玩家可见输出统一口

↓

Capability / direct job / optional TaskAgent
  ├─ 简单安全命令 → direct job
  ├─ 经济/生产域 → EconomyCapability
  └─ 复杂 managed task → TaskAgent

↓

Execution Experts / Jobs
↓
WorldModel + RuntimeFacts + Information outputs + Logs
```

对应代码证据：

- `adjutant/adjutant.py`
  - 已经负责玩家唯一入口、NLU 前置、rule 路由、query/reply/cancel、merge/override/interrupt、capability merge、玩家可见输出
- `adjutant/runtime_nlu.py`
  - 已接入旧 NLU 前半段
- `task_agent/agent.py`
  - 现在已经不是默认生产/建造脑；生产权正被抽离
- `kernel/core.py`
  - 已承接 direct-managed task、UnitRequest、共享调度

所以，**当前系统里最接近 Commander 的就是 Adjutant 本身**。

再单独发明一层 `Commander`，会导致：

- `Adjutant` 和 `Commander` 职责重叠
- disposition 到底谁做不清楚
- merge/override/interruption 路径不清楚
- capability 路由到底归谁不清楚
- 玩家消息统一出口到底归谁不清楚

这不是抽象上的“小问题”，而是会直接制造实现混乱。

## 3. `design_v_next` 的主要问题

### 3.1 把 Adjutant 错写成“只负责路由的前门”

文档里把 Adjutant 定义为：

- 语言入口
- 分类
- NLU 前置
- rule routing
- 输出统一
- 不做战术/战略决策

这和当前现实不符。

当前 `Adjutant` 已经在做：

- disposition
- merge/override/interrupt
- capability merge
- query answer orchestration
- 对玩家的统一叙事与反馈

也就是说，**Adjutant 不是一个“薄入口”**。  
它已经是上层控制器。

如果文档继续坚持“Adjutant 不做战略判断”，那实现会被迫再补一层新的 Commander，造成纯概念内耗。

### 3.2 Commander 设计得太早、太满、太像下一轮大重构起点

`Commander` 一节里定义了：

- 战略意图
- 阶段判断
- Task 处置
- Capability 调用
- 优先级仲裁
- 主动规划
- tool set
- wake 策略

这份定义在理论上整齐，但在当前项目阶段是**过早的完整中心脑设计**。

当前更现实的状态是：

- EconomyCapability 还在成型
- Recon/Combat capability 还不存在
- TaskAgent prompt/context/runtime semantics 仍在 hardening
- 信息面还在补基础语义

这时直接把 `Commander` 提成主轴，执行结果只会是：

- 团队误以为“现在该做 Commander 重构”
- 现有 `Adjutant`、`TaskAgent`、`Capability` 三者关系再次被打散
- 运行时问题被新概念掩盖

### 3.3 Capability 家族和 Information Expert 家族铺得太开

文档里一下子铺了：

- Economy / Recon / Combat / Base Capability
- BaseStateExpert / ThreatAssessor / QueueStateExpert / AwarenessExpert / TechGateExpert / RecoveryAdvisor
- Planner Expert 全家桶

这在目标态上可以成立，但**对当前代码没有执行价值**。

当前真正应该保留并继续投入的只有：

- `EconomyCapability`
- `BaseStateExpert`
- `ThreatAssessor`
- `ProductionAdvisor`
- 以及少量直接支撑 live runtime 的 queue / awareness / tech gate facts

其他东西现在写得太满，只会让人误判“下一阶段要同时造一堆新层和新类”。

### 3.4 Task 被重新抽象过头

文档试图把：

- task
- direct task
- managed task
- capability
- sub-agent task

重新全部重新命名和定位。

但当前系统真正需要的，不是重新定义 task 哲学，而是：

- 降低默认 TaskAgent 覆盖面
- 明确 direct job / capability merge / optional managed task 的实际分流

换句话说，当前最需要的是**执行路径收口**，不是“Task ontology”。

## 4. 当前系统应该怎么描述，才有执行价值

### 4.1 正确的核心句

应该写成：

> 当前系统的正确收敛方向，不是 `Adjutant → Commander → Capability → TaskAgent`，而是 `Adjutant（兼 Commander）→ Capability / direct job / optional TaskAgent → Experts`。

### 4.2 当前最值得保留的骨架

#### 保留并强化

- `Adjutant = Front Door + top-level coordinator`
- `RuntimeNLU + rule fast path`
- `EconomyCapability`
- `Execution Experts / Jobs`
- `WorldModel + RuntimeFacts + Information outputs + Logs`

#### 收缩而不是扩张

- `TaskAgent`
  - 只保留给复杂 managed task
  - 不再默认代表“系统的大脑”

#### 只作为未来目标，不作为当前实施主线

- 独立 `Commander`
- `ReconCapability`
- `CombatCapability`
- `BaseCapability`
- 一整套 Information Expert 新家族
- 普遍化 sub-agent 体系

## 5. 真正有执行价值的近期架构结论

### 5.1 先把 Adjutant 做成真正的上层协调器

当前最有执行价值的架构目标不是新建 Commander，而是让 `Adjutant` 更明确地承担现有 Commander 职责：

- 前门统一
- NLU/Rule/LLM disposition 统一
- capability merge 统一
- 玩家消息统一
- 复杂任务是否进入 TaskAgent 的总闸统一

### 5.2 先把 EconomyCapability 做实

`Capability` 方向是对的，但当前只应先把第一块做实：

- 共享生产队列
- build/produce request
- 电力/矿场/矿车恢复
- shared queue 善后

不要把第一版 Capability 写成“大而全的经济战略脑”。

### 5.3 继续把 TaskAgent 降级成受限局部推理器

当前正确方向不是继续把 TaskAgent 做得更万能，而是：

- 用 prompt 约束它
- 用 runtime facts 喂饱它
- 用 expert signal / success guard 约束它
- 缩小它的使用范围

也就是：

- 默认路径：`NLU/rule/capability`
- 复杂路径：`optional TaskAgent`

### 5.4 Information Plane 继续做厚，这是最值得投入的方向

应该继续增强：

- runtime facts
- buildable / feasibility
- phase / failure signature
- deploy / recon / combat 的显式 guard
- expert 输出的 `roles / impacts / recovery_package`

这条线是真正符合最近 LLM+RTS 实践经验的。

## 6. 对 Wang 这版设计的直接批评

直接说：

1. **你把现有系统里已经成立的 Adjutant 角色说没了。**
   这是最大问题。当前系统已经不是“前门 + 一个未来 Commander 的空壳”，而是 `Adjutant` 本身已经承担上层协调。

2. **你把目标态愿景写成了当前执行框架。**
   这会让团队误以为下一步该做 Commander 大重构，而不是把现有运行时收稳。

3. **你在正确方向上过度抽象、过早分层。**
   Capability / Information Plane 值得继续，但不是现在同时铺四个 capability、多个 info expert 家族和 sub-agent 体系。

4. **这版文档不够尊重代码现实。**
   它更像一个重新命名后的理想图，而不是一个对当前系统有强执行指导价值的收敛文档。

## 7. 建议替换方案

如果要把 `design_v_next` 改成真正有执行价值的版本，建议直接改成下面三条主线：

### 主线 A：明确承认 Adjutant 现在就是上层控制器

- 不再单独新造 Commander 概念
- 先把 Adjutant 写成当前的 top-level coordinator

### 主线 B：Capability 先只落 Economy

- 不再铺满 Recon/Combat/Base capability
- 先把 EconomyCapability 做实、做稳、做可解释

### 主线 C：TaskAgent 改写为“复杂 managed task 的局部推理器”

- 不是默认 brain
- 不是系统主控制器
- 只在前门和 capability 都处理不了时进入

## 8. 最终判断

Wang 这版设计不该被当成下一阶段的直接实施蓝图。

它的价值在于：

- 对“多 TaskAgent 是错误终态”这个判断是对的
- 对 Capability / Information Plane 的方向判断是对的

它的缺陷在于：

- 过度设计
- 概念重叠
- 忽视现有结构
- 对近期执行几乎没有直接帮助

更正确的收敛文档应该：

- 以当前代码为基点
- 承认 Adjutant 已经承担 Commander 职责
- 把 Capability 做成真正的一条收敛线
- 把 TaskAgent 明确降级
- 把 Information Plane 列为继续投入主线

这才是对当前系统真正有执行价值的架构描述。
