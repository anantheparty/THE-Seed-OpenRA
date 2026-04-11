# Demo 代码质量与结构风险审计

日期：2026-04-06  
作者：yu  
范围：`adjutant/`、`kernel/`、`task_agent/`、`world_model/`、`models/`、`experts/`、`openra_api/`、`game_loop/`、`main.py`、`ws_server/`、`web-console-v2/`

## 0. 结论先说

当前系统不是“不可演示的半成品”，而是：

- **基础链路已经能演示**
- **简单命令路径相对稳**
- **复杂 managed-task 路径仍然脆**
- **代码结构上处于明显的过渡态**

我本轮针对核心用例跑了主回归：

- `tests/test_adjutant.py`
- `tests/test_adjutant_routing.py`
- `tests/test_task_agent.py`
- `tests/test_kernel.py`
- `tests/test_world_model.py`
- `tests/test_economy_expert.py`
- `tests/test_recon_expert.py`
- `tests/test_ws_and_review.py`

结果：`191 passed in 19.65s`

所以现在的判断不是“系统已经烂到不能演示”，而是：

**明天 demo 可以做，但必须控制在简单稳定闭环上；复杂复合指令和自由 managed-task 仍然是最大风险源。**

## 1. P0 / P1 风险列表

## P0-1：语义相近命令仍可能走完全不同的生命周期

位置：

- `adjutant/adjutant.py:228-257`
- `adjutant/adjutant.py:567-597`

问题：

- 当前路由优先级是：
  - runtime NLU
  - rule
  - LLM classification
- 经济类命令只有落到 `_handle_command()` 里时，才会尝试 `_try_merge_to_capability()`
- 但如果它先被 runtime NLU/rule 直接吃掉，就会变成“立刻建 task/job”，只做 `_notify_capability_of_nlu(...)`

这意味着：

- 两句语义相近的话，可能一条 merge 给 `EconomyCapability`
- 另一条却直接起独立 `EconomyExpert` task

demo impact：

- 用户会感觉系统“同一个意思，不同说法，行为完全不一样”
- 这在 live 演示里很容易让人质疑系统到底有没有统一控制面

建议：

- demo 时尽量统一用已经验证过的 phrasing
- 不要现场随机换说法验证“语义等价性”

## P0-2：managed-task 的 prompt/context 仍然很重，长任务 latency 和漂移风险高

位置：

- `task_agent/agent.py:441-457`
- `task_agent/agent.py:481-601`
- `task_agent/agent.py:906-919`
- `task_agent/agent.py:943-950`

问题：

- 每轮 wake 都会重新注入完整 system prompt
- 再叠加 conversation window 中保留的历史
- 每次 tool call 后又会 append 一次 fresh context
- `llm_input` 日志也完整记录所有消息

这本身不是 bug，但会导致：

- context 很长
- 增量信息被淹没
- 复合任务延迟高
- managed-task 发生“明明在动，但用户感觉卡住”

demo impact：

- 复杂命令可能明显慢
- 且慢的不只是模型 API，而是 prompt/context 组织本身

建议：

- 明天 demo 不要把成败压在复杂复合命令上
- 只演简单 NLU/rule 直达命令 + 一个受控的 managed task 查询案例

## P1-1：当前 runtime facts 仍不足以支撑复杂 task 的稳定因果判断

位置：

- `world_model/core.py:509-650`
- `task_agent/context.py:171-193`
- `task_agent/agent.py:61-124`

问题：

- 现在已经有 `runtime_facts`，这是正确方向
- 但还缺少很多对复杂任务真正关键的“决策事实”，例如：
  - 当前阶段 / phase
  - 上一步动作是否明确验证成功
  - 某类失败签名的重复次数
  - 某类动作是否应禁止继续重试
  - 某能力是否由别的 task/capability 正在占有

所以虽然 prompt 里已经写了很多 guard，但 LLM 仍要自己从上下文里推断因果。

demo impact：

- 复杂 task 容易“看起来有道理，但实际跑偏”
- trace 能解释它为什么偏，但不能保证它不偏

建议：

- 明天 demo 把重点放在“系统已经有 runtime facts/info experts，因此方向正确”
- 不要把复杂多阶段任务稳定性讲得过满

## P1-2：Task / Job / Expert 的界面可见性仍然不够清晰

位置：

- `kernel/core.py:212-248`
- `main.py:525-560`
- `web-console-v2/src/components/TaskPanel.vue:22-31`

问题：

- 对代码来说，概念其实已经分开：
  - Expert 是类型
  - Job 是实例
  - Task 是容器
- 但对 UI 来说，这层区分还不够直观
- `TaskPanel` 虽然已经显示 jobs/expert_type，但仍偏“调试可读”，不算“用户一眼就懂”

demo impact：

- 一旦 task 内跑出多个 job，非开发者很容易搞不懂系统到底在做什么

建议：

- demo 时尽量选“一条 task 只触发一个 expert/job”的用例
- 复杂链路用 `Diagnostics -> Task Trace` 解释，不强行让 `TaskPanel` 承担全部表达

## P1-3：对用户的消息通道仍然分裂

位置：

- `main.py:376-421`
- `main.py:462-483`
- `web-console-v2/src/components/ChatView.vue:222-255`

问题：

- 用户可见消息现在分三类：
  - `query_response`
  - `player_notification`
  - `task_message`
- 技术上都打通了
- 但从用户感知上，这仍然不是一个完全统一的话语面

demo impact：

