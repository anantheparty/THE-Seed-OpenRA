# THE-Seed OpenRA Agent 项目结构说明

## 1. 项目概览

本项目是一个基于 `the-seed` 框架构建的 OpenRA 智能体，旨在实现 AI 在红色警戒游戏中的自主决策与操作。
目前项目处于**架构演进**阶段，包含两个主要部分：
1.  **Legacy Framework**: 基于 `the-seed` 框架的早期实现（FSM 驱动）。
2.  **Next-Gen Modules**: 正在开发中的新一代独立智能体模块（战略、战术、运营），目前独立运行，未来将接入框架。

## 1.1 特别提示 (AI 开发者必读)

⚠️ **请仔细阅读以下内容，避免无效的上下文消耗和错误的方向：**

1.  **忽略 `OpenCodeAlert/`**:
    *   这是 **OpenRA 游戏本体的 C# 源码**，体积非常庞大。
    *   我们正在开发的是**外部智能体 (Python)**，通过 Socket API 与游戏通信，**不需要也不应该**阅读游戏源码。
    *   **切勿**尝试索引或修改该目录下的文件。

2.  **暂时忽略 `the-seed/`**:
    *   这是底层的通用 Agent 框架。
    *   由于该框架目前正处于频繁重构阶段，为了避免依赖不稳定的接口，我们当前的开发工作主要集中在 **Next-Gen Modules (`agents/`)**。
    *   未来当 `the-seed` 框架稳定后，我们会进行统一接入。

3.  **遵循“模块化”开发原则**:
    *   鉴于上述原因，我们在开发 `agents/` 下的各个专家模块（如 `combat`, `strategy`, `economy`）时，必须严格遵循**独立性**原则。
    *   **Readme 规范**: 每个模块都必须包含详细的 `README.md`，明确定义其**输入 (Input)**、**输出 (Output)** 和**对外接口**。
    *   **目的**: 确保当未来接入新版 `the-seed` 框架时，只需编写简单的 Adapter 即可集成，而无需重写核心逻辑。

## 2. 核心架构

### 2.1 Legacy Framework (Based on `the-seed`)
*   **Agent Core (`main.py`)**: 旧版智能体入口，使用 FSM 驱动。
*   **Framework (`the-seed/`)**: 基础框架 submodule。
*   **Visualization (`web-console/`)**: Web 控制台监控面板（Rust dashboard 已移除）。
*   **Multimodal (`uni_mic/`)**: 语音交互层。

### 2.2 Next-Gen Independent Modules (Standalone)
这些模块是当前开发重点，设计为独立运行的微服务或 Agent，未来将集成到统一架构中：

*   **Combat Agent (`agents/combat/`)**: 战术专家。负责连队级的微操控制、阵地战与移动攻击。
*   **Economy Agent (`agents/economy/`)**: 运营专家。负责资源管理、建筑生产与科技攀升。
*   **Strategy Agent (`agents/strategy/`)**: 战略专家。负责全局战略规划、指挥战术与运营专家。
*   **Game State & Intel (`openra_state/`)**: 全局状态中心。负责情报聚合、地图分析与可视化，服务于所有 Agent。
*   **Tactical Core (`tactical_core/`)**: 独立战术算法核心。负责势场微操、协同回退与硬中断逻辑，可被上层模块集成或独立运行。

## 3. 目录结构详解

```text
D:\THE-Seed-OpenRA\
├── main.py                     # [Legacy] 旧版智能体入口
├── run.bat / run.sh            # [启动] 启动 Legacy Agent
│
├── adapter/                    # [Legacy] 旧版胶水层
│   └── openra_env.py           
│
├── openra_api/                 # [公共] 底层游戏交互库 (Socket Client)
│   ├── game_api.py             # 原始 JSON 通信
│   └── macro_actions.py        # 宏指令封装
│
├── agents/                     # [Next-Gen] 新一代独立智能体模块
│   ├── economy/                # 运营专家 (Standalone)
│   │   ├── agent.py            # 入口
│   │   └── run_standalone.py   # 独立启动脚本
│   ├── strategy/               # 战略专家 (Standalone)
│   │   ├── strategic_agent.py  # 入口
│   │   └── cli.py              # 交互式 CLI
│   └── combat/                 # 战术专家 (Standalone)
│       ├── combat_agent.py     # 入口
│       └── run_standalone.py   # 独立启动脚本
│
├── openra_state/               # [Next-Gen] 独立情报与状态服务
│   ├── intel/                  # 智能化情报处理 (ZoneManager)
│   └── visualize_intel.py      # 独立可视化工具
│
├── web-console/                # [可视化] Web 控制台
│   ├── index.html
│   ├── css/
│   ├── js/
│   └── api/
│
├── tactical_core/              # [Next-Gen] 独立战术核心 (Algorithm Layer)
│   ├── enhancer.py             # 统一入口 (BiodsEnhancer)
│   ├── potential_field.py      # 势场算法
│   └── decision_guard.py       # 决策守护
│
├── the-seed/                   # [框架] (IGNORE) 暂不关注，等待重构
│
├── uni_mic/                    # [交互] 语音/多模态交互模块
│
└── OpenCodeAlert/              # [游戏] (IGNORE) 游戏本体 C# 源码，切勿读取！
```

## 4. 关键机制说明 (Legacy Framework)

> **注意**: 以下机制主要适用于 `main.py` 驱动的旧版架构。新模块 (`agents/*`) 拥有独立的运行逻辑。

### 4.1 `the-seed` 框架集成
项目通过 Git Submodule 引入 `the-seed`。在 `run.bat` / `run.sh` 中，会执行：
```bash
uv pip install -e ./the-seed
```

### 4.2 智能体工作流 (FSM)
智能体运行在一个无限循环中，驱动 FSM 状态流转：
1.  **OBSERVE**: 调用 `OpenRAEnv.observe()` 获取当前局势的文本描述。
2.  **PLAN**: LLM 根据局势和目标，生成高层计划。
3.  **ACTION_GEN**: LLM 将计划步骤转化为 Python 代码。
4.  **EXECUTION**: 运行生成的 Python 代码。
5.  **REVIEW**: 检查执行结果。

### 4.3 黑板机制
黑板 (`Blackboard`) 是 FSM 各节点间共享数据的介质。

## 5. 开发协议与规范

### 5.1 Console 通信协议
Web Console 通过 WebSocket (`ws://localhost:8090`) 接收状态。
*   **Legacy**: 自动同步 FSM 和 Blackboard。
*   **Next-Gen**: 目前部分模块 (如 `strategy`) 已开始对接独立的可视化或日志流。

### 5.2 游戏交互协议 (Socket API)
所有模块（无论是 Legacy 还是 Next-Gen）都统一使用底层的 Socket JSON 协议与 OpenRA 交互。
*   📄 **协议文档**: [socket-apis.md](socket-apis.md)
