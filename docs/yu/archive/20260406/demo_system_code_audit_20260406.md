# Demo 导向系统代码审计

日期：2026-04-06  
作者：yu

## 0. 目标

这份报告不是泛泛的代码 review，而是站在“明天要 live demo”的视角，对当前系统的代码质量、结构风险和成熟度做一次收口审计。目标只有三个：

1. 找出最可能让 demo 失败、出丑或产生错误结论的问题。
2. 把“今晚必须修”和“今晚不要碰”的东西分开。
3. 明确系统现在到底是“半成品的哪里”，避免继续误把结构债当成临场小 bug。

审计范围：

- `adjutant/`
- `kernel/`
- `task_agent/`
- `world_model/`
- `experts/`
- `openra_api/`
- `queue_manager.py`
- `game_loop/`
- `main.py`
- `web-console-v2/`
- `ws_server/`
- `logging_system/`

## 1. 总体判断

当前系统已经不是“完全不能演示”的状态，而是一个**可演示但仍处于混合过渡架构**的系统。

优点很明确：

- 旧 NLU 前半段已经接回 runtime。
- Expert / WorldModel / Kernel / WS / 日志链已经形成基本闭环。
- 可观测性明显强于项目中前期状态。

但问题也很明确：

- 共享生产/共享资源善后还不稳。
- stale world 时系统仍可能继续基于旧状态给出错误反馈。
- 多 TaskAgent 高层脑的结构问题仍在，只是被一层层补丁压住了。

所以明天 demo 的正确策略不是“假装系统已经全都成熟”，而是：

- 选窄闭环演示
- 修掉真正会爆雷的 P0/P1
- 把未收口点说成“已识别并正在 Capability 化/中央化”

## 2. 今晚必须优先看的 P0 / P1

## P0-1：`EconomyJob` 没有检查 `GameAPI.produce()` 的失败返回值

位置：

- `experts/economy.py:176-183`
- `openra_api/game_api.py:376-406`

现象：

- `GameAPI.produce()` 在 `COMMAND_EXECUTION_ERROR` 时会返回 `None`
- 但 `EconomyJob.tick()` 完全不检查返回值
- 仍然无条件 `self.issued_count += batch`

后果：

- 生产命令可能实际上没压进游戏队列
- 但 job 内部账本却认为“已经发出”
- 后面就可能出现：
  - produced/issued 不一致
  - 虚假进度
  - 卡住不收口
  - 最终错误 success/partial

demo impact：

- 这是最危险的 production-chain 逻辑 bug 之一。
- 一旦 live 里某次生产因为队列/前置/异常没真正执行，这个 job 仍会沿着错误账本继续往下跑。

判断：

- **P0**

## P0-2：build job 取消时，当前完全不清共享建造队列

位置：

- `experts/economy.py:109-111`
- `experts/economy.py:590-594`

现象：

- `EconomyJob.abort()` 会调用 `_cleanup_queue_on_abort()`
- 但这个函数现在是 `pass`

后果：

- task 漂移 / 中止 / 覆盖后，已经压进 shared `Building` queue 的建筑项会残留
- 这会直接造成你们最近现场反复看到的现象：
  - 队列里卡 ready building
  - waiting item 越积越多
  - 当前 task 已经没了，但游戏还背着旧任务的建筑债

demo impact：

- 这类残留会让后续任何建造命令都变得不可信
- 非常容易造成“为什么又在建兵营/为什么不放置/为什么后面都堵了”

判断：

- **P0**

备注：

- 这条不是理论风险，而是你们 live 里已经踩过的根因之一。

## P0-3：world stale 时，只局部挡住了 deploy，其他命令仍可能基于旧状态继续走

位置：

- `game_loop/loop.py:308-354`
- `adjutant/adjutant.py:397-403`
- `adjutant/adjutant.py:825-844`

现象：

- `GameLoop` 已经能检测 `WorldModel.stale`
- stale 时会暂停 job，并发玩家通知
- `Adjutant` 对 deploy 路径做了 stale guard
- 但系统没有统一的“stale world 禁止继续做状态性命令判断”总闸

