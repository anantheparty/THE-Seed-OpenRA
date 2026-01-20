from __future__ import annotations
from typing import Dict, List, Any
import time
import threading

from openra_api.game_api import GameAPI
from openra_api.models import Actor, MapQueryResult, Location, TargetsQueryParam
from openra_api.intel.zone_manager import ZoneManager
from agents.global_blackboard import GlobalBlackboard
from the_seed.utils import LogManager

logger = LogManager.get_logger()

class IntelligenceService:
    """
    情报服务 (Intelligence Service)。
    负责定期扫描战场，更新 ZoneManager 和 GlobalBlackboard。
    作为 Adjutant 或后台线程的一部分运行。
    """
    
    def __init__(self, game_api: GameAPI, global_bb: GlobalBlackboard):
        self.api = game_api
        self.bb = global_bb
        self.zone_manager = ZoneManager()
        
        self.last_map_update = 0
        self.last_heatmap_update = 0
        
        # 配置
        self.map_update_interval = 60.0  # 60秒更新一次全图（主要针对矿区变化/迷雾探索）
        self.heatmap_update_interval = 2.0  # 2秒更新一次热力图（兵力移动）
        
        # 初始化状态
        self._map_initialized = False

    def tick(self):
        """主循环 Tick，由 Agent 或 Scheduler 调用"""
        now = time.time()
        
        # 1. 低频更新：全图结构与资源
        if not self._map_initialized or (now - self.last_map_update > self.map_update_interval):
            self._update_map_structure()
            self.last_map_update = now
            
        # 2. 高频更新：单位与热力图
        if now - self.last_heatmap_update > self.heatmap_update_interval:
            self._update_heatmap()
            self.last_heatmap_update = now

    def _update_map_structure(self):
        """查询全图信息，更新 ZoneManager"""
        try:
            # map_query 可能比较耗时
            map_data = self.api.query_map()
            self.zone_manager.update_from_map_query(map_data)
            
            # 更新黑板
            self.bb.update_intelligence("map_width", map_data.MapWidth)
            self.bb.update_intelligence("map_height", map_data.MapHeight)
            self.bb.update_intelligence("zone_manager", self.zone_manager) # 引用传递
            
            logger.info(f"Map structure updated. Zones: {len(self.zone_manager.zones)}")
            self._map_initialized = True
            
        except Exception as e:
            logger.error(f"Failed to update map structure: {e}")

    def _update_heatmap(self):
        """查询所有单位，更新热力图和基地信息"""
        if not self._map_initialized:
            return

        try:
            # 1. 获取所有单位
            # 注意：query_actor 默认返回所有单位吗？
            # 假设我们需要明确指定 type=None 获取所有
            query_params = TargetsQueryParam(type=None) # All units
            # GameAPI.query_actor 的参数可能是 dict 或 object，视封装而定
            # 查看 models.py, TargetsQueryParam 是 dataclass
            # GameAPI.query_actor 接受 dict
            
            all_units = self.api.query_actor(query_params.to_dict())
            
            # 2. 筛选基地并更新 ZoneManager
            # 传入所有单位，由 ZoneManager 内部识别建筑
            self.zone_manager.update_bases(all_units)
            
            # 3. 计算热力图
            # Heatmap Structure: ZoneID -> { "my_power": int, "enemy_power": int, "unit_counts": dict }
            heatmap = {}
            
            # 预填充所有 Zone
            for zone_id in self.zone_manager.zones:
                heatmap[zone_id] = {
                    "my_power": 0, 
                    "enemy_power": 0, 
                    "my_units": 0,
                    "enemy_units": 0
                }
            
            for unit in all_units:
                if not unit.position:
                    continue
                    
                zone_id = self.zone_manager.get_zone_id(unit.position)
                if zone_id == 0:
                    continue
                    
                if zone_id not in heatmap:
                     heatmap[zone_id] = {
                        "my_power": 0, 
                        "enemy_power": 0, 
                        "my_units": 0,
                        "enemy_units": 0
                    }
                
                # 简单战力计算 (以后可以查表获取单位价值)
                power = 10 
                # 假设 faction 识别：我的 faction 可以从哪里获取？
                # GameAPI.get_player_info()? 
                # 暂时假设我们不知道自己的 faction name，需要额外获取
                # 或者通过 unit.faction 是否等于 "player" (OpenRA 有时用 Generic names)
                # 通常 API 会返回 relation? 目前 Actor 模型只有 faction string。
                # 暂时假设：需要从外部注入 my_faction
                
                # 这是一个 Hack，假设第一个单位的 faction 是我？不靠谱。
                # 应该在 GlobalBlackboard 存储 my_faction。
                # 暂时把所有单位都算进去，后面再区分敌我。
                
                # 检查 GlobalBlackboard 是否有 my_faction
                my_faction = self.bb.get_intelligence("my_faction")
                
                is_mine = (unit.faction == my_faction) if my_faction else True # 默认算自己的？
                
                if is_mine:
                    heatmap[zone_id]["my_power"] += power
                    heatmap[zone_id]["my_units"] += 1
                else:
                    heatmap[zone_id]["enemy_power"] += power
                    heatmap[zone_id]["enemy_units"] += 1

            # 4. 写入黑板
            self.bb.update_intelligence("heatmap", heatmap)
            self.bb.update_intelligence("last_updated", time.time())
            
            # logger.debug(f"Heatmap updated. Active Zones: {len(heatmap)}")

        except Exception as e:
            logger.error(f"Failed to update heatmap: {e}")

