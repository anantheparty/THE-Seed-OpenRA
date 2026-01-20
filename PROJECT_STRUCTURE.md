# THE-Seed OpenRA Agent 项目结构说明

## 1. 项目概览

本项目是一个基于 `the-seed` 框架构建的 OpenRA 智能体，旨在实现 AI 在红色警戒游戏中的自主决策与操作。项目核心由 Python 编写的智能体后端、Rust 编写的可视化 Dashboard 以及 OpenRA 游戏本体（作为子模块存在）组成。

## 2. 核心架构

项目采用分层架构设计，各层职责清晰：

*   **Agent Core (`main.py`, `agents/`)**: 智能体的大脑。使用 `the-seed` 框架提供的有限状态机 (FSM) 驱动，负责感知游戏状态、规划行动序列并生成具体指令。
*   **Game Interface (`openra_api/`)**: 智能体的手和眼。负责与 OpenRA 游戏引擎进行 TCP Socket 通信，将游戏原始数据转化为结构化情报 (Intel)，并将智能体指令封装为宏操作 (MacroActions)。
*   **Framework (`the-seed/`)**: 基础框架。提供通用的 FSM 实现、节点工厂 (NodeFactory)、黑板机制 (Blackboard) 和大模型适配器 (ModelAdapter)。这是一个独立的 submodule。
*   **Visualization (`dashboard/`)**: 监控面板。基于 Rust 和 Makepad 开发的高性能 GUI，通过 WebSocket 实时展示智能体思维状态和黑板数据。
*   **Multimodal (`uni_mic/`)**: 交互层。提供语音识别 (ASR) 和语音合成 (TTS) 能力，允许用户通过自然语言指挥智能体。

## 3. 目录结构详解

```text
D:\THE-Seed-OpenRA\
├── main.py                     # [入口] 智能体启动脚本。负责初始化 API、FSM 和黑板，并启动 WebSocket 服务。
├── run.bat / run.sh            # [启动] 跨平台一键启动脚本。会自动处理 Python 虚拟环境和依赖安装。
├── install&run.sh              # [安装] Linux 下的安装与运行脚本。
│
├── adapter/                    # [适配] 连接 Agent 和 GameAPI 的胶水层
│   └── openra_env.py           # OpenRAEnv 类，实现 observe() 方法，将游戏数据转化为 LLM 可读的文本摘要。
│
├── openra_api/                 # [接口] 游戏交互核心库
│   ├── game_api.py             # 底层 Socket 客户端，实现与 OpenRA Server 的原始 JSON 通信。
│   ├── macro_actions.py        # 宏指令封装 (如 produce_wait, dispatch_attack)，简化 LLM 调用。
│   ├── game_midlayer.py        # 中间层门面，整合了 IntelService 和 MacroActions。
│   ├── rts_middle_layer.py     # RTS 专用中间层实现。
│   ├── models.py               # 游戏数据模型 (Actor, Location 等)。
│   ├── intel/                  # 情报系统，负责处理和缓存游戏状态。
│   │   ├── zone_manager.py     # [核心] 战术地图管理器。提供混合拓扑 (DBSCAN + Mine Snapping) 和 Gabriel Graph 邻居网络。
│   │   ├── clustering.py       # 空间聚类算法实现。
│   │   └── ...
│   └── jobs/                   # 任务管理系统，用于处理持续性任务 (如自动探索、自动攻击)。
│
├── agents/                     # [逻辑] 智能体具体实现 (目前主要逻辑在 main.py 中组装)
│   └── commander.py            # (备用) 包含构建 Commander Runtime 的辅助函数。
│
├── dashboard/                  # [可视化] Rust 编写的实时监控面板
│   ├── src/                    # Rust 源码
│   │   ├── main.rs             # 应用程序入口
│   │   ├── app.rs              # UI 布局与核心逻辑
│   │   └── ws_client.rs        # WebSocket 客户端，接收智能体状态推送。
│   └── Cargo.toml              # Rust 项目配置。
│
├── the-seed/                   # [框架] (Submodule) 通用 AI Agent 框架
│   ├── the_seed/
│   │   ├── core/               # 核心逻辑
│   │   │   ├── fsm.py          # 有限状态机实现
│   │   │   ├── blackboard.py   # 黑板模式实现，用于节点间共享数据
│   │   │   └── node/           # 标准节点实现 (Observe, Plan, ActionGen, Review)
│   │   └── utils/              # 工具库 (DashboardBridge, LogManager 等)
│
├── uni_mic/                    # [交互] 语音/多模态交互模块
│   ├── cli.py                  # 命令行入口
│   ├── gui.py                  # 悬浮窗 UI
│   └── asr_module.py           # 语音识别实现 (Whisper/FunASR)
│
└── OpenCodeAlert/              # [游戏] (Submodule) OpenRA 游戏本体源码 (本项目不直接修改此处)
```

## 4. 关键机制说明

### 4.1 `the-seed` 框架集成
项目通过 Git Submodule 引入 `the-seed`。在 `run.bat` / `run.sh` 中，会执行：
```bash
uv pip install -e ./the-seed
```
这不仅安装了依赖，还将 `the-seed` 目录以**可编辑模式 (Editable Mode)** 安装到当前 Python 环境中。因此，在 `main.py` 中可以直接使用 `from the_seed.core.fsm import FSM` 进行导入，且对 `the-seed` 源码的修改会即时生效，无需重新安装。

