from __future__ import annotations
from typing import Any, Dict, List, Optional, Protocol
from dataclasses import dataclass, field
import logging
import time

from ..api_client import GameAPI
from ..models import Actor, MapQueryResult, Location, TargetsQueryParam
from .zone_manager import ZoneManager
logger = logging.getLogger(__name__)


class IntelligenceSink(Protocol):
    def update_intelligence(self, key: str, value: Any) -> None:
        ...


@dataclass
class RawGameState:
    timestamp: float
    map_info: Optional[MapQueryResult] = None
    base_info: Optional[Dict] = None
    screen_info: Optional[Dict] = None
    all_actors: List[Actor] = field(default_factory=list)


class IntelligenceService:
    def __init__(self, game_api: GameAPI, global_bb: IntelligenceSink):
        self.api = game_api
        self.bb = global_bb
        self.zone_manager = ZoneManager()
        self.last_map_update = 0
        self.last_unit_update = 0
        self.map_update_interval = 10.0
        self.unit_update_interval = 2.0
        self._map_initialized = False
        self.mine_keywords = {"mine", "gmine"}

    def tick(self):
        now = time.time()
        need_map_update = not self._map_initialized or (now - self.last_map_update > self.map_update_interval)
        need_unit_update = now - self.last_unit_update > self.unit_update_interval
        if need_map_update or need_unit_update:
            try:
                raw_state = self._query_game_state(query_map=need_map_update)
                self._process_game_state(raw_state, update_map=need_map_update)
                if need_map_update:
                    self.last_map_update = now
                if need_unit_update:
                    self.last_unit_update = now
            except Exception as e:
                logger.error(f"IntelligenceService tick failed: {e}", exc_info=True)

    def _query_game_state(self, query_map: bool = False) -> RawGameState:
        state = RawGameState(timestamp=time.time())
        try:
            res = self.api._send_request("player_baseinfo_query", {})
            if res and "data" in res:
                state.base_info = res["data"]
        except Exception as e:
            logger.warning(f"Failed to query player base info: {e}")
        try:
            res = self.api._send_request("screen_info_query", {})
            if res and "data" in res:
                state.screen_info = res["data"]
        except Exception as e:
            logger.warning(f"Failed to query screen info: {e}")
        if query_map:
            try:
                state.map_info = self.api.query_map()
            except Exception as e:
                logger.error(f"Failed to query map: {e}")
        factions = ["己方", "敌方", "友方", "中立"]
        all_actors = []
        seen = set()
        blocklist_keywords = ["husk"]
        neutral_allowlist = ["mine", "gmine", "crate", "油井"]
        for faction in factions:
            try:
                query_params = TargetsQueryParam(faction=faction, range="all")
                actors = self.api.query_actor(query_params)
                if actors:
                    filtered_actors = []
                    for actor in actors:
                        type_lower = str(actor.type).lower()
                        if any(b in type_lower for b in blocklist_keywords):
                            continue
                        if faction == "中立":
                            if not any(a in type_lower for a in neutral_allowlist):
                                continue
                        if actor.is_frozen and faction != "敌方":
                            # frozenActors 只应计入敌方视角，避免多阵营查询导致建筑数量翻倍
                            continue
                        position = actor.position
                        key = (
                            actor.faction,
                            actor.type,
                            position.x if position else None,
                            position.y if position else None,
                            actor.is_frozen,
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        filtered_actors.append(actor)
                    all_actors.extend(filtered_actors)
            except Exception as e:
                logger.warning(f"Failed to query actors for faction {faction}: {e}")
        state.all_actors = all_actors
        return state

    def _process_game_state(self, state: RawGameState, update_map: bool = False):
        if update_map and state.map_info:
            mines = []
            for actor in state.all_actors:
                if actor.faction == "中立" or actor.faction == "Neutral":
                    type_lower = str(actor.type).lower()
                    if any(k in type_lower for k in self.mine_keywords):
                        mines.append(actor)
            logger.info(f"Updating map structure with {len(mines)} mines detected.")
            self.zone_manager.update_from_map_query(state.map_info, mine_actors=mines)
            self._map_initialized = True
            self.bb.update_intelligence("map_width", state.map_info.MapWidth)
            self.bb.update_intelligence("map_height", state.map_info.MapHeight)
            self.bb.update_intelligence("zone_manager", self.zone_manager)
        if state.all_actors:
            self.zone_manager.update_bases(state.all_actors, my_faction="己方", ally_factions=["友方"])
            self.zone_manager.update_combat_strength(state.all_actors, my_faction="己方", ally_factions=["友方"])
        if self.zone_manager.zones:
            self._update_zone_visibility()
        if state.base_info:
            self.bb.update_intelligence("player_info", state.base_info)
            cash = state.base_info.get("Cash", 0)
            resources = state.base_info.get("Resources", 0)
            self.bb.update_intelligence("cash", cash)
            self.bb.update_intelligence("resources", resources)
            self.bb.update_intelligence("total_funds", cash + resources)
            self.bb.update_intelligence("power", state.base_info.get("Power", 0))
        if state.screen_info:
            self.bb.update_intelligence("screen_info", state.screen_info)
        self.bb.update_intelligence("last_updated", state.timestamp)

    def _update_zone_visibility(self) -> None:
        for zone in self.zone_manager.zones.values():
            center = zone.center
            if not center:
                zone.is_visible = None
                zone.is_explored = None
                continue
            try:
                fog = self.api.fog_query(center)
                zone.is_visible = fog.get("IsVisible")
                zone.is_explored = fog.get("IsExplored")
            except Exception as e:
                logger.warning(f"Failed to query fog for zone {zone.id}: {e}")
                zone.is_visible = None
                zone.is_explored = None
