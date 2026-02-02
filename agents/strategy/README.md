
# Strategic Commander Module (战略指挥官模块)

本模块是 OpenRA 智能体的最高决策层，负责全局战略规划与战术指挥。它通过整合情报服务 (`openra_state`) 和战术执行层 (`combat_agent`)，实现全自动的 RTS 游戏指挥。

## 1. 核心功能
*   **全自动决策**: 独立的 LLM (Doubao-Pro-32k) 持续进行战略思考，不设固定时间间隔，确保充分思考时间。
*   **情报融合**: 实时获取地图拓扑、矿区分布、敌我兵力对比和连队状态。
*   **多级指挥**:
    *   **战略层**: 决定进攻方向、占领目标、兵力分配（权重）。
    *   **战术层**: 自动执行阵地战微操、长途奔袭、自动编队。
*   **双模式指令**: 核心使用 `relocate` (战略移动/部署)，支持 `attack` (推进模式) 和 `normal` (急行军模式)。部队到达目标后会自动接管战斗。
*   **指令协同**: 战略指令通过战术层 (`combat_agent`) 的指令队列进行同步，确保在战术微操循环间隙生效，避免指令覆盖冲突。

## 2. 快速开始

### 2.1 环境准备

推荐使用 `uv` 进行环境管理（速度快，依赖解析强）。

1. **安装 uv (如果尚未安装)**:
   ```bash
   pip install uv
   ```

2. **创建虚拟环境并安装依赖**:
   在项目根目录下执行：
   ```bash
   # 创建虚拟环境 (.venv)
   uv venv

   # 激活虚拟环境 (Windows)
   .venv\Scripts\activate
   # 激活虚拟环境 (Linux/macOS)
   # source .venv/bin/activate

   # 安装依赖
   uv pip install -r requirements.txt
   ```

### 2.2 启动

在项目根目录下，使用交互式 CLI 工具启动（推荐）：
```bash
uv run agents/strategy/cli.py
# 或
python agents/strategy/cli.py
```

或者使用测试脚本直接运行（用于无头模式或调试）：
```bash
uv run agents/strategy/run_strategy.py
# 或
python agents/strategy/run_strategy.py
```

### 2.3 交互控制
推荐使用 `cli.py` 进行交互控制：
*   `start`: 启动代理（默认执行“自主决策”）
*   `stop`: 停止代理
*   `cmd <指令内容>`: 下达指令 (例如 `cmd 全力进攻`)
*   `status`: 查看连队状态
*   `eco start`: 启动自动运营模块 (自动造建筑/造兵)
*   `eco stop`: 关闭自动运营模块

**注意**: `cli.py` 启动时同样会初始化 `StrategicAgent` 并触发日志系统，详细日志依然会写入 `agents/strategy/strategic_agent.log`。

若直接运行 `run_strategy.py`，代理将默认执行“自主决策”模式，除非你手动创建 `user_command.txt` 文件并写入指令。
*   **默认内容**: `自主决策`

LLM 会在下一次决策循环读取并执行该指令。

## 3. 日志与调试
程序启动后，控制台会输出实时日志：
*   **[INFO]**: 关键事件（连队创建、指令下发、模块启动）。
*   **[Strategy]**: 战略 LLM 的思考过程与决策摘要。
*   **[Combat]**: 战术连队的微操日志。

详细日志会同时写入 `strategic_agent.log` 文件。

## 4. 故障排查
如果程序卡住或无反应：
1.  检查 OpenRA 游戏是否已启动并开启了 External API (7445端口)。
2.  检查 `.env` 中的 API Key 是否有效。
3.  查看控制台是否有报错信息 (Exception/Error)。
4.  确保 `user_command.txt` 文件存在（启动时会自动创建）。

## 5. 其他模块联动
*   **自动运营 (Economy Agent)**:
    *   **方式一 (推荐)**: 在 CLI 中使用 `eco start` 指令直接启动集成在战略代理中的运营模块（仅限苏军）。
    *   **方式二 (独立运行)**:
        *   苏军: `$env:OPENRA_FACTION="Soviet"; python agents/economy/run_standalone.py`
        *   盟军: `$env:OPENRA_FACTION="Allies"; python agents/economy/run_standalone.py`
*   **内环微操 (Combat Agent)**: 
    *   已内置集成，负责具体的连队管理与阵地战。
*   **战术增强 (Tactical Core)**:
    *   **启用方式**: 在 CLI 中使用 `tac start` 启用势场微操与协同回退逻辑。
    *   **日志窗口**: `tac show` / `tac hide` 控制独立日志窗口显示。