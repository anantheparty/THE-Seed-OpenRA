## Current
design.md 经 5 轮审计达到 Zero Blockers。等待用户审阅确认，然后进入实现阶段。

## 活跃文档（6 个）
- `design.md` — 唯一的设计主文档（架构、对象模型、接口、决策）
- `user_requirements.md` — 用户需求 + 已确认决策 + 开放问题
- `code_asset_inventory.md` — 代码资产盘点（keep/reference/rewrite/delete）
- `agents.md` — 知识库
- `plan.md` — 任务状态
- `progress.md` — 进度日志

## 归档文档（archive/，调查过程产物）
- architecture_analysis.md, architecture_v2.md
- yu_investigation_report.md
- expert_contracts_draft.py
- intel_merge_analysis.md
- rts_ai_research.md
- dashboard_audit.md

## Queue

### A. 待用户审阅确认
1. 审阅 design.md — 确认对象模型、Kernel 接口、Expert 契约、WorldModel 设计
2. 确认 Constraint 作为"活跃修饰器"的定位
3. 确认看板三区分离方案

### B. 实现准备（用户确认后）
4. 清理可删除代码（standalone launchers/demos）
5. main.py 拆分方案
6. 新项目结构设计（目录组织）
7. 看板技术栈选型

### C. Phase 1 实现（用户说开工后）
8. 对象模型 Python dataclass 实现
9. Kernel 仲裁器骨架
10. WorldModel facade
11. 一个最简 ExecutionExpert 示例
12. 结构化日志骨架
13. 看板 Task 面板

## Blocked
- 无

## 分工
- wang: 架构审查、文档维护、用户沟通、设计决策
- yu: 代码清理、实现执行、技术调研
