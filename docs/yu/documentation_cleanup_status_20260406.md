# Documentation Cleanup Status — 2026-04-06

作者：yu  
目的：给当前文档状态一个清晰的“哪些可直接用、哪些要改、哪些只是未来目标”的整理结果。

## 1. 当前可直接作为执行参考的文档

### Runtime / demo /缺陷收口

- `docs/yu/demo_code_audit_20260406.md`
- `docs/yu/demo_system_audit_20260406.md`
- `docs/yu/demo_system_code_audit_20260406.md`
- `docs/yu/pending_drift_fixes.md`

这些文档用于：
- demo 风险判断
- 当前 runtime 缺口收集
- 后续 bug / drift 收口

### Wang 架构纠偏

- `docs/yu/wang_design_v_next_correction_20260406.md`

当前它应被视为：
- 近期开发主线纠偏参考
- 对 `docs/wang/design_v_next.md` 的执行层纠正

### TaskAgent / prompt / trace 相关分析

- `docs/yu/task_agent_prompt_runtime_report.md`
- `docs/yu/task001_trace_analysis.md`

这些文档用于：
- managed-task 失败链分析
- prompt / context / runtime semantics 问题定位

## 2. 当前应视为“目标态草案”，不要直接当执行蓝图的文档

- `docs/wang/design_v_next.md`
- `docs/wang/capability_task_design.md`
- `docs/wang/adjutant_redesign.md`
- `docs/wang/architecture_crisis.md`

这些文档的价值是：
- 记录 Wang 的架构判断与收敛方向
- 提供未来目标态草案

这些文档当前**不应**直接等同于：
- 本周实现计划
- demo 前施工清单
- 当前 runtime 的事实描述

## 3. 当前最明显过时的顶层文档

以下文档应尽快重写：

- `README.md`
- `PROJECT_STRUCTURE.md`

过时原因：
- 仍把 legacy / next-gen 双架构当当前事实
- 仍描述 “LLM 生成 Python 再执行” 的旧链路
- 没有正确描述当前主 runtime：
  - `Adjutant`
  - `RuntimeNLU`
  - `Kernel`
  - `TaskAgent`
  - `Experts`
  - `WorldModel`
  - `web-console-v2`

## 4. Wang 文档侧建议整理动作

建议 Wang 处理：

1. 给 `docs/wang/design_v_next.md` 明确加注：
   - `future target / not current execution blueprint`
2. 把已过时但仍有参考价值的文档收进 `docs/wang/archive/` 或在正文顶部标记过渡态
3. 对 `design_v_next` 和 `capability_task_design` 做一次角色收口：
   - `Adjutant = top-level coordinator`
   - `Commander = future target`

## 5. Xi 文档侧建议整理动作

建议 Xi 处理：

1. `docs/xi/expert_redesign.md`
   - 标注哪些已落地
   - 哪些仍是未来工作
2. `docs/xi/full_audit_report.md`
   - 标出哪些结论已经被后续实现关闭
   - 避免它继续被误读成“当前代码事实”

## 6. Yu 自己名下文档整理原则

`docs/yu/` 保持以下分层：

- `*_audit_*.md`
  - 某轮审计/评估结果
- `*_report.md`
  - 某条问题的深挖报告
- `pending_drift_fixes.md`
  - 尚未处理的 confirmed gap 单一入口
- `plan.md / progress.md / agents.md`
  - 状态与长期知识库

后续原则：
- 新问题优先记进 `pending_drift_fixes.md`
- 不再散落多个“待处理”文档
- 架构纠偏类只保留一份主文档，避免重复写多个版本

## 7. 当前文档整理结论

当前最需要执行的文档动作不是“再写更多设计”，而是：

1. 重写顶层 `README.md`
2. 重写 `PROJECT_STRUCTURE.md`
3. Wang / Xi 给各自核心设计文档补“当前/目标/已落地/未落地”标识
4. 继续把未处理项统一收进 `docs/yu/pending_drift_fixes.md`

这四件事做完之后，文档就不会再继续制造第三套现实。
