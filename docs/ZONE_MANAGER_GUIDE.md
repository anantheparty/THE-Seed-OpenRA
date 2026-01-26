# ZoneManager 指南 (ZoneManager Guide)

**ZoneManager** 是 OpenRA 智能体感知系统的核心组件，负责将连续的地图坐标抽象为离散的战术区域 (Zones)。它采用混合拓扑策略，结合了全图静态资源分布和动态视野信息，为上层智能体提供高效的战术决策基础。

## 1. 核心设计理念

### 1.1 混合拓扑策略 (Hybrid Topology)
传统的 RTS 地图分析通常要么只用全图网格 (Grid)，要么只用路点 (Waypoint)。ZoneManager 采用 **"全图覆盖 + 局部修正"** 的混合策略：

1.  **全图基准层 (Global Layer)**:
    - 使用 `MapQueryResult` 获取全图资源分布 (即使在战争迷雾下，地图的资源分布通常是静态可查的)。
    - 采用 **Grid-optimized DBSCAN** 聚类算法，识别所有潜在的矿区 (Resource Patches)。
    - **目的**: 建立全图的宏观骨架，确保智能体知道"哪里有矿"，即使还没探开视野。

2.  **局部修正层 (Local Layer)**:
    - 当智能体视野内发现 **矿柱 (Mine Actor)** 时，触发 **锚定机制 (Snapping)**。
    - **优先级逻辑**:
        - 优先锚定 **宝石矿柱 (Gem Mine)**，因为其战略价值更高。
        - 若存在多个同级矿柱，选择距离资源几何中心最近的一个。
    - 将 Zone 的中心强制移动到选定的矿柱位置。
    - **目的**: 解决 Grid 坐标与游戏内实际 Actor 坐标的微小偏差，确保采矿车能准确导航到矿点中心。

### 1.2 自然邻接网络 (Gabriel Graph)
ZoneManager 摒弃了基于固定距离阈值的邻居判定，转而使用 **Gabriel Graph** 算法构建 Zone 之间的连接关系。

- **定义**: 两个 Zone (A, B) 相连，当且仅当以 A、B 为直径的圆内不包含任何其他 Zone C。
- **优势**:
    - **自适应**: 无论地图是大是小，都能生成合理的连接图。
    - **无阻挡**: 倾向于连接"直达"的邻居，避免跨越中间节点连接，天然符合 RTS 的路径逻辑。

## 2. 核心功能

### 2.1 区域识别与分类
ZoneManager 会自动识别以下类型的区域：
- **RESOURCE**: 资源区 (矿区)。包含 `subtype` 细分：
    - `ORE`: 普通黄金矿区 (Base Value)。
    - `GEM`: 宝石矿区 (High Value, 约 2.5x 权重)。
    - `MIXED`: 混合矿区。
- **MAIN_BASE**: 主基地 (检测到基地车/Fact)。
- **SUB_BASE**: 分基地 (检测到建筑群但无基地车)。

### 2.2 动态归属判定
结合 `update_bases` 接口，ZoneManager 实时计算每个 Zone 的归属权：
- **Owner**: 拥有该区域最多建筑的阵营。
- **Resource Value (Score)**: 综合资源评分。
    - **计算公式**: `(OreTiles * 1.0 + GemTiles * 2.5) + (OreMines * 50 + GemMines * 150)`
    - **含义**: 不再区分"储量"和"战略价值"，该评分直接代表该区域的战术价值，供智能体直接比较。矿柱的高权重反映了其作为无限资源源头的核心价值。

## 3. 使用方法


### 3.1 初始化与更新
ZoneManager 通常嵌入在 `IntelligenceService` 中自动运行。

```python
from openra_api.intel.zone_manager import ZoneManager

zm = ZoneManager()

# 1. 初始构建 (通常在游戏开始或每分钟低频执行)
# map_data: MapQueryResult
# mine_actors: List[Actor] (可选，用于锚定)
zm.update_from_map_query(map_data, mine_actors=visible_mines)

# 2. 状态刷新 (高频执行，如每 2秒)
# all_units: List[Actor]
zm.update_bases(all_units, my_faction="Soviet", ally_factions=[])
```

### 3.2 读取信息
```python
# 获取指定坐标所在的 Zone
zone_id = zm.get_zone_id(Location(x, y))
zone = zm.get_zone(zone_id)

if zone:
    print(f"Zone {zone.id}: {zone.type}, Value: {zone.resource_value}")
    if zone.owner_faction == "MY":
        print("This is our territory.")
```

## 4. 数据结构
```python
@dataclass
class ZoneInfo:
    id: int
    center: Location
    type: str          # "RESOURCE", "BASE", "CHOKEPOINT"
    subtype: str       # "ORE", "GEM", "MIXED"
    resource_value: float # Weighted Score (Tiles + Mines)
    owner_faction: Optional[str]
    neighbors: List[int]
    bounding_box: Tuple[int, int, int, int]

    # Combat Stats (Dynamic)
    my_strength: float = 0.0
    enemy_strength: float = 0.0
    ally_strength: float = 0.0
    my_units: Dict[str, int] = field(default_factory=dict)
    enemy_units: Dict[str, int] = field(default_factory=dict)
    ally_units: Dict[str, int] = field(default_factory=dict)

    # Structure Stats (Dynamic, includes frozen buildings)
    my_structures: Dict[str, int] = field(default_factory=dict)
    enemy_structures: Dict[str, int] = field(default_factory=dict)
    ally_structures: Dict[str, int] = field(default_factory=dict)
```

## 5. 调试与可视化 (Debugging)

### 5.1 可视化脚本
运行以下命令启动可视化 Web Server：
```bash
python scripts/visualize_intel.py
```
为了方便调试 ZoneManager 的拓扑生成逻辑，项目提供了可视化工具。
访问 `http://localhost:8000` 即可查看实时的 Zone 分布、连接关系和资源评分。

### 5.2 结构化日志
上述脚本运行时，会自动在项目根目录生成 `debug_zone_topology.md` 文件。该文件包含了当前所有 Zone 的详细属性（坐标、价值、归属、邻居、兵力统计等），格式为 **JSON**，不仅方便开发者阅读，也可直接作为 Context 提供给 LLM 进行地图理解。

```json
{
  "width": 130,
  "height": 130,
  "zones": [
    {
      "id": 14,
      "center": { "x": 81, "y": 55 },
      "type": "SUB_BASE",
      "subtype": "ORE",
      "radius": 5,
      "resource_value": 89.0,
      "owner_faction": "己方",
      "combat_strength": {
        "my": 133.0,
        "enemy": 0.0,
        "ally": 0.0
      },
      "units": {
        "my": { "e1": 5, "3tnk": 8, "ftur": 1, "4tnk": 2 },
        "enemy": {},
        "ally": {}
      },
      "structures": {
        "my": { "powr": 2, "fact": 1 },
        "enemy": { "turret": 1 },
        "ally": {}
      },
      "neighbors": [7, 8, 12, 13, 17, 18, 19]
    }
  ]
}
```