后果：

- 非 deploy 命令和部分 query/command 仍可能在 stale snapshot 上运行
- 这会直接造成：
  - “明明现在只有基地车，却说建造厂已存在”
  - 基于旧局面的错误建议或错误短路

demo impact：

- 这是 live 演示里非常伤观感的一类问题，因为用户能一眼看出系统在胡说。

判断：

- **P0**

## P1-1：`DeployExpert` 的成功验证条件过窄

位置：

- `experts/deploy.py:72-79`
- `experts/deploy.py:105-145`

现象：

- 现在 deploy 成功只接受一种证据：
  - 先记 pre-deploy 的 CY actor IDs
  - 再要求出现一个“新的” CY actor_id

问题：

- 如果 OpenRA 的真实 deploy 语义不是“新 actor id 出现”，而是原 actor 转换/替换为同 id 建筑，当前验证会误判失败
- 即使游戏内已经成功部署，也可能在 Expert 层被标为 fail/timeout

demo impact：

- 如果明天 demo 包含 `展开基地车`，这条是一个真实风险点
- 不是一定会炸，但炸了会非常难看，因为游戏里成功、系统却说失败

判断：

- **P1**

## P1-2：`ReconExpert` 的资源需求太弱，仍可能抓到不该去探图的单位

位置：

- `experts/recon.py:279-287`
- `kernel/core.py:1117-1145`

现象：

- `ReconJob.get_resource_needs()` 只写了：
  - `{"owner": "self"}`
- Kernel 现在已经默认排除了 building/static actor
- 但没有排除：
  - harvester
  - MCV

后果：

- 在某些局面里，Recon 仍有可能抓矿车或 MCV 去探图
- 这类错误不是每次都出现，但一出现就非常伤 demo

demo impact：

- 会直接让用户觉得系统“没有 RTS 常识”

判断：

- **P1**

## P1-3：`GameAPI` 的阻塞重试策略仍然可能让一条调用挂很久

位置：

- `openra_api/game_api.py:40-46`
- `openra_api/game_api.py:235-285`

现象：

- `SOCKET_TIMEOUT = 10s`
- `MAX_RETRIES = 3`
- 也就是说单次请求最坏可以拖到几十秒

后果：

- 即使你们已经把 GameLoop 的阻塞 I/O 放进 worker thread
- 某些 expert/job/world refresh 仍可能因为底层调用超时而拖很长

demo impact：

- 用户体感会是：
  - 没反应
  - 很久才有结果
  - 世界状态迟钝

判断：

- **P1**

## P1-4：`QueueManager` 是必要补丁，但它现在更像补洞器而不是完整治理方案

位置：

- `queue_manager.py:54-189`

现象：

- `QueueManager` 现在会监控 shared ready building
- 超时后 warn 或 auto_place

问题：

- 它没有真正的 job provenance
- 它是在“队列已经脏了”的前提下做善后
- 所以它非常有用，但不能被误当成生产架构已经正确

demo impact：

- 作为保底是好的
- 但不能指望它掩盖所有 shared queue 语义问题

判断：

- **P1**

## 3. 结构级问题，但今晚不要深挖

这些问题是系统半成品感的真正来源，但不适合在 demo 前夜做大修。

### S1：Task 仍然默认绑定独立高层脑

位置：

- `kernel/core.py:212-248`

问题：

- `create_task()` 仍然是“每个 task 一个 TaskAgent”
- 这意味着多脑结构没有根治

影响：

- 战略一致性依然是结构性弱点

今晚建议：

- **不要动大架构**
- 只在汇报里明确说 Capability/Commander 化正在推进

### S2：`TaskAgent` prompt 仍然过重，很多系统语义还没真正下沉

位置：

- `task_agent/agent.py:51-135`
- `task_agent/context.py:264-340`

问题：

- prompt 仍然同时承载：
  - 游戏知识
  - 行为约束
  - 完成规则
  - retry policy
  - query_world policy
  - task-player 通信 policy

影响：

