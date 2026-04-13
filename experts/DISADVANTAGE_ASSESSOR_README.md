# Disadvantage Assessor (劣势评估专家)

## 模块概述

`DisadvantageAssessor` 是一个全新的、独立运作的 Information Expert（信息专家模块）。
它的核心目标是：**弥补现有 `ThreatAssessor` 仅能预警“有敌人（被骚扰）”而无法评估“敌我双方真实战力差距（我方是否处于劣势）”的短板。**

该模块是完全非侵入式的。它通过读取 `world_model` 提供的全量快照和 `openra_state` 提供的底层数据字典/算法，输出精准的劣势预警信号。下游的指挥系统（LLM 或 FSM）可以根据这些信号进行强力的战术干预。

---

## 核心预警定义与实现原理

本模块提供三种维度的劣势预警：

### 1. 全局不利预警 (Global Combat Disadvantage)
- **定义**：场上敌方总战斗力远远超过我方总战斗力。
- **算法原理**：
  - 精确过滤出双方的“纯战斗单位”（排除了建筑、防御塔、矿车、工程师、残骸、出生点等干扰项）。
  - 不使用单纯的“单位数量”对比，而是调用 `/workspace/openra_state/data/combat_data.py` 中的 `get_unit_combat_info()` 方法。将每个单位映射为真实的“战力评分（Combat Score）”（例如 1 辆坦克 = 10分，1 个动员兵 = 1分）。
  - **触发条件**：当敌方全局总评分 $\ge$ 我方总评分的 **3.0 倍**，**且** 敌方总评分比我方多出 **20.0 分**以上时，触发预警。

### 2. 局部不利预警 (Local Squad Disadvantage)
- **定义**：我方的某个局部小分队在野外遭遇了压倒性的敌军。
- **算法原理**：
  - 复用 `/workspace/openra_state/intel/clustering.py` 中的 **DBSCAN 算法**，将我方散落的战斗单位自动聚类成多个小队（Squads），并排除明显的离散单位。
  - 计算每个小队的“重心坐标”。
  - 在小队重心附近（半径 25.0 内）圈出所有敌方战斗单位，并计算双方的局部战力评分对比。
  - **触发条件**：当局部敌军战力 $\ge$ 该小队战力的 **2.5 倍**，**且** 差值 $\ge$ **15.0 分**时，触发预警。

### 3. 资源匮乏预警 (Economy Shortage Disadvantage)
- **定义**：我方拥有的安全矿区资源量，无法支撑当前存活的矿车数量，经济即将陷入停滞。
- **算法原理**：
  - 提取 `world_model` 中由 `ZoneManager`（区域管理器）预处理好的全地图矿区数据。
  - 过滤出“我方安全矿区”：该区域必须有我方建筑/部队，且不能有任何敌方建筑/部队。
  - **触发条件**：当前安全矿区的总资源评分，低于（我方矿车总数 $\times$ 5.0）的最低运作阈值时，触发预警。

---

## 模块输出接口规范

`analyze()` 方法返回一个结构化的 Python 字典，格式如下：

```python
{
    "is_disadvantaged": True,          # 强干预布尔信号，只要触发上述任一劣势，即为 True
    "disadvantage_level": "critical",  # 劣势等级: "none", "high", "critical"
    "warnings": [                      # 人类可读/LLM可读的具体警告原因数组
        "[GLOBAL INFERIORITY] Enemy global combat score (50.0) severely outweighs ours (10.0). Ratio: 5.0x.",
        "[LOCAL INFERIORITY] Squad #1 at (45, 60) is outmatched! Squad score: 10.0, Nearby enemy score: 30.0.",
        "[ECONOMY SHORTAGE] Safe resource zones are depleted! Safe value: 2.0, Required for 3 harvesters: 15.0."
    ]
}
```

---

## 推荐的下游接入与干预策略

**建议项目负责团队在下游（如 Kernel 或具体的 Expert 中）按以下思路接入该信号（请勿在此模块内直接硬编码干预行为）：**

1. **对于“全局不利预警” (`[GLOBAL INFERIORITY]`)**：
   - **干预策略**：建议直接替玩家给 `Adjutant` 下令：“全面撤退，所有部队退守基地”。此时绝对不能分散兵力。
   - **经济策略**：强制 `EconomyExpert` 将全部资源投入生产主力单位（如 `4tnk`），并紧急在基地周边建造防御塔（`defense`）。

2. **对于“局部不利预警” (`[LOCAL INFERIORITY]`)**：
   - **干预策略**：强制让正在控制该区域的 `CombatExpert`（战斗专家实例）执行撤退状态机（Retreat FSM）。
   - **LLM 联动**：将该警告作为 `TaskMessage` 推送给全局的 `TaskAgent` (LLM)，建议其派遣其他空闲小队前往增援。

3. **对于“资源匮乏预警” (`[ECONOMY SHORTAGE]`)**：
   - **干预策略**：向 LLM 抛出警告信号，建议启动“开分矿”的复合任务流程。
