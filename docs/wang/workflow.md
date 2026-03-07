# 开发工作流

## 角色

| 角色 | 职责 | 不做 |
|---|---|---|
| **wang** | 管理：任务拆解/分配、进度跟踪、概念漂移防控、文档维护、git 管理、与用户沟通 | 不写代码 |
| **yu** | 开发 + 审计 xi 的代码 | — |
| **xi** | 开发 + 审计 yu 的代码 | — |

## 设计文件冻结

以下文件**不再修改**，作为开发的参照基准：
- `docs/wang/design.md`
- `docs/wang/test_scenarios.md`
- `docs/wang/implementation_plan.md`

如果开发过程中发现设计需要调整 → 先与用户讨论 → 用户确认后由 wang 记录到 `docs/wang/dev_decisions.md`（新文件，开发期间的增量决策）。

## 任务生命周期

```
wang 拆解任务 → 分配给 yu 或 xi
    ↓
开发者实现（在 worktree 或分支上）
    ↓
开发者自测通过
    ↓
交叉审计（yu 审 xi 的代码，xi 审 yu 的代码）
    ↓
审计通过 → wang 确认无概念漂移 → 合并到 main
    ↓
wang 更新进度
```

## 任务拆解规则

- 每个任务对应 implementation_plan.md 中的一个编号（如 0.1、1.3a）
- 大任务（标记"大"或"中"）拆成可在一个 session 内完成的子任务
- 每个任务必须有：
  - 明确的输入（依赖哪些已完成的任务）
  - 明确的产出（文件路径 + 验收标准）
  - 分配给谁（yu 或 xi）

## Git 规范

### 分支
- 不分支，直接在 main 上工作
- **文件冲突防控**：三人共用一个工作目录，必须避免同时修改同一文件
  - wang 分配任务时明确列出每个任务涉及的文件
  - yu 和 xi 的任务不能涉及同一个文件
  - 需要改同一文件时 → 串行，不并行
- 每个任务完成后立即 commit，commit message 格式：

```
<type>(<scope>): <summary>

<body>

Task: <implementation_plan task id>
Author: <yu|xi>
Reviewed-by: <xi|yu>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

type: feat / fix / refactor / test / docs
scope: kernel / worldmodel / expert / task_agent / adjutant / dashboard / benchmark

### Commit 节奏
- 小步提交，不积攒大量改动
- 每个功能单元完成就提交
- wang 定期检查 git log 确保进度

## 交叉审计规则

| 开发者 | 审计者 |
|---|---|
| yu | xi |
| xi | yu |

审计检查清单：
1. **功能正确**：代码实现了任务描述的功能吗？
2. **概念一致**：和 design.md 的架构一致吗？有没有偏离？
3. **接口匹配**：数据模型、方法签名和 design.md 定义的一致吗？
4. **测试覆盖**：有对应的测试吗？测试通过吗？
5. **代码质量**：可读性、无硬编码、错误处理

审计结果：
- **通过** → 告知 wang → wang 确认后合并
- **需修改** → 列出具体问题 → 开发者修改 → 重新审计
- **概念漂移** → 立即通知 wang → wang 判断是否需要和用户讨论

## 进度跟踪

wang 维护 `docs/wang/dev_progress.md`：

```markdown
## Phase X: <名称>

### Task X.X: <描述>
- 分配给：yu/xi
- 状态：待开始 / 开发中 / 自测中 / 审计中 / 完成
- 审计者：xi/yu
- 开始时间：
- 完成时间：
- 备注：
```

每个任务状态变更时更新。

## 沟通协议

| 场景 | 方式 |
|---|---|
| wang 分配任务给 yu/xi | send_message，包含任务描述+验收标准+依赖 |
| 开发者完成开发 | send_message 给 wang 报告 + 给审计者发审计请求 |
| 审计者完成审计 | send_message 给 wang 报告结果 |
| 发现概念漂移 | send_message 给 wang，priority=high |
| 开发者遇到阻塞 | send_message 给 wang，描述问题 |
| wang 进度汇报 | send_message 给用户 |

## 概念漂移防控

wang 在以下时机检查概念漂移：
1. **每次审计通过后**：合并前 wang 快速检查关键接口是否和 design.md 一致
2. **每个 Phase 完成时**：全面检查已实现的组件是否和 design.md 对齐
3. **开发者提出"设计不合理需要改"时**：评估是真的需要改设计还是理解有误

漂移信号：
- 数据模型字段和 design.md 不同
- 组件职责边界和 design.md 不同
- 三种 Expert 类型被合并或混淆
- Task Agent 做了 Expert 该做的事（或反过来）
- Job 的 GameAPI 调用被加了中间层

## 质量门禁

### Milestone 1 (2.3) 门禁
- [ ] T1 测试场景全流程通过（mock GameAPI）
- [ ] Benchmark 数据可查（每步耗时）
- [ ] 日志可查
- [ ] 代码 100% 交叉审计

### Milestone 2 (3.5) 门禁
- [ ] T1-T8 测试场景通过
- [ ] 五种 Execution Expert 工作
- [ ] Expert 设计文档和实现一致

### Milestone 3 (4.5) 门禁
- [ ] T1-T11 测试场景通过
- [ ] Adjutant 路由正确
- [ ] 看板可用

### Milestone 4 (7.3) 门禁
- [ ] 全量端到端 + benchmark 基线
- [ ] Live GameAPI 测试通过

## 第一批任务（Phase 0）

准备分配的任务：

| 任务 | 分配 | 审计 | 产出 |
|---|---|---|---|
| 0.1 删除可删代码 | yu | xi | 干净代码库 |
| 0.1b 移除 the-seed | yu | xi | NLU 规则迁出 + 子库删除 |
| 0.2 数据模型 dataclass | xi | yu | `models/` 包 |
| 0.3 项目目录结构 | xi | yu | 目录骨架 |
| 0.4 LLM 模型抽象层 | xi | yu | `llm/provider.py` |
| 0.5 Benchmark 框架 | yu | xi | `benchmark.py` |

yu 和 xi 可以并行工作：yu 做清理 (0.1, 0.1b, 0.5)，xi 做搭建 (0.2, 0.3, 0.4)。
