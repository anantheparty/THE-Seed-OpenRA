# 情报服务指南 (Intelligence Service Guide)

`IntelligenceService` 是 OpenRA 智能体的核心感知模块，负责与游戏引擎通信、同步全图状态，并为上层决策模块（如 `ZoneManager`、`GlobalBlackboard`）提供清洗后的结构化数据。

## 1. 模块职责
- **数据同步 (Sync)**: 定期轮询 GameAPI，拉取地图、单位、资源、迷雾等原始数据。
- **数据清洗 (Clean)**: 过滤无效数据，识别关键实体（如矿柱、基地）。
- **状态注入 (Inject)**: 将处理后的数据注入到 `ZoneManager` 和 `GlobalBlackboard` 中。

## 2. 代码架构与目录 (Code Architecture & Catalog)

本节详细列出 `IntelligenceService` (`openra_api/intel/intelligence_service.py`) 的内部实现细节与数据流向，方便开发者维护。

### 2.1 查询层 (`_query_game_state`)
负责与 GameAPI 交互，获取原始数据并进行初步清洗。该层**不包含复杂业务逻辑**，主要关注数据的获取与过滤。

| 查询指令 (API Command) | 对应代码逻辑 | 获取数据 (Raw Data) | 备注 (Notes) |
| :--- | :--- | :--- | :--- |
| `player_baseinfo_query` | `_query_game_state` | 玩家资金、电力、资源 | 基础经济数据，用于决策是否建造/生产 |
| `screen_info_query` | `_query_game_state` | 屏幕视口信息 (Viewport) | 用于判断当前视野位置 |
| `map_query` | `_query_game_state` (按需调用) | 地图尺寸、地形、资源分布 | **低频调用** (约60s/次)，数据量大，包含 `ResourcesType` |
| `query_actor` | `_query_game_state` | 全图单位列表 (Actors) | **核心查询**。包含以下过滤逻辑：<br>1. **全局屏蔽**: 剔除 `husk` (残骸)<br>2. **中立白名单**: 仅保留 `mine`, `crate`, `油井`<br>3. **冻结处理**: 自动合并 `frozenActors` |

### 2.2 处理层 (`_process_game_state`)
负责将 `RawGameState` 转化为业务对象，并分发给各子系统。

| 处理逻辑 (Process Logic) | 关键操作 (Key Operations) | 下游消费者 (Downstream Consumers) |
| :--- | :--- | :--- |
| **地图结构更新** (Map Update) | 1. 提取中立矿柱 (`mine`)<br>2. 调用 `ZoneManager.update_from_map_query` 构建拓扑<br>3. 写入黑板 | **ZoneManager**: 用于构建战区拓扑 (Hybrid Topology)<br>**GlobalBlackboard**: `map_width`, `map_height`, `zone_manager` |
| **基地归属判定** (Base Ownership) | 1. 传入全量清洗后的 `all_actors`<br>2. 调用 `ZoneManager.update_bases` 计算基地位置与归属 | **ZoneManager**: 动态更新战区归属 (`Owner`) 和基地坐标<br>*(ZoneManager 随后会被注入黑板供其他 Agent 使用)* |
| **通用情报更新** (General Intel) | 1. 解析 `base_info` (资金/电力)<br>2. 解析 `screen_info`<br>3. 写入黑板 | **GlobalBlackboard**: `cash`, `resources`, `power`, `player_info`, `screen_info` |

### 2.3 数据流向图 (Data Flow)
```mermaid
graph LR
    GameAPI[Game Engine API] -->|Raw JSON| QueryLayer[_query_game_state]
    QueryLayer -->|RawGameState (Filtered)| ProcessLayer[_process_game_state]
    ProcessLayer -->|Topology & Bases| ZoneManager
    ProcessLayer -->|Intel & Stats| GlobalBlackboard
    ZoneManager -->|Inject| GlobalBlackboard
```

## 3. API 实测记录 (Technical Findings)
以下基于真实游戏环境的测试结果，后续开发者在修改查询逻辑时请务必参考：

### 3.1 单位查询 (`query_actor`)
- **阵营过滤 (Faction)**:
  - 必须显式遍历查询 `["己方", "敌方", "友方", "中立"]` 才能获取全图单位。
  - 默认参数（不传 `faction`）通常只返回 `己方` 单位。
