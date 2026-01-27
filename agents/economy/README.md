# Economy Specialist Module

## 1. 简介
本模块 (`agents/economy`) 是 OpenRA 智能体的 **运营专家**，负责全自动的资源管理、建筑建造和单位生产。它采用 **去 FSM 化** 的设计，核心逻辑基于纯 Python 实现的 heuristic state machine (启发式状态机)。

## 2. 核心算法 (Algorithm)

`EconomyEngine` 采用基于优先级的决策树 (Priority-based Decision Tree)：

1.  **MCV 展开 (Deployment)**:
    *   **条件**: 没有任何己方建筑 (`my_structures` 为空) 且 拥有 MCV 单位。
    *   **动作**: 立即展开 MCV 建立基地。
2.  **电力保障 (Power Safety)**:
    *   **条件**: 电力盈余 (`PowerProvided - PowerDrained`) < 0。
    *   **动作**: 强制插入电厂建造任务 (优先高级电厂)。
3.  **标准建造序列 (Build Order)**:
    *   **逻辑**: 依次检查标准开局序列 (基地 -> 电厂 -> 兵营 -> 矿场 -> 重工 -> 雷达 -> 更多矿场(至多5个)和机场 -> 科技中心)。
    *   **动作**: 如果当前序列中的建筑未满足数量要求，拥有建造厂且建造队列空闲，则发布建造指令。
4.  **资源溢出与动态扩张 (Dynamic Expansion)**:
    *   **> 5,000**: 扩建战车工厂 (最多 5 个)，提升载具产能。
    *   **> 10,000**: 启动 **空军生产** (Aircraft)。支持苏军 (Yak/Mig) 和盟军 (Heli/BlackHawk) 1:1 混合生产，无数量上限。
    *   **> 15,000**: 启动 **防御工事** (Defense)。自动检测电力，若电力不足会自动建造电厂以维持防御塔建设。
5.  **单位生产 (Unit Production)**:
    *   **逻辑**: 动态比例配兵 (Dynamic Ratio Balancing) + 移动生产支持。
    *   **特性**: 即使收起基地车 (MCV)，只要满足先决条件 (如拥有兵营/重工)，仍可持续生产步兵和载具。
    *   **目标比例**: `ARTY:1 | MBT:2 | AFV:0.5 | INF_MEAT:2 | INF_AT:1`
    *   **动作**: 计算当前兵力缺口最大的类别，若资源充足 (`>500`) 且队列未满，则加入生产。

## 3. 独立运行 (Standalone Usage)

本模块设计为可独立运行，方便调试和测试。

**运行方法 (默认苏军)**:
```bash
python agents/economy/run_standalone.py
```

**运行方法 (盟军)**:
#### Windows (PowerShell):
```powershell
$env:OPENRA_FACTION="Allies"; python agents/economy/run_standalone.py
```

#### Linux / Mac / Git Bash:
```bash
OPENRA_FACTION=Allies python agents/economy/run_standalone.py
```

**环境变量**:
*   `OPENRA_FACTION`: 设置阵营，支持 `Soviet` (默认) 或 `Allies`。

## 4. 集成规范 (Integration Guide)

### 上游依赖 (Input)
*   **GameAPI**: 需要一个连接到 OpenRA 服务的 `GameAPI` 实例。
*   **Module Switch**: `EconomyAgent` 提供 `set_active(bool)` 方法。主程序可通过此开关在运行时动态启用或关闭运营模块（例如在手动控制模式下关闭）。

### 下游输出 (Output)
*   **Game Commands**: 直接调用 `GameAPI` 发送 `start_production`, `deploy`, `player_baseinfo_query` 等指令。

### 代码结构
*   `agent.py`: **EconomyAgent** (驱动层)，负责 Tick 循环和 API 调用。
*   `engine.py`: **EconomyEngine** (逻辑层)，纯算法，无副作用。
*   `state.py`: **EconomyState** (数据层)，负责从 API 拉取并解析状态。
*   `utils.py`: **Utils** (工具层)，包含 ID 映射和阵营常量。
