# Disadvantage Assessor (劣势评估专家)

## 模块概述

`DisadvantageAssessor` 是一个全新的、独立运作的 Information Expert（信息专家模块）。
它的核心目标是：**弥补现有 `ThreatAssessor` 仅能预警“有敌人”而无法评估“敌我双方真实战力差距（我方是否处于劣势）”的短板。**

该模块是完全非侵入式的。它通过读取 `world_model` 提供的 `NormalizedActor` 数据，输出精准的劣势预警信号。下游的指挥系统可以根据这些信号了解战局的不利情况。

---

## 核心预警定义与实现原理

本模块提供两种维度的劣势预警：

### 1. 全局不利预警 (Global Combat Disadvantage)
- **定义**：场上敌方总战斗力远远超过我方总战斗力。
- **算法原理**：
  - 通过 `world_model.find_actors` 精确过滤出双方的机动战斗单位（`infantry`, `vehicle`, `aircraft`，且 `can_attack=True`）。
  - 利用底层引擎解析好的 `combat_value`（战力评分）进行真实战力对比，而不是简单的单位数量对比。
  - **触发条件**：当敌方全局总评分 $\ge$ 我方总评分的 **3.0 倍**，**且** 敌方总评分比我方多出 **20.0 分**以上时，触发预警。

### 2. 局部不利预警 (Local Squad Disadvantage)
- **定义**：我方的某个局部小分队在野外遭遇了压倒性的敌军。
- **算法原理**：
  - 基于距离聚类算法（Distance-based Clustering），将我方相近的战斗单位自动聚类成多个小队（Squads）。
  - 计算每个小队的“重心坐标”。
  - 在小队重心附近（半径 25.0 内）圈出所有敌方战斗单位，并计算双方的局部战力评分对比。
  - **触发条件**：当局部敌军战力 $\ge$ 该小队战力的 **2.5 倍**，**且** 差值 $\ge$ **15.0 分**时，触发预警。

---

## 模块输出接口规范

`analyze()` 方法返回一个结构化的 Python 字典，格式如下：

```python
{
    "disadvantage_global": True,           # 是否触发全局劣势
    "disadvantage_local": False,           # 是否触发局部劣势
    "disadvantage_warnings": [             # 人类可读/LLM可读的具体警告原因数组
        "[GLOBAL INFERIORITY] Enemy mobile combat score (50.0) severely outweighs ours (10.0). Ratio: 5.0x.",
        "[LOCAL INFERIORITY] Squad #1 at (45, 60) is outmatched! Squad score: 10.0, Nearby enemy score: 30.0."
    ]
}
```

---

## 下游接入建议

本模块作为一个纯粹的信息评估器（Information Expert），自身不包含任何控制逻辑。
建议在 `main.py` 初始化 `WorldModel` 时将其注册：

```python
from experts.info_disadvantage import DisadvantageAssessor

disadvantage_assessor = DisadvantageAssessor(world_model)
world_model.register_info_expert(disadvantage_assessor)
```

注册后，这些劣势信号将自动注入到 `runtime_facts["info_experts"]` 中，供上层策略或 LLM prompt 上下文参考。