- **数据清洗 (Data Cleaning)**:
  - **全局屏蔽 (Blocklist)**:
    - 过滤 `husk` (残骸): 避免决策智能体将残骸误判为活跃威胁。
  - **中立阵营白名单 (Neutral Allowlist)**:
    - 仅保留有用实体，过滤无关干扰项 (如 `mpspawn` 路径点)。
    - **保留项**:
      - `mine`: 矿柱 (Ore Mine Structure)，用于 Zone 拓扑锚定。
      - `gmine`: 宝石矿柱 (Gem Mine Structure)。
      - `crate`: 奖励箱 (Box)。
      - `油井`: 可占领经济建筑 (Tech Oil Derrick)。
    - **被过滤项**:
      - `mpspawn`: 实测为出生点/路径点标记 (单局可多达27+个)，对寻找敌方基地无实际参考价值，视为噪声。
      - `derrick`: 英文关键字已移除，统一使用中文 `"油井"`。
      - `gem`/`ore`: 属于地图资源层 (`map_query`)，不属于实体层 (`query_actor`)。
- **关键实体识别**:
  - **油井 (Oil Derrick)**:
    - 中文环境下 Type 名称通常为 `"油井"`。
    - 可被工程师占领，持续提供资金。
  - **矿柱 (Mines)**:
    - 属于 `faction="中立"`。
    - Type 关键字: `mine` (黄金矿柱), `gmine` (宝石矿柱)。
    - 注意: 这与地表铺设的矿石 (Resources) 不同，这是会再生矿石的建筑结构。
- **冻结状态 (Frozen Actors)**:
  - API 返回数据中包含独立的 `frozenActors` 字段。
  - `IntelligenceService` 已将其合并入主 `actors` 列表，并标记 `is_frozen=True`。
  - **用途**: 仅供 `ZoneManager` 用于推断敌方建筑位置，**严禁**用于 `CombatAgent` 的攻击目标选择。

### 3.2 基础信息 (`player_baseinfo_query`)
返回的数据结构如下：
```json
{
  "Cash": 2125,          // 初始携带资金 (Starting Cash)
  "Resources": 0,        // 采矿获取资金 (Harvested Resources)
  // Total Funds = Cash + Resources
  "Power": 10,           // 净电力盈余 (Provided - Drained) ? 需核实，实测 Power=10
  "PowerDrained": 90,    // 已用电力
  "PowerProvided": 100   // 总供电能力
}
```

### 3.3 地图与迷雾 (`map_query` / `fog_query`)
- `map_query` 返回全图的静态元数据（尺寸、地形、资源分布）。
- `fog_query` 用于查询单点可见性。
- **资源分布 (Resources)**:
  - `map_query` 返回的 `ResourcesType` 字段包含全图的资源类型分布。
  - **数据类型**: `int[][]` (二维整数数组)，而非文档所述的 string。
  - **实测值**:
    - `0`: **None** (无资源)。
    - `1`: **Ore** (普通矿/黄金矿)。分布广泛，对应 RA 中的金色矿堆。
    - `2`: **Gem** (宝石矿)。分布稀疏，价值更高，对应 RA 中的彩色宝石矿堆。
  - **注意**:
    - 不要与 `query_actor` 的中立实体混淆。`query_actor` 返回的是“矿柱建筑” (`mine`/`gmine`)，而 `map_query` 返回的是地表铺设的“矿石资源”。
    - **油井 (Oil Derrick)** 不在 `map_query` 中，它是可交互的 Actor 实体，必须通过 `query_actor` 获取。
    - `ResourcesType` 中没有 `"gold"` 或 `"oil"` 字符串，早期记录的 "gold" 可能是指代数值 `1` (Ore)。
- **地形 (Terrain)**:
  - **数据类型**: `int[][]` (二维整数数组)，而非文档所述的 string。
  - **含义**: 对应地图块 (Tile) 的纹理/属性 ID。
- **注意**: `query_actor` 已经隐含了可见性过滤（只返回迷雾下的可见单位），因此通常不需要对每个单位再调一次 `fog_query`。

## 4. 维护指南
1.  **添加新查询**: 请在 `_query_game_state` 中添加，并更新 `RawGameState` 数据类。
2.  **添加新处理逻辑**: 请在 `_process_game_state` 中添加，避免污染查询逻辑。
3.  **性能注意**: `map_query` 数据量较大，建议中频调用（如 10秒/次）；`query_actor` 建议高频调用（如 1-2秒/次）。
4.  **静态数据维护**: 单位/建筑的元数据（如造价、耗电、分类）统一在 `openra_api/data/dataset.py` 中维护，`StructureData` 提供了对此的封装访问。
