# xi Phase 2-4 最终回归审计（47a783d）

审计目标：验证上轮剩余的 1 个 blocker + 1 个 should-fix 是否关闭。

结论：**不是 zero blockers。**

这次修复**确实关闭了通知 retry 的 should-fix**，但 `3.5` 的里程碑级 E2E 仍然**没有完全关掉**。测试文件比上一轮更接近真实运行时了，但 `T2` 仍然没有真正跑到 `EconomyExpert`，所以这组测试还不能证明 `T2-T8` 全部都在共享主链上可用。

## 已关闭

### 1. `NotificationManager` 的 retry 语义已修正

涉及：
- `adjutant/notifications.py:106-147`

这版改成了按 append-only 通知列表的**索引集合**记录已成功推送项：
- `self._pushed_indices: set[int]`
- 仅在 sink 成功后把当前 `idx` 写入集合
- 失败项不会被标记成功，因此下一次 `poll_and_push()` 会重试该项
- 后续已经成功的项不会因为前面失败而被重复发送

我本地做了中间失败复现：
- 原始通知：`A, B, C`
- 第一次推送：`B` 故意失败
- 第二次推送：只重试 `B`

实际结果：
- 第一次后 `total_pushed == 2`
- 第二次只返回 `B`
- sink 调用序列是 `['A', 'B', 'C', 'B']`

这说明上轮的“跳过失败项、重复后续成功项”问题已经关闭。

## Remaining findings

### 1. Blocker — `tests/test_e2e_experts.py` 仍没有把 `T2` 跑到真实 `EconomyExpert`

涉及：
- `tests/test_e2e_experts.py:1-5`
- `tests/test_e2e_experts.py:96-117`
- `tests/test_e2e_experts.py:120-135`
- `tests/test_e2e_experts.py:149-171`
- `kernel/core.py:565-580`

这次 `tests/test_e2e_experts.py` 的确比上一轮真实很多：
- 不再使用旧的 `MockKernel`
- 现在创建的是真实 `Kernel`
- `Movement / Deploy / Combat` 也都注册成了真实 Expert

但 `3.5` 还不能清成 milestone-grade E2E，原因有两层。

第一层，文件自己的 claim 还比实际更强：
- 文件头写的是“`real Kernel + real Experts + real TaskAgent`”和“`full pipeline from LLM tool_use through Expert Job execution`”
- 但实际 `task_agent_factory` 传入的是测试私有的 `RecordingAgent`
- 各测试也是直接调用 `agent.tool_executor.execute(...)`
- 文件里没有真实 `TaskAgent.run()` / wake loop，也没有 `GameLoop`

第二层，也是这轮仍然构成 blocker 的关键点：
- `make_kernel()` 只注册了 `MovementExpert`、`DeployExpert`、`CombatExpert`
- **没有注册 `EconomyExpert`**
- `T2` 仍然通过 `start_job("EconomyExpert", ...)` 启动作业
- live Kernel 在 `kernel/core.py:565-580` 里对“未注册 expert”会回退到 `_ManagedJob`

我本地复现了这个路径，结果是：
- `jobs[0].expert_type == "EconomyExpert"`
- 但底层 controller class 实际是 `_ManagedJob`

这意味着：
- `T2` 现在验证的是 “Kernel 接受了一个 `expert_type=EconomyExpert` 的 job 启动请求”
- **不是** “真实 `EconomyExpert + EconomyJob` 已经进入共享运行时并可工作”

按 Wang 要求的“确认 `xi E2E 重写` 是否闭环”口径，这个 blocker 还在。

## 本地验证

我跑了：
- `python3 tests/test_e2e_experts.py`
- `python3 tests/test_adjutant.py`
- `python3 tests/test_adjutant_routing.py`

另外做了两组 live repro：
- `T2` 的 `EconomyExpert` 启动路径复现，确认底层 controller 实际是 `_ManagedJob`
- `NotificationManager` 中间失败重试复现，确认失败项会被单独重试、后续成功项不会重复

## 结论

这轮不能清成 `zero blockers`。

当前状态应当是：
- 已关闭：`NotificationManager` retry 语义
- 未关闭：`3.5` E2E 真实性 blocker，具体是 `T2` 仍未跑到真实 `EconomyExpert`
