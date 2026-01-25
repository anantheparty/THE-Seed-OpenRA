# THE-Seed OpenRA Agent & Dashboard

本项目是一个基于 `the-seed` 框架构建的 OpenRA 智能体，并配备了基于 Makepad 的高性能 Rust 可视化 Dashboard。

## 🆕 新简化架构

**v2.0** 采用全新的简化架构，移除了复杂的 FSM 状态机：

```
玩家输入 → 观测游戏状态 → LLM 生成代码 → 执行 → 返回结果
```

核心组件：
- **CodeGenNode**: 单一代码生成节点，接收指令直接生成 Python 代码
- **SimpleExecutor**: 简化执行器，处理整个流程

详见 [REFACTOR_ROADMAP.md](./REFACTOR_ROADMAP.md)

## 📋 目录
- [环境要求](#环境要求)
- [快速启动](#快速启动)
- [项目结构](#项目结构)
- [使用方式](#使用方式)

## 环境要求

### 基础环境
- **OpenRA**: 需要安装并运行 OpenRA（推荐使用配合本项目的版本），并确保开启外部 API 支持（默认端口 `7445`）。

### Python 环境 (智能体)
- **uv**: 极速 Python 包管理器。
    - 安装方式 (Windows): `powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"`
    - 安装方式 (Linux/Mac): `curl -LsSf https://astral.sh/uv/install.sh | sh`

### Rust 环境 (Dashboard)
- **Rust**: 需要安装 Rust 编程语言。
- **Nightly Toolchain**: Makepad 依赖 Rust Nightly 版本。

## 快速启动

### 1. 启动智能体 (Python 后端)

**Windows:**
```powershell
.\run.bat
```

**Linux / macOS:**
```bash
./run.sh
```

或直接运行：
```bash
python main.py
```

**CLI 测试模式**（无需 Dashboard）：
```bash
python main.py --cli
```

启动成功后，WebSocket Server 会在 `ws://127.0.0.1:8080` 监听 Dashboard 连接。

### 2. 启动可视化 Dashboard (Rust 前端)

保持 Python 智能体运行，打开新终端：

```bash
cd dashboard
cargo run
```

## 项目结构

```text
.
├── main.py                 # 智能体入口（新简化版）
├── main_legacy.py          # 旧版 FSM 入口（已废弃）
├── agents/
│   └── commander.py        # 指挥官代理构建器
├── adapter/
│   └── openra_env.py       # OpenRA 环境适配器
├── openra_api/             # OpenRA API 封装
├── the-seed/               # 核心框架子模块
│   └── the_seed/
│       ├── core/
│       │   ├── codegen.py      # 代码生成节点（新）
│       │   ├── executor.py     # 简化执行器（新）
│       │   └── legacy/         # 旧架构（已废弃）
│       ├── model/              # LLM 模型适配
│       ├── config/             # 配置管理
│       └── utils/              # 工具类
├── dashboard/              # 可视化前端 (Rust + Makepad)
├── test_simple.py          # 新架构测试
├── test_legacy.py          # 旧架构测试
├── run.bat                 # Windows 启动脚本
└── run.sh                  # Linux/Mac 启动脚本
```

## 使用方式

### 新架构（推荐）

```python
from the_seed.core import CodeGenNode, SimpleExecutor, ExecutorContext
from the_seed.model import ModelFactory
from the_seed.config import load_config

# 加载配置和模型
cfg = load_config()
model = ModelFactory.build("codegen", cfg.model_templates["default"])

# 创建执行器
codegen = CodeGenNode(model)
ctx = ExecutorContext(
    api=mid.skills,
    observe_fn=env.observe,
    api_rules=api_rules,
    runtime_globals=runtime_globals,
)
executor = SimpleExecutor(codegen, ctx)

# 执行命令
result = executor.run("展开基地车，造一个电厂")
print(result.message)
```

### 旧架构（已废弃）

```python
# 会触发 DeprecationWarning
from the_seed.core import FSM, NodeFactory
# ...
```

## 配置

配置文件位于 `the-seed/seed_config.yaml`，主要配置项：

```yaml
logging:
  logfile_level: debug
  console_level: info

model_templates:
  default:
    request_type: openai
    api_key: sk-xxx
    base_url: https://api.openai.com/v1
    model: gpt-4o-mini

node_models:
  action: default  # 代码生成使用的模型
```

## License

MIT
