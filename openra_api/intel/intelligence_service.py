from __future__ import annotations
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
import time

from openra_api.game_api import GameAPI
from openra_api.models import Actor, MapQueryResult, Location, TargetsQueryParam
from openra_api.intel.zone_manager import ZoneManager
from agents.global_blackboard import GlobalBlackboard
from the_seed.utils import LogManager

logger = LogManager.get_logger()

@dataclass
class RawGameState:
    """
    原始游戏状态快照。
    存储从 GameAPI 查询到的直接结果，不做复杂处理。
    """
    timestamp: float
    map_info: Optional[MapQueryResult] = None
    base_info: Optional[Dict] = None
    screen_info: Optional[Dict] = None
    # 所有的单位 (包含己方、敌方、友方、中立)
    all_actors: List[Actor] = field(default_factory=list)

class IntelligenceService:
    """
    情报服务 (Intelligence Service)。
    负责定期扫描战场，更新 ZoneManager 和 GlobalBlackboard。
    
    维护指南:
    1. 查询层 (_query_game_state): 负责与 GameAPI 交互，获取原始数据。添加新的 query 请在此处。
    2. 处理层 (_process_game_state): 负责清洗数据并更新各子系统 (ZoneManager, Blackboard)。
    """
    
    def __init__(self, game_api: GameAPI, global_bb: GlobalBlackboard):
        self.api = game_api
        self.bb = global_bb
        self.zone_manager = ZoneManager()
        
        self.last_map_update = 0
        self.last_unit_update = 0
        
        # 配置
        self.map_update_interval = 10.0  # 10秒更新一次全图（加快刷新频率以适应即时战略需求）
        self.unit_update_interval = 2.0  # 2秒更新一次单位状态（兵力移动）
        
        # 初始化状态
        self._map_initialized = False
        
        # 矿柱识别关键字 (忽略大小写)
        # mine: 矿柱 (拓扑锚定点)
        # gmine: 宝石矿柱
        self.mine_keywords = {"mine", "gmine"}

    def tick(self):
        """主循环 Tick，由 Agent 或 Scheduler 调用"""
        now = time.time()
        
        # 决定是否需要更新
        need_map_update = not self._map_initialized or (now - self.last_map_update > self.map_update_interval)
        need_unit_update = now - self.last_unit_update > self.unit_update_interval
        
        if need_map_update or need_unit_update:
            try:
                # 1. 查询层：获取全量数据
                raw_state = self._query_game_state(query_map=need_map_update)
                
                # 2. 处理层：分发数据
                self._process_game_state(raw_state, update_map=need_map_update)
                
                if need_map_update:
                    self.last_map_update = now
                if need_unit_update:
                    self.last_unit_update = now
                    
            except Exception as e:
                logger.error(f"IntelligenceService tick failed: {e}", exc_info=True)

    def _query_game_state(self, query_map: bool = False) -> RawGameState:
        """
        [查询层] 执行 Socket 查询，聚合所有原始数据。
        """
        state = RawGameState(timestamp=time.time())
        
        # 1. 基础信息查询
        # 查询玩家资源、电力 (PlayerBaseInfo)
        try:
            # 注意：API 返回的是 dict，非 dataclass
            res = self.api._send_request("player_baseinfo_query", {})
            if res and "data" in res:
                state.base_info = res["data"]
        except Exception as e:
            logger.warning(f"Failed to query player base info: {e}")

        # 查询屏幕信息 (ScreenInfo)
        try:
            res = self.api._send_request("screen_info_query", {})
            if res and "data" in res:
                state.screen_info = res["data"]
        except Exception as e:
            logger.warning(f"Failed to query screen info: {e}")

        # 2. 地图查询 (按需)
        if query_map:
            try:
                state.map_info = self.api.query_map()
            except Exception as e:
                logger.error(f"Failed to query map: {e}")

        # 3. 单位查询 (全量遍历所有阵营)
        # 注意：必须显式遍历 ["己方", "敌方", "友方", "中立"]
        factions = ["己方", "敌方", "友方", "中立"]
        all_actors = []
        
        # 过滤配置
        # 1. 全局屏蔽词: 移除残骸等无用实体
        blocklist_keywords = ["husk"]
        # 2. 中立白名单: 仅保留关键实体
        # mine: 矿柱 (Ore Mine Structure)
        # gmine: 宝石矿柱 (Gem Mine Structure)
        # crate: 宝箱 (Crate)
        # 油井: 科技油井 (Tech Oil Derrick)
        neutral_allowlist = ["mine", "gmine", "crate", "油井"]
        
        for faction in factions:
            try:
                # API 要求传入 targets 参数
                query_params = TargetsQueryParam(faction=faction, range="all")
                actors = self.api.query_actor(query_params)
                if actors:
                    # 应用过滤逻辑
                    filtered_actors = []
                    for actor in actors:
                        type_lower = str(actor.type).lower()
                        
                        # 1. 全局屏蔽检查 (如 husk)
                        if any(b in type_lower for b in blocklist_keywords):
                            continue
                            
                        # 2. 中立阵营白名单检查
                        if faction == "中立":
                            if not any(a in type_lower for a in neutral_allowlist):
                                continue
                                
                        filtered_actors.append(actor)
                        
                    all_actors.extend(filtered_actors)
            except Exception as e:
                logger.warning(f"Failed to query actors for faction {faction}: {e}")
        
        state.all_actors = all_actors
        return state

    def _process_game_state(self, state: RawGameState, update_map: bool = False):
        """
        [处理层] 清洗数据并更新子系统。
        """
        # 1. 更新 ZoneManager (若有新地图数据)
        if update_map and state.map_info:
            # 过滤出矿柱 Actor
            # 规则：faction="中立" 且 type 名称包含特定关键字
            mines = []
            for actor in state.all_actors:
                # 简单判断：faction 为中立
                if actor.faction == "中立" or actor.faction == "Neutral":
                    # 名称检查
                    type_lower = str(actor.type).lower()
                    if any(k in type_lower for k in self.mine_keywords):
                        mines.append(actor)
            
            logger.info(f"Updating map structure with {len(mines)} mines detected.")
            self.zone_manager.update_from_map_query(state.map_info, mine_actors=mines)
            self._map_initialized = True
            
            # 写入黑板
            self.bb.update_intelligence("map_width", state.map_info.MapWidth)
            self.bb.update_intelligence("map_height", state.map_info.MapHeight)
            self.bb.update_intelligence("zone_manager", self.zone_manager)

        # 2. 更新 Zone 归属 (基于单位)
        if state.all_actors:
            # 更新 ZoneManager 的动态归属 (Owner/IsFriendly)
            # 这里我们假定 my_faction="己方", ally_factions=["友方"]
            # 因为 query_actor 返回的就是这些中文字符串
            self.zone_manager.update_bases(
                state.all_actors, 
                my_faction="己方", 
                ally_factions=["友方"]
            )

        # 3. 更新 Blackboard 通用情报
        if state.base_info:
            self.bb.update_intelligence("player_info", state.base_info)
            # 也可以拆开存
            cash = state.base_info.get("Cash", 0)
            resources = state.base_info.get("Resources", 0)
            self.bb.update_intelligence("cash", cash)
            self.bb.update_intelligence("resources", resources)
            self.bb.update_intelligence("total_funds", cash + resources)
            self.bb.update_intelligence("power", state.base_info.get("Power", 0))

        if state.screen_info:
            self.bb.update_intelligence("screen_info", state.screen_info)
            
        self.bb.update_intelligence("last_updated", state.timestamp)
        # logger.debug(f"Intelligence updated. Actors: {len(state.all_actors)}")