### 4.2 智能体工作流 (FSM)
智能体运行在一个无限循环中，驱动 FSM 状态流转：
1.  **OBSERVE**: 调用 `OpenRAEnv.observe()` 获取当前局势的文本描述，并覆盖更新黑板上的感知数据。
2.  **PLAN**: LLM 根据局势和目标，生成高层计划 (Plan)，并重置黑板上的计划索引。
3.  **ACTION_GEN**: LLM 将计划步骤转化为 Python 代码，调用 `bb.midapi` (即 `MacroActions`) 中的函数，执行结果会覆盖黑板上的 `last_outcome`。
4.  **EXECUTION**: (隐式) 运行生成的 Python 代码，通过 `GameAPI` 发送指令给游戏。
5.  **REVIEW**: 检查执行结果，决定下一步状态。

### 4.3 动态文档生成
为了让 LLM 学会使用 API，项目在启动时通过 `build_def_style_prompt` 动态生成 API 文档。它会反射读取 `MacroActions` 类中方法的签名和 Docstring，构建出实时的“函数使用手册”注入到 Prompt 中。

### 4.4 黑板机制与数据生命周期
黑板 (`Blackboard`) 是 FSM 各节点间共享数据的唯一介质，其数据生命周期管理如下：

*   **覆盖式更新 (Overwrite)**:
    *   **感知数据 (`intel`, `game_basic_state`)**: 每次进入 `ObserveNode` 时被全量覆盖。
    *   **计划数据 (`plan`, `step_index`)**: 每次进入 `PlanNode` 时被全量重置。
    *   **执行结果 (`action_result`, `python_script`, `last_outcome`)**: 每次执行动作后被最新结果覆盖。
*   **追加式更新 (Append) - ⚠️ 注意**:
    *   **便签本 (`scratchpad`)**: 用于记录临时的推理过程和假设。目前代码中**仅追加无清理**，长期运行会导致 Context 无限膨胀，是重构时需要重点解决的 Context 泄漏点。
    *   **事件流 (`events`)**: 设计用于记录历史关键事件，目前主要为追加模式。

### 4.5 节点职责与数据流向
FSM 中的各节点通过黑板 (Blackboard) 进行松耦合的数据交换，各节点职责如下：

1.  **ObserveNode (感知)**
    *   **输入**: `game_basic_state` (环境注入), `last_outcome`
    *   **职责**: 生成观测报告，更新感知状态。
    *   **输出**: 覆盖 `bb.intel`。
    *   **流向**: -> `PLAN`

2.  **PlanNode (规划)**
    *   **输入**: `goal`, `intel`, `events`
    *   **职责**: 生成或更新任务队列。
    *   **输出**: 覆盖 `bb.plan`, 重置 `bb.step_index=0`, 更新 `bb.current_step`。
    *   **流向**: -> `ACTION_GEN`

3.  **ActionGenNode (执行)**
    *   **输入**: `current_step`, `gameapi_rules`
    *   **职责**: 将子任务转化为 Python 代码。
    *   **输出**: 
        *   覆盖 `bb.python_script` (生成的代码)。
        *   覆盖 `bb.action_result` (执行结果)。
        *   覆盖 `bb.last_outcome` (执行结果副本)。
    *   **流向**: -> `REVIEW`

4.  **ReviewNode (审查)**
    *   **输入**: `current_step`, `python_script`, `action_result`
    *   **职责**: 检查执行是否成功，必要时进行代码修复。
    *   **输出**: 覆盖 `bb.review` (审查意见), 更新 `bb.action_result` (修复后的结果)。
    *   **流向**: -> `ACTION_GEN` (成功) 或 `PLAN` (失败)

**注意**: 节点之间**不直接**传输数据，所有状态流转均通过**黑板**作为中介。

## 5. 开发协议与规范

为了降低开发复杂度，请严格遵守以下协议，无需关注 `dashboard/` 和 `OpenCodeAlert/` 的具体实现。

### 5.1 Dashboard 通信协议
Dashboard 通过 WebSocket (`ws://localhost:8080`) 被动接收智能体推送的状态更新。智能体后端 (`DashboardBridge`) 会自动序列化 FSM 和 Blackboard 的状态。

**开发者只需关注黑板上的数据结构，无需手动发送 WebSocket 消息。** Dashboard 会自动渲染以下标准字段：

*   **FSM 状态**: 自动同步 `fsm.state`。
*   **黑板数据**: 自动同步 `blackboard` 字典中的所有内容。
    *   `intel`: 显示在“感知”面板。
    *   `plan`: 显示在“计划”列表。
    *   `current_step`: 高亮当前正在执行的步骤。
    *   `scratchpad`: 显示在“思考过程/日志”区域。

**Dashboard 开发者规约**:
*   🚫 **不要修改 Dashboard 源码**: `dashboard/` 目录下的 Rust 代码仅用于展示，非必要不修改。
*   ✅ **数据驱动**: 如果要在 Dashboard 上显示新内容，只需将其写入 Blackboard 的标准字段 (`intel`, `plan`, `events` 等) 即可。

### 5.2 游戏交互协议 (Socket API)
智能体与 OpenRA 游戏的通信完全遵循 `socket-apis.md` 定义的 JSON 协议。

*   📄 **协议文档**: 请参考根目录下的 **[socket-apis.md](file:///d:/THE-Seed-OpenRA/socket-apis.md)**。
*   🚫 **忽略游戏源码**: `OpenCodeAlert/` 目录是游戏本体源码，极其庞大且复杂，**开发智能体时完全不需要阅读或修改它**。
*   ✅ **使用封装层**: 始终通过 `openra_api.macro_actions` 或 `openra_api.game_api` 进行交互，不要手动拼接 JSON 字符串。