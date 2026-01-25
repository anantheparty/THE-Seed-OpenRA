# THE-Seed Refactor Roadmap

## 目标
将复杂的 FSM 多节点架构简化为单一的 CodeGen 节点：
- **输入**: 玩家语句
- **处理**: LLM 直接生成 Python 代码
- **输出**: 执行代码并返回结果

## 保留
- ✅ Job 系统 (`openra_api/jobs`)
- ✅ Observe 功能（游戏状态观测）
- ✅ OpenRA API 和 MacroActions
- ✅ Dashboard Bridge（WebSocket 通信）
- ✅ Model Adapter（LLM 调用）

## 移除
- ❌ FSM 状态机（`fsm.py`）
- ❌ Plan 节点
- ❌ Review 节点
- ❌ Commit 节点
- ❌ NeedUser 节点
- ❌ 复杂的多步骤 plan 系统

---

## Phase 1: 创建简化核心结构 [进行中]
- [ ] 1.1 创建新的 `CodeGenNode` - 单一代码生成节点
- [ ] 1.2 创建简化的 `SimpleExecutor` - 直接执行流程
- [ ] 1.3 创建新的统一 prompt

## Phase 2: 重构 main.py
- [ ] 2.1 移除 FSM 循环
- [ ] 2.2 实现：输入 → 观测 → 代码生成 → 执行 流程
- [ ] 2.3 保持 Dashboard 兼容性

## Phase 3: 清理 the-seed 子模块
- [ ] 3.1 移除未使用的节点文件
- [ ] 3.2 简化 factory.py
- [ ] 3.3 精简 blackboard.py
- [ ] 3.4 清理 prompt.py

## Phase 4: 更新配置
- [ ] 4.1 简化 config schema（只需一个 model 配置）
- [ ] 4.2 更新 config manager

## Phase 5: 测试与验证
- [ ] 5.1 基本流程测试
- [ ] 5.2 Dashboard 集成测试

---

## 新架构设计

```
玩家输入
    ↓
[Observe] 获取游戏状态
    ↓
[CodeGenNode] LLM 生成 Python 代码
    ↓
[Execute] 执行代码
    ↓
返回结果给玩家
```

## 预期文件结构变化

```
the_seed/
├── core/
│   ├── __init__.py
│   ├── executor.py      # 简化的执行器（新）
│   ├── codegen.py       # CodeGenNode（新）
│   ├── blackboard.py    # 精简版
│   └── prompt.py        # 简化的 prompt
├── config/
├── model/
└── utils/
```
