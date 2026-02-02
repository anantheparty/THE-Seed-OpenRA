# Tactical Core V2

OpenRA Assistant 的战术微操核心模块。该模块设计为**独立组件**，既可以集成在 Assistant 主程序中运行，也可以作为独立进程运行。

## 模块功能

*   **Entity Manager**: 独立维护战术实体状态（位置、血量、威胁值）。
*   **Decision Guard**: 目标管理与协同回退（自动填补失效目标）。
*   **Potential Field**: 基于人工势场的实时微操（避障、阵型保持）。
*   **Interrupt Logic**: 高优先级战术硬中断（如脆皮自动后撤、反隐强制集火）。

## 目录结构

```text
tactical_core/
├── __init__.py           # 包导出
├── client.py             # 独立 Socket 客户端 (不依赖主程序)
├── constants.py          # 兵种属性与常量定义
├── decision_guard.py     # 决策守护模块
├── enhancer.py           # 核心入口 (Facade)
├── entity_manager.py     # 实体状态管理
├── interrupt_logic.py    # 硬中断逻辑
├── potential_field.py    # 势场算法
├── ui.py                 # 独立运行时的日志窗口
├── launcher.py           # 独立启动脚本
└── README.md             # 本文档
```

## Upstream Interface Specification (上游接口规范)

为了保证模块独立性，本模块尽量减少对主程序的直接依赖，通过依赖注入（Dependency Injection）获取必要服务。同时，**模块内部维护了独立的 `TacticalClient`**，不复用主程序的 API Client，以确保该模块可以不依赖主程序独立运行。

### 1. Unit Mapper Dependency (单位名称映射)

本模块内部实现了 `STANDARD_NAME_MAP` (在 `constants.py` 中)，能够自动处理官方中文单位名称并转换为标准英文代码。这意味着无论引擎返回单位中文还是英文代码，模块都能正常工作。

## 接入指南 (Integration)

### 1. 作为模块集成 (In-Process)

在主程序中导入并实例化 `BiodsEnhancer`。建议在程序启动时尽早初始化并使其常驻后台。

```python
from assistant.tactical_core import BiodsEnhancer

# 初始化
enhancer = BiodsEnhancer()

# 启动常驻线程 (默认隐藏日志窗口)
# 注意：务必设置 LLM_DEBUG 环境变量以启用内部日志流
import os
os.environ["LLM_DEBUG"] = "1"
enhancer.start(api_client, show_log_window=False)

# 在主循环或回调中注入上游分配
# pairs: List[Tuple[attacker_id, target_id]]
enhancer.enhance_execute(api_client, pairs)

# 需要查看状态时唤出窗口
enhancer.show_log_window()

# 停止
enhancer.stop()
```

### 2. 独立运行 (Standalone)

该模块可以完全脱离主程序运行，仅依赖游戏引擎暴露的 Socket API (7445端口)。

**启动方式**：
提供了两种终端指令模式（这里以我的项目路径为例）：

1. **独立启动本模块 (同时自动启动UI)**
   适用于独立测试战术核心逻辑，会自动连接游戏并显示日志窗口。
   ```bash
   python assistant/tactical_core/launcher.py
   ```
   **独立运行时的行为**：
   *   自动连接本地 7445 端口。
   *   启动一个半透明置顶窗口显示实时战术日志。
   *   执行被动战术逻辑（如自动后撤、自动反击），但**无法接收** LLM 的目标分配（除非通过某种 IPC 机制注入）。
   *   **单位名称处理**：利用内部的 fallback 机制 (`constants.py` 中的标准名称映射表) 自动处理本地化单位名称。

2. **UI 调试模式**
   
   主程序运行时，战术模块 UI 默认隐藏。
   - 在主程序界面勾选 **"显示战术日志"** 即可唤出实时日志窗口。
   - 战术模块现在随主程序自动启动并常驻后台，无需手动开启。

## 接口规范 (API Spec)

### 上游输入 (Input)
*   **`enhance_execute(..., pairs)`**: 
    *   `pairs`: `[(int, int)]`，攻击者ID与目标ID的列表。
    *   说明：这是战术核心接收外部指令的唯一入口。

### 下游输出 (Output)
*   **Socket Commands**: 模块内部通过 `client.py` 直接向游戏引擎发送指令：
    *   `attack(attacker_id, target_id)`
    *   `move_actor(actor_id, direction, ...)`
    *   `query_actor(faction="all")`
*   **UI Integration**: 
    *   `show_log_window()`: 唤出战术日志窗口。
    *   `hide_log_window()`: 隐藏战术日志窗口（后台继续运行）。
    *   `start(..., show_log_window=bool)`: 初始化并启动模块，参数控制是否立即显示日志。