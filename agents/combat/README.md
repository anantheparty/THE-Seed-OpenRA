# Combat Agent Module (战术指挥官模块)

## 1. 模块概述
本模块是 OpenRA 智能体的战术执行层，负责微观战斗操作与连队管理。
设计为**独立进程运行**，通过标准 Socket 协议与游戏通信，不依赖主框架 (`the-seed`) 的全局状态，具备独立的 API 客户端与配置环境。

## 2. 核心组件 (Current Progress)

### 2.1 基础设施 (`infra/`)
*   **GameClient** (`infra/game_client.py`): 
    *   独立封装的 Socket 客户端，零依赖。
    *   **自动翻译**: 强制使用中文 (`zh`) 与游戏引擎通信，但在内部自动将中文单位名映射为标准英文代码，确保上层逻辑统一使用英文 ID。
    *   **功能**: 支持 `query_actors` 和 `attack_move`, `stop` 等基础指令。
*   **LLMClient** (`infra/llm_client.py`): 
    *   封装 OpenAI SDK，适配火山引擎 (Doubao) 模型。
    *   **低延迟优化**: 强制设置 `reasoning_effort="minimal"` (关闭深度思考) 并开启流式输出 (`stream=True`)，适应 RTS 实时性要求。
*   **配置**: 使用 `.env` 文件管理 API Key 和 Endpoint。

### 2.2 编队管理系统 (Squad Management)
*   **数据结构** (`structs.py`):
    *   `CombatUnit`: 仅包含战术相关数据 (ID, Type, HP%, Pos, Score)。
    *   `Squad`: 连队结构，支持 `target_weight` 权重参数，用于非对称兵力分配。
*   **单位追踪** (`unit_tracker.py`):
    *   **高频轮询**: 后台线程以 **0.5s** 间隔同步游戏状态。
    *   **智能过滤**: 自动排除非战斗单位（矿车、MCV、工程师）及防御建筑，仅追踪有效战斗力量。
    *   **状态同步**: 实时更新 HP 和位置，自动处理单位死亡注销。
*   **编队管理器** (`squad_manager.py`):
    *   **分区管理**: 维护 `Unassigned` (待分配池), `Companies` (自动连队), `PlayerSquad` (玩家手动控制/隐藏)。
    *   **自动编队算法**: 采用 **加权贪心策略 (Weighted Greedy Heuristic)**。
        *   原理：`Load = (TotalScore / Weight) + (UnitCount / Weight)`
        *   机制：新单位自动流入当前负载最小的连队，实现“只进不出”的动态平衡。
    *   **API**: 提供 `enable_company`, `delete_company`, `transfer_unit`, `update_company_weight` 接口供上层调用。

## 3. 目录结构
```text
agents/combat/
├── infra/                  # 基础设施层
│   ├── game_client.py      # 游戏客户端 (Socket)
│   ├── llm_client.py       # LLM 客户端 (OpenAI/Doubao)
│   ├── combat_data.py      # 本地单位评分数据
│   └── dataset_map.py      # 中英文单位名映射表
├── structs.py              # 数据结构定义
├── unit_tracker.py         # 单位状态追踪器
├── squad_manager.py        # 编队管理器
├── run_standalone.py       # 独立运行入口
└── .env                    # 配置文件 (需手动创建)
```

## 4. 快速开始 (Standalone)

确保根目录下有 `.env` 文件（或在 `agents/combat/.env`），然后运行：

```bash
# 确保安装依赖
uv pip install python-dotenv openai

# 启动独立模块
python agents/combat/run_standalone.py
```

## 5. 接口规范 (Superior Agent Interface)

### 5.1 指令下发 (`CombatAgent.set_company_order`)
上级智能体通过此接口控制连队行为。

```python
def set_company_order(self, company_id: str, order_type: str, params: Dict):
    """
    Args:
        company_id (str): 连队 ID (从 SquadManager 获取)
        order_type (str): "combat" 或 "relocate"
        params (dict):
            - combat: {"target_pos": {"x": 1, "y": 2}}
            - relocate: {"target_pos": {"x": 1, "y": 2}, "move_mode": "attack"|"normal"}
    """
```

*   **Combat Mode (阵地战)**:
    *   `CombatAgent` 接管控制权。
    *   以 `target_pos` 为圆心扫描敌军，LLM 决策攻击。
    *   **循环机制**: 流式输出结束后立即启动下一轮，无固定间隔。
    *   **指令队列**: 每一轮微操循环开始前，会检查并应用来自战略层的最新指令 (`Pending Orders`)，确保指令切换的原子性。
*   **Relocate Mode (战略调遣)**:
    *   **Attack Move (默认)**:
        *   收到指令后，立即发送一次底层 `AttackMove` 指令。
        *   **即刻切换**: 随后立即自动切换至 `Combat Mode`，以目标坐标为圆心开始 Combat Loop。
        *   **目的**: 确保新加入连队的单位能被 Combat Loop 捕获并推向前线，防止滞留家中。
    *   **Normal Move (强制移动)**:
        *   发送底层 `Move` (isAttackMove=False) 指令。
        *   **暂停微操**: 保持在 Relocate 状态，暂停 Combat Loop，直到上级下达新指令。适合撤退或强行突防。

### 5.2 状态查询 (`SquadManager.get_status`)
返回所有连队的实时状态摘要。

```json
{
    "companies": [
        {
            "id": "1", 
            "count": 5, "power": 45.0, "weight": 1.0,
            "location": {"x": 120, "y": 145}
        }
    ]
}
```

> **注意**: `location` 是连队中心点坐标，已自动排除距离主力过远（如刚出生在基地）的离群单位。如果连队为空，`location` 为 `null`。

### 5.3 兵力分配权重 (`weight`) 说明

**权重 (Weight)** 是控制自动编队算法核心参数，用于实现**非对称兵力分配**。

*   **默认值**: `1.0` (平权分配)。
*   **计算公式**: 
    $$ Load = \frac{CombatPower}{Weight} + \frac{UnitCount}{Weight} $$
    新生产的单位会自动加入当前 `Load` 最低的连队。
*   **战术应用**:
    *   **主攻/佯攻**: 将主攻连队权重设为 `3.0`，佯攻连队设为 `1.0`。此时主攻连队将获得约 75% 的新兵力补充，而佯攻连队仅获得 25%。
    *   **快速补员**: 当某连队在前线战损严重时，可临时调高其权重，使后续援军优先补充该连队。
    *   **冻结编队**: 将权重设为极小值 (如 `0.01`)，该连队将几乎不再接收新兵。

## 6. 开发计划 (Next Steps)
- [x] **Task 4.0**: 基础设施搭建 (Done)
- [x] **Task 4.1**: 编队管理系统 (Done)
- [x] **Task 4.2**: 战术指挥官实例 (Done)
    - [x] 实现了 Combat Loop 与流式决策。
    - [x] 实现了 Combat/Relocate 双模式切换与冲突规避。
- [ ] **Task 4.3**: 可视化交互控制台 (UI)
