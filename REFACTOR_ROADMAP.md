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
- ❌ FSM 状态机 → 移至 `legacy/`
- ❌ Plan 节点 → 移至 `legacy/`
- ❌ Review 节点 → 移至 `legacy/`
- ❌ Commit 节点 → 移至 `legacy/`
- ❌ NeedUser 节点 → 移至 `legacy/`
- ❌ 复杂的多步骤 plan 系统 → 移至 `legacy/`

---

## Phase 1: 创建简化核心结构 ✅
- [x] 1.1 创建新的 `CodeGenNode` - 单一代码生成节点
- [x] 1.2 创建简化的 `SimpleExecutor` - 直接执行流程
- [x] 1.3 创建新的统一 prompt

## Phase 2: 重构 main.py ✅
- [x] 2.1 移除 FSM 循环
- [x] 2.2 实现：输入 → 观测 → 代码生成 → 执行 流程
- [x] 2.3 保持 Dashboard 兼容性
- [x] 2.4 保留旧版本为 `main_legacy.py`

## Phase 3: 清理 the-seed 子模块 ✅
- [x] 3.1 移动未使用的节点文件到 `legacy/`
- [x] 3.2 简化 `core/__init__.py`
- [x] 3.3 修复 legacy 模块的 import 路径
- [x] 3.4 添加向后兼容的懒加载

## Phase 4: 更新配置 [待定]
- [ ] 4.1 简化 config schema（单一 model 配置即可）
- [ ] 4.2 更新 config manager（可选）

## Phase 5: 测试与验证 [待定]
- [ ] 5.1 基本流程测试（需要 OpenRA 环境）
- [ ] 5.2 Dashboard 集成测试

---

## 新架构设计

```
玩家输入
    ↓
[Observe] 获取游戏状态 (OpenRAEnv.observe)
    ↓
[CodeGenNode] LLM 生成 Python 代码
    ↓
[Execute] 执行代码 (SimpleExecutor)
    ↓
返回结果给玩家
```

## 文件结构变化

```
the_seed/
├── core/
│   ├── __init__.py          # 新架构导出
│   ├── executor.py          # SimpleExecutor（新）
│   ├── codegen.py           # CodeGenNode（新）
│   └── legacy/              # 旧架构（保留向后兼容）
│       ├── __init__.py
│       ├── blackboard.py
│       ├── excution.py
│       ├── factory.py
│       ├── fsm.py
│       ├── prompt.py
│       └── node/
│           ├── base.py
│           ├── observe.py
│           ├── plan.py
│           ├── action_gen.py
│           ├── review.py
│           ├── commit.py
│           └── need_user.py
├── config/
├── model/
└── utils/
```

## 使用方式

### 新架构（推荐）
```python
from the_seed.core import CodeGenNode, SimpleExecutor, ExecutorContext

# 创建执行器
codegen = CodeGenNode(model)
ctx = ExecutorContext(api=mid.skills, observe_fn=env.observe, ...)
executor = SimpleExecutor(codegen, ctx)

# 执行命令
result = executor.run("展开基地车，造一个电厂")
print(result.message)
```

### 旧架构（已废弃，保留兼容）
```python
# 会触发 DeprecationWarning
from the_seed.core import FSM, NodeFactory, ...
```

## 运行方式

```bash
# 新版本（简化）
python main.py

# CLI 模式测试
python main.py --cli

# 旧版本（如需要）
python main_legacy.py
```
