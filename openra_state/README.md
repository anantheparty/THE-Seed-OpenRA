# openra_state 模块使用说明

`openra_state` 提供与 OpenRA 游戏状态交互、情报聚合与区域拓扑分析的能力，可独立运行可视化，也可作为项目模块嵌入其他系统。

## 1. 目录结构
- `openra_api/`: 统一的 OpenRA Socket API 客户端与数据模型（本仓库主入口）
- `data/`: 静态数据与元信息封装
- `intel/`: 情报服务与 ZoneManager
- `static/`: 可视化页面
- `visualize_intel.py`: 情报可视化服务
- `docs/`: 设计与维护文档

## 2. 独立运行
### 2.1 启动可视化
在项目根目录执行：
```bash
python openra_state/visualize_intel.py
```

浏览器访问：
```
http://localhost:8000
```

该脚本会：
- 启动 HTTP 服务
- 定期调用 `IntelligenceService.tick()`
- 将 ZoneManager 数据返回给前端页面

## 3. 作为模块使用
### 3.1 GameAPI 基础调用
```python
from openra_api.game_api import GameAPI
from openra_api.models import TargetsQueryParam

api = GameAPI("localhost", 7445)
actors = api.query_actor(TargetsQueryParam(faction="己方"))
```

### 3.2 IntelligenceService 集成
`IntelligenceService` 依赖一个“情报接收器”对象，用于写入聚合后的情报。该对象只需实现：

```python
class IntelligenceSink:
    def update_intelligence(self, key: str, value: object) -> None:
        ...
```

示例：
```python
from openra_api.game_api import GameAPI
from openra_state.intel.intelligence_service import IntelligenceService

class DummySink:
    def __init__(self):
        self.data = {}

    def update_intelligence(self, key, value):
        self.data[key] = value

api = GameAPI("localhost", 7445)
sink = DummySink()
intel = IntelligenceService(api, sink)
intel.tick()
```

### 3.3 ZoneManager 独立使用
```python
from openra_state.intel.zone_manager import ZoneManager

zm = ZoneManager()
zm.update_from_map_query(map_data, mine_actors=visible_mines)
zm.update_bases(all_units, my_faction="己方", ally_factions=["友方"])
```

## 4. 上下游参数规范
### 4.1 输入来源
- `GameAPI`: 负责返回 `MapQueryResult`、`Actor` 等结构化数据
- `IntelligenceService`: 负责按频率拉取与清洗数据

### 4.2 输出写入
`IntelligenceService` 会调用情报接收器写入以下键：
- `map_width`: 地图宽度
- `map_height`: 地图高度
- `zone_manager`: ZoneManager 实例
- `player_info`: 原始基地信息
- `cash`, `resources`, `total_funds`, `power`
- `screen_info`: 屏幕信息
- `last_updated`: 当前 tick 时间戳

## 5. 相关文档
- `docs/INTELLIGENCE_SERVICE_GUIDE.md`
- `docs/ZONE_MANAGER_GUIDE.md`