- raw log 很长
- 决策边界仍不够硬
- 模型仍需要在太多“半结构化”语义里自己脑补

今晚建议：

- 不大改 prompt
- 只保证 demo 命令路径稳定

### S3：Info Expert 方向正确，但还太少

位置：

- `experts/info_base_state.py`
- `experts/info_threat.py`
- `world_model/core.py:509-650`

问题：

- 现在的信息 expert 只有很薄两层：
  - base_state
  - threat
- 还没有：
  - queue/prod 状态 expert
  - awareness/radar expert
  - tech gate expert
  - recovery/package expert

影响：

- LLM 仍然要从 raw facts 自己推很多东西

今晚建议：

- 不扩
- 只把它作为明天汇报里的明确下一步

## 4. 前端 / 可观测性 / demo 展示面

当前展示面已经比中前期强很多，但它还不是“产品级前端”，而是“够调试、够演示”的状态。

目前最强的优点：

1. `session_clear` 已经不是纯前端假清空  
2. `Diagnostics` 后开时可以历史回放  
3. 有 `Task Trace`、per-task log、session log，调试能力已经形成闭环  

仍需注意的现实：

- 这套前端仍以 debug/value-through-visibility 为主，不是 polished UX 为主
- 所以 demo 时应主动把观众带到：
  - Chat
  - Tasks
  - Diagnostics/Task Trace
- 不要试图用它伪装成已经完成的消费级 UI

## 5. 当前系统最强的 5 个优点

1. **旧 NLU 前半段接回 runtime**
   - 这让简单命令和安全复合命令不必默认掉进 LLM

2. **Runtime Facts / Information Plane 已成形**
   - 系统终于不再完全靠 LLM 猜

3. **Expert 知识开始结构化**
   - `experts/knowledge.py` 和 Economy/Recon/Combat 的知识输出方向是对的

4. **logging / trace / session persistence 已形成可复盘能力**
   - 这对 live hardening 至关重要

5. **Capability 化方向已经出现，不再是纯 Task 脑议会**
   - 即使还没完全做完，方向已经对了

## 6. 明晚 demo 的建议打法

不要把 demo 建在“最复杂命令一定全都稳”这个假设上。

更稳的打法是：

1. 先做一条干净闭环
   - `展开基地车`
   - `建造电厂`
   - `建造兵营`
   - `生产3个步兵`
   - `探索地图`
   - `战况如何`

2. 边演示边强调三点
   - NLU + direct route
   - Capability / Expert 执行
   - 可观测性与 task trace

3. 如果现场要解释“为什么不是所有复合命令都完美”
   - 直接说当前正在从多 Task brain 收敛到 Capability/Commander 架构
   - 这不是遮掩，而是实话，而且是正确方向

## 7. 今晚建议的严格优先级

### 今晚必须优先修

1. `EconomyJob` 检查 `produce()` 返回值，不要在失败时递增 `issued_count`
2. 给 build abort 补最小共享队列善后
3. stale world 时，至少对主要 command/query 入口加统一保护，不要继续基于旧状态给结论

### 今晚能修则修

4. `DeployExpert` 的成功验证条件更稳一点
5. `ReconExpert` 资源需求至少排除 harvester / MCV
6. 缩短或更可控地处理 `GameAPI` 超时

### 今晚不要碰

7. 大规模 Commander 重构
8. 全量 prompt 重写
9. 复杂复合任务的通用 phase planner
10. 大型 UI 重构

## 8. 结论

这套系统现在最准确的状态不是“烂尾”，也不是“已经成熟”，而是：

**一套已经具备真实 live 演示价值，但仍然带有明显过渡架构痕迹的半成品系统。**

对明天 demo 而言，真正危险的不是“功能不够多”，而是：

- 共享生产队列账本不一致
- world stale 后继续乱回答
- deploy/production 这类因果闭环不够硬

把这几条守住，明天就能演示出：

- 中文命令 → NLU/routing → Expert/capability → 游戏变化 → trace 可解释

这已经足够构成一个像样的成果展示。
