# 交接文档 (Handover to Next Developer)

**日期**: 2026-01-20
**当前阶段**: Phase 2 完成 -> Phase 3 启动

## 1. 已完成工作 (Accomplishments)

### 1.1 基础架构 (Phase 1)
- **Global Blackboard**: 实现了全局信息共享中心 (`agents/global_blackboard.py`)。
- **Base Agent**: 封装了标准 Agent 基类，支持 FSM 和信号通信。
- **Main Loop**: 重构了 `main_mas.py`，支持多 Agent 轮询调度。

### 1.2 感知与通信 (Phase 2)
- **ZoneManager (核心)**: 
    - 实现了 **混合拓扑策略** (DBSCAN + 矿柱锚定)，解决了矿区识别和战术分区问题。
    - 详见文档: `docs/ZONE_MANAGER_GUIDE.md`。
- **信号系统**: 
    - 实现了 Agent 间的信号收发 (`send_signal`, `get_new_signals`)。
    - 接口已暴露给 runtime，LLM 可直接调用。
- **API 验证**:
    - 确认 `query_actor` 可查询友军 (`faction="友方"`) 和矿柱 (`type="mine"` + `faction="中立"`).
    - 确认 API 受战争迷雾限制，只能查询可见单位。

## 2. 当前代码状态
- **核心模块**: `openra_api/intel/zone_manager.py` (稳定，已测试)
- **测试用例**: `tests/test_zone_manager_refactor.py` (全部通过)
- **蓝图状态**: `REFACTOR_BLUEPRINT.md` 已更新最新进度和技术发现。

## 3. 下一步任务 (Phase 3: 运营专家)

接下来的重点是开发 **Economy Agent**，剥离生产逻辑。

### Task 3.1: 资源监控器 (Resource Monitor)
- **目标**: 实时监控资源、电力和生产队列。
- **建议**:
    - 在 `GlobalBlackboard` 或 `EconomyAgent` 本地黑板中建立数据结构。
    - 利用 `PlayerBaseInfo` 获取资源/电力。
    - 利用 `ZoneManager.zones` 获取矿区分布和归属 (Is Friendly?)。

### Task 3.2: 生产循环 (Production Loop)
- **目标**: 迁移并优化原有的 MacroActions。
- **挑战**:
    - 处理 "卡钱"、"卡电" 状态。
    - 实现 "采矿车饱和度" 逻辑 (基于 Zone 资源量和矿车数量)。

## 4. 附录：ZoneManager 核心指南

**位置**: `openra_api/intel/zone_manager.py`  
**核心架构**: 混合拓扑 (Hybrid Topology)

### 4.1 混合拓扑策略
ZoneManager 采用 **"全图覆盖 + 局部修正"** 策略：
1.  **第一层 (全图基准)**: 使用 `MapQueryResult` (全图资源网格) 进行 **DBSCAN** 聚类。这保证了即使在迷雾中也能识别所有潜在矿区。
2.  **第二层 (局部修正)**: 如果 Zone 内存在可见的 **矿柱 Actor** (Mine)，将 Zone 中心强制 **锚定 (Snap)** 到矿柱位置，确保采矿车导航精度。

### 4.2 邻居网络 (Gabriel Graph)
- **连接规则**: 使用 **Gabriel Graph** 算法构建拓扑。
- **定义**: 两个 Zone (A, B) 相连，当且仅当以 AB 为直径的圆内不包含任何其他 Zone C。
- **优势**: 无需人工设定距离阈值，自动适应地图尺度，保证连接的无阻挡性。

### 4.3 关键接口
```python
# 初始化 (通常在游戏开始时)
zm = ZoneManager()
zm.update_from_map_query(map_data, mine_actors=[...]) 

# 运行时更新 (刷新盟友状态)
zm.update_bases(all_units, my_faction="Soviet", ally_factions=["Cuba"])

# 资源刷新 (低频)
zm.update_resource_values(map_data)

# 战术寻路
path_zones = zm.find_path(start_zone_id, end_zone_id)
```

祝开发顺利！