- 用户可能会问：为什么这句在“副官回复”，那句在“任务消息”，还有一句在“通知”
- 系统明明在做事，但主聊天流不一定连续

建议：

- demo 时主动解释：副官主回复 + 任务过程消息 + 系统通知是三层信息
- 不要等观众自己去理解这个差异

## P1-4：RuntimeBridge 直接读取 Kernel 私有字段，结构上脆

位置：

- `main.py:187-216`

问题：

- `RuntimeBridge.sync_runtime()` 直接访问：
  - `kernel._task_runtimes`
  - `kernel._jobs`
- 这是明显的层间穿透

短期它能跑，长期会带来：

- 生命周期 bug 更难定位
- session_clear/reset 后状态同步依赖隐式约束

demo impact：

- 明天不太会直接炸
- 但这是典型“结构上不干净”的地方

建议：

- 作为欠缺点记录，不建议今晚动它

## P1-5：Diagnostics 虽然增强了，但前端内存窗口仍然有限

位置：

- `web-console-v2/src/components/DiagPanel.vue:156-171`
- `web-console-v2/src/components/DiagPanel.vue:104-115`

问题：

- trace 只保留 800 条
- log 只保留 500 条
- 这对长 session 或反复调试仍然不够

好消息是：

- 现在后台已经有按 session / task 的落盘日志

坏消息是：

- 前端 Diagnostics 还不是一个真正的“从磁盘历史读取”的调试台

demo impact：

- 短时演示够用
- 长时间调试中途开面板或重复操作，前端仍可能丢早期可视上下文

建议：

- demo 期间用新 session
- 不要让一个 session 持续堆很久

## P2-1：知识层已有雏形，但仍含未核实映射

位置：

- `experts/knowledge.py:75`
- `experts/knowledge.py:158`
- `experts/knowledge.py:171`
- `experts/knowledge.py:179`

问题：

- 当前知识层方向对了
- 但仍残留未核实项，例如：
  - opening 细节
  - counter table
  - `e4` / `arti` 这类注释里仍写着 TODO

demo impact：

- 如果明天只演基础建造/侦察/查询，影响不大
- 如果现场讲“我们已经有完整战术知识库”，会说过头

建议：

- 明天把它讲成“结构化知识层已经建立并开始用于 Expert，不是已经覆盖全部 RTS doctrine”

## 2. 当前最强的 5 个优点

### 1. 旧 NLU 前半段已经回到主 runtime

位置：

- `adjutant/runtime_nlu.py`
- `adjutant/adjutant.py:228-242`

这件事非常重要，因为它意味着：

- 系统已经不是“全靠 LLM 自由理解”
- shorthand、安全复合序列、query_actor 等都能稳定前置路由

这是明天 demo 最值得强调的成熟点之一。

### 2. runtime facts + info experts 方向是对的

位置：

- `world_model/core.py:509-650`
- `experts/info_base_state.py`
- `experts/info_threat.py`

这表明系统已经开始从：

- “模型自己猜世界”

转向：

- “系统先把世界编译成可决策语义，再喂给模型”

这点和近年的 LLM+RTS 论文方向高度一致。

### 3. TaskAgent 已经有结构化对玩家发话能力

位置：

- `task_agent/handlers.py:299-328`
- `main.py:376-421`

这说明系统已经不再是“task 只能闷头跑 job”，而是开始具备：

- `task_info`
- `task_warning`
- `task_question`
- `task_complete_report`

虽然用户面还有分裂，但方向是对的，而且已经落代码了。

### 4. 日志与 trace 已经进入“能做 postmortem”的阶段

位置：

- `logging_system/`
- `main.py` runtime replay 路径
- task/session log files

现在不是只有控制台 print 了，而是：

- 有 session log
- 有 per-task log
- 有 Task Trace
- 有 Diagnostics replay

这意味着系统已经具备真正工程化迭代的基础。

### 5. 当前关键回归集整体是稳的

本轮主回归结果：

- `191 passed`

这不代表 live 不会有坑，但至少说明：

- 当前系统不是“连基本行为都没有被锁住”
- 很多核心链路已经有回归护栏

## 3. 明天 demo 的建议边界

## 必须主打

- `展开基地车`
- `建造电厂`
- `建造兵营`
- `生产3个步兵`
- `探索地图`
- `战况如何`

## 可以讲，但别压成主 show

- Capability
- Information Expert
- Runtime Facts
- Task Trace / per-task log

## 不要主打

- 长复合战略命令
- 多 task 并发协同很强的故事
- “已经是完整 AI 对手”
- “已经有完整 RTS 常识库”

## 4. 现在还欠缺，但可以明说

这些点可以直接当作“下一步”讲，而不是藏着：

1. 复杂 managed-task 的 phase policy 还不够强
2. Capability 化还在推进中，尚未覆盖所有域
3. 信息 Expert 还只是第一批，不是完整世界知识层
4. 用户消息面仍在统一中
5. 前端 Diagnostics 还没有完全磁盘级历史回放

## 5. 最终判断

如果明天 demo 目标是：

- 展示这套系统已经形成了“NLU + Capability/Expert + Runtime Facts + 可观测性”的成熟主干

那么它是可以站住的。

如果明天 demo 目标是：

- 证明这已经是一个稳定、完整、强战略的全自动红警 AI

那会说过头。

更准确的表述应该是：

**这是一套已经进入可演示、可调试、可持续硬化阶段的 OpenRA 智能副官系统；简单命令链路已相对成熟，复杂长链策略仍在收口。**
