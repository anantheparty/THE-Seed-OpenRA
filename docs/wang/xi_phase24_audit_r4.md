# xi Phase 2-4 最终回归审计（749e156）

审计目标：验证上轮最后 1 个 blocker 是否关闭，也就是 `T2` 是否不再走 `_ManagedJob` fallback，而是进入真实 `EconomyExpert + EconomyJob` 路径。

结论：**zero blockers。**

这次修复把上轮最后一个 blocker 关掉了。`tests/test_e2e_experts.py` 的 `make_kernel()` 现在已经注册 `EconomyExpert`，`T2` 不再落到 live Kernel 的 `_ManagedJob` fallback，而是创建真实 `EconomyJob`。

## 已关闭

### 1. `T2` 现在进入真实 `EconomyExpert + EconomyJob`

涉及：
- `tests/test_e2e_experts.py:31-35`
- `tests/test_e2e_experts.py:40-65`
- `tests/test_e2e_experts.py:127-143`
- `tests/test_e2e_experts.py:157-179`

这次 commit 做了两件关键事：
- 引入 `from experts.economy import EconomyExpert`
- 在 `make_kernel()` 的 `expert_registry` 中注册 `"EconomyExpert": EconomyExpert(game_api=game_api, world_model=world)`

同时，测试用 `MockGameAPI` 也补了 Economy 路径需要的接口：
- `can_produce(...)`
- `produce(...)`

我本地重新跑了 live repro，结果是：
- `jobs[0].expert_type == "EconomyExpert"`
- 底层 controller class == `EconomyJob`

这说明 `T2` 现在验证的已经是：
- `start_job("EconomyExpert", ...)`
- `Kernel._make_job_controller(...)`
- `EconomyExpert.create_job(...)`
- 真实 `EconomyJob` controller 实例化

而不再是之前那条“`expert_type` 看起来对，但底层其实是 `_ManagedJob`”的假绿路径。

## 本地验证

我跑了：
- `python3 tests/test_e2e_experts.py`
- `python3 tests/test_economy_expert.py`

另外做了 1 组 live repro：
- 直接复现 `T2` 的 `start_job("EconomyExpert", ...)` 路径，确认底层 controller 实际类型是 `EconomyJob`

## 非阻塞说明

`tests/test_e2e_experts.py` 仍然不是“完全真实 runtime”的端到端：
- 仍使用测试私有 `RecordingAgent`
- 仍直接调用 `agent.tool_executor.execute(...)`
- 仍没有真实 `TaskAgent.run()` / wake loop，也没有 `GameLoop`

但这次 Wang 要我确认的 blocker 是：
- `T2` 是否还在走 `_ManagedJob` fallback

按这个回归目标，这个 blocker 已经明确关闭，因此不再构成 Phase 2-4 的阻塞项。

## 结论

这轮可以清成 `zero blockers`。

按我的审计口径，**Phase 2-4 现在可以全部关闭。**
