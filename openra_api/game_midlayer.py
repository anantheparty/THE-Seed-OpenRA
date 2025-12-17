from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .game_api import GameAPI, GameAPIError
from .intel_rules import (
    DEFAULT_HIGH_VALUE_TARGETS,
    DEFAULT_NAME_ALIASES,
    DEFAULT_UNIT_CATEGORY_RULES,
    DEFAULT_UNIT_VALUE_WEIGHTS,
)
from .models import Actor, Location, MapQueryResult, TargetsQueryParam

logger = logging.getLogger(__name__)

HIGH_VALUE_TARGETS = DEFAULT_HIGH_VALUE_TARGETS
UNIT_CATEGORY_RULES = DEFAULT_UNIT_CATEGORY_RULES
UNIT_VALUE_WEIGHTS = DEFAULT_UNIT_VALUE_WEIGHTS


def normalize_unit_name(name: Optional[str]) -> str:
    if not name:
        return "未知"
    return DEFAULT_NAME_ALIASES.get(name, name)


@dataclass(frozen=True)
class ActorView:
    """Actor 的轻量快照视图"""

    id: str
    type: str
    faction: str
    pos: Location
    hp_percent: int

    @classmethod
    def from_actor(cls, actor: Actor) -> "ActorView":
        actor_id = getattr(actor, "actor_id", getattr(actor, "id", None))
        actor_type = normalize_unit_name(getattr(actor, "type", getattr(actor, "unit_type", "未知")) or "未知")
        faction = getattr(actor, "faction", "未知") or "未知"
        hp_percent = getattr(actor, "hp_percent", getattr(actor, "hppercent", -1))

        raw_pos = getattr(actor, "position", None)
        if isinstance(raw_pos, Location):
            pos = raw_pos
        elif isinstance(raw_pos, dict):
            pos = Location(raw_pos.get("x", 0), raw_pos.get("y", 0))
        else:
            x = getattr(raw_pos, "x", 0)
            y = getattr(raw_pos, "y", 0)
            pos = Location(x, y)

        return cls(
            id=str(actor_id) if actor_id is not None else "unknown",
            type=str(actor_type),
            faction=str(faction),
            pos=pos,
            hp_percent=int(hp_percent) if isinstance(hp_percent, (int, float)) else -1,
        )


class MapAccessor:
    """统一的地图访问工具，自动处理 row/col major 差异"""

    def __init__(self, map_info: MapQueryResult) -> None:
        self.map_info = map_info
        self.width = map_info.MapWidth or 0
        self.height = map_info.MapHeight or 0
        # 探测存储方式：len == width 视为列主（x 索引第一维），否则行主
        self.col_major = False
        explored = map_info.IsExplored or []
        if explored:
            self.col_major = len(explored) == self.width and self.width > 0

    def _get_cell(self, grid: List[List[Any]], x: int, y: int) -> Any:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return None
        if not grid:
            return None
        try:
            if self.col_major:
                return grid[x][y]
            return grid[y][x]
        except (IndexError, TypeError):
            return None

    def is_explored(self, x: int, y: int) -> bool:
        return bool(self._get_cell(self.map_info.IsExplored or [], x, y))

    def is_visible(self, x: int, y: int) -> bool:
        return bool(self._get_cell(self.map_info.IsVisible or [], x, y))

    def resource(self, x: int, y: int) -> Any:
        return self._get_cell(self.map_info.Resources or [], x, y)


@dataclass
class IntelMemory:
    """内部记忆，用于差分估计与 last-seen"""

    last_resources: Optional[float] = None
    last_time: Optional[float] = None
    prev_snapshot_time: Optional[float] = None
    last_snapshot_time: Optional[float] = None
    last_explored_ratio: Optional[float] = None
    enemy_last_seen: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    map_cache: Optional[Tuple[float, MapQueryResult]] = None
    queues_cache: Dict[str, Tuple[float, Dict[str, Any]]] = field(default_factory=dict)
    attributes_cache: Optional[Tuple[float, Dict[str, Any], Tuple[str, ...]]] = None
    scout_stalled: bool = False


@dataclass
class IntelModel:
    """仅承载情报数据的结构体"""

    meta: Dict[str, Any]
    economy: Dict[str, Any]
    tech: Dict[str, Any]
    forces: Dict[str, Any]
    battle: Dict[str, Any]
    opportunities: List[Dict[str, Any]]
    map_control: Dict[str, Any]
    alerts: List[str]
    legacy: Dict[str, Any]


class IntelSerializer:
    """负责将 IntelModel 序列化为对外结构"""

    @staticmethod
    def _prune_compact(data: Any, minimal_coords: bool) -> Any:
        preserve_zero_keys = {"width", "height", "tiles"}
        if isinstance(data, dict):
            pruned: Dict[str, Any] = {}
            for k, v in data.items():
                if v in (None, {}, [], ()):
                    continue
                if isinstance(v, (int, float)) and v == 0 and k not in preserve_zero_keys:
                    continue
                if minimal_coords and k in {"nearby_unexplored", "frontier_points"}:
                    continue
                if minimal_coords and k == "resource_summary" and isinstance(v, dict):
                    compact_rs = {kk: vv for kk, vv in v.items() if kk == "tiles"}
                    if compact_rs:
                        pruned[k] = compact_rs
                    continue
                pruned_v = IntelSerializer._prune_compact(v, minimal_coords)
                if pruned_v not in (None, {}, [], ()):
                    pruned[k] = pruned_v
            return pruned
        if isinstance(data, list):
            pruned_list = []
            for item in data:
                if minimal_coords and isinstance(item, dict):
                    item = {k: v for k, v in item.items() if k not in {"pos", "centroid", "nearest_to_base"}}
                pruned_item = IntelSerializer._prune_compact(item, minimal_coords)
                if pruned_item not in (None, {}, [], ()):
                    pruned_list.append(pruned_item)
            return pruned_list
        return data

    @staticmethod
    def to_debug(model: IntelModel) -> Dict[str, Any]:
        return {
            "t": model.meta.get("game_time"),
            "meta": model.meta,
            "economy": model.economy,
            "tech": model.tech,
            "forces": model.forces,
            "battle": model.battle,
            "opportunities": model.opportunities,
            "map_control": model.map_control,
            "alerts": list(model.alerts),
            "legacy": model.legacy,
        }

    @staticmethod
    def to_brief(model: IntelModel) -> Dict[str, Any]:
        economy = model.economy or {}
        tech = model.tech or {}
        forces = model.forces or {}
        battle = model.battle or {}
        opportunities = model.opportunities or []
        map_control = model.map_control or {}
        alerts = list(model.alerts or [])

        tech_level = tech.get("tech_level_est", 0) or 0
        tier = min(max(int(tech_level), 0), 4)
        if tier <= 1:
            stage = "opening"
        elif tier == 2:
            stage = "mid"
        else:
            stage = "late"

        key_order = ("兵营", "车间", "雷达", "科技中心")
        owned_keys = tech.get("owned_key_buildings", {}) or {}
        next_missing = None
        for name in key_order:
            if owned_keys.get(name, 0) <= 0:
                next_missing = name
                break

        queue_blocked = "none"
        queues = economy.get("production_queues") or {}
        for q in queues.values():
            reason = q.get("queue_blocked_reason")
            if reason in ("ready_not_placed", "paused"):
                queue_blocked = reason
                if reason == "ready_not_placed":
                    break
            elif reason and queue_blocked == "none":
                queue_blocked = "unknown"

        power = economy.get("power") or {}
        power_ok = True
        surplus = power.get("surplus")
        if isinstance(surplus, (int, float)):
            power_ok = surplus >= 0

        miners = economy.get("miners")
        refineries = economy.get("refineries", 0)

        my_force = forces.get("my", {}) or {}
        enemy_force = forces.get("enemy", {}) or {}
        my_value = int(my_force.get("army_value_est", 0) or 0)
        enemy_visible = enemy_force.get("visible_units", 0) or 0
        enemy_value = None if enemy_visible == 0 else int(enemy_force.get("army_value_est", 0) or 0)

        threats = battle.get("threats_to_base") or []
        threat_near_base = "none"
        if threats:
            top = threats[0]
            dist = top.get("distance", 999)
            score = top.get("threat_score", 0)
            if dist <= 12 or score >= 220:
                threat_near_base = "high"
            elif dist <= 20 or score >= 140:
                threat_near_base = "med"
            else:
                threat_near_base = "low"

        engagements = battle.get("engagements") or {}
        engaged = bool(engagements.get("engaged_units", 0))

        best_target = None
        best_score = None
        if opportunities:
            best = opportunities[0]
            best_target = {"type": best.get("type"), "pos": best.get("pos")}
            best_score = int(best.get("opportunity_score", 0) or 0)

        explored = map_control.get("explored_ratio")
        scout_need = bool(model.meta.get("scout_stalled")) or ("侦察停滞" in alerts)
        nearest_resource = None
        rs = map_control.get("resource_summary")
        if rs and isinstance(rs, dict) and rs.get("nearest_to_base"):
            nearest_resource = rs.get("nearest_to_base")

        brief_alerts = alerts[:3]

        return {
            "t": model.meta.get("game_time"),
            "stage": stage,
            "economy": {
                "cash": economy.get("cash"),
                "power_ok": power_ok,
                "miners": miners if miners is not None else None,
                "refineries": refineries,
                "queue_blocked": queue_blocked,
            },
            "tech": {
                "tier": min(tier, 3),
                "next_missing": next_missing,
            },
            "combat": {
                "my_value": my_value,
                "enemy_value": enemy_value,
                "threat_near_base": threat_near_base,
                "engaged": engaged,
            },
            "opportunity": {
                "best_target": best_target,
                "best_score": best_score,
            },
            "map": {
                "explored": explored,
                "scout_need": scout_need,
                "nearest_resource": nearest_resource,
            },
            "alerts": brief_alerts,
        }

    @staticmethod
    def to_legacy(model: IntelModel) -> Dict[str, Any]:
        return {
            "t": model.meta.get("game_time"),
            "economy": model.economy,
            "tech": model.tech,
            "my": model.forces.get("my", {}),
            "enemy": model.forces.get("enemy", {}),
            "map": model.map_control,
            "alerts": list(model.alerts),
            "match": model.legacy.get("match", {}),
        }

@dataclass
class SkillResult:
    """宏指令执行结果"""

    ok: bool
    need_replan: bool
    reason: str = ""
    actions: List[Dict[str, Any]] = field(default_factory=list)
    observations: Dict[str, Any] = field(default_factory=dict)
    player_message: Optional[str] = None

    @classmethod
    def success(
        cls,
        reason: str = "",
        actions: Optional[List[Dict[str, Any]]] = None,
        observations: Optional[Dict[str, Any]] = None,
        player_message: Optional[str] = None,
        need_replan: bool = False,
    ) -> "SkillResult":
        return cls(
            ok=True,
            need_replan=need_replan,
            reason=reason,
            actions=actions or [],
            observations=observations or {},
            player_message=player_message,
        )

    @classmethod
    def fail(
        cls,
        reason: str,
        actions: Optional[List[Dict[str, Any]]] = None,
        observations: Optional[Dict[str, Any]] = None,
        player_message: Optional[str] = None,
        need_replan: bool = True,
    ) -> "SkillResult":
        return cls(
            ok=False,
            need_replan=need_replan,
            reason=reason,
            actions=actions or [],
            observations=observations or {},
            player_message=player_message,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "need_replan": self.need_replan,
            "reason": self.reason,
            "actions": self.actions,
            "observations": self.observations,
            "player_message": self.player_message,
        }


class IntelService:
    """负责状态采集、摘要与缓存"""

    TECH_PROBE_BUILDINGS = ("电厂", "矿场", "车间", "雷达", "科技中心", "机场")
    TECH_PROBE_UNITS = ("步兵", "矿车", "防空车", "装甲车", "重坦", "v2", "猛犸坦克")

    def __init__(
        self,
        api: GameAPI,
        cache_ttl: float = 0.25,
        map_ttl: float = 0.8,
        queues_ttl: float = 1.5,
        attributes_ttl: float = 2.0,
    ) -> None:
        self.api = api
        self.cache_ttl = cache_ttl
        self.map_ttl = map_ttl
        self.queues_ttl = queues_ttl
        self.attributes_ttl = attributes_ttl

        self._snapshot_cache: Optional[Tuple[float, Dict[str, Any]]] = None
        self._intel_cache: Optional[Tuple[float, IntelModel]] = None
        self._building_names = set(getattr(self.api, "BUILDING_DEPENDENCIES", {}).keys())
        self._unit_names = set(getattr(self.api, "UNIT_DEPENDENCIES", {}).keys())
        # 统一规范名后的集合：避免 [采矿车/矿车]、[雷达站/雷达] 造成分类失败
        self._building_names_norm = {normalize_unit_name(n) for n in self._building_names}
        self._unit_names_norm = {normalize_unit_name(n) for n in self._unit_names}
        self.memory = IntelMemory()

    def get_snapshot(self, force: bool = False) -> Dict[str, Any]:
        if not force and self._snapshot_cache and self._is_cache_valid(self._snapshot_cache[0], self.cache_ttl):
            return self._snapshot_cache[1]

        snapshot = self._fetch_snapshot()
        self._snapshot_cache = (time.time(), snapshot)
        self.memory.prev_snapshot_time = self.memory.last_snapshot_time
        self.memory.last_snapshot_time = snapshot.get("t")
        return snapshot

    def get_map_info(self, force: bool = False) -> Optional[MapQueryResult]:
        if (
            not force
            and self.memory.map_cache
            and self._is_cache_valid(self.memory.map_cache[0], self.map_ttl)
        ):
            return self.memory.map_cache[1]
        try:
            info = self._fetch_map_info()
            self.memory.map_cache = (time.time(), info)
            return info
        except GameAPIError as exc:
            logger.info("获取地图信息失败: %s", exc)
            return None

    def get_intel(self, force: bool = False) -> IntelModel:
        if not force and self._intel_cache and self._is_cache_valid(self._intel_cache[0], self.cache_ttl):
            return self._intel_cache[1]

        snapshot = self.get_snapshot(force=force)
        map_info = self.get_map_info(force=False)
        queues = self._get_production_queues()
        unit_attrs = self._get_unit_attributes(snapshot.get("my_actors", []))
        intel = self._build_intel(snapshot, map_info, queues, unit_attrs)
        self._intel_cache = (time.time(), intel)
        return intel

    def get_base_center(self, snapshot: Dict[str, Any]) -> Location:
        buildings = []
        for actor in snapshot.get("my_actors", []):
            actor_type = normalize_unit_name(getattr(actor, "type", None))
            pos = getattr(actor, "position", None)
            category = UNIT_CATEGORY_RULES.get(actor_type)
            is_building = actor_type in self._building_names_norm or category in ("building", "defense")
            if is_building and isinstance(pos, Location):
                buildings.append(pos)

        if buildings:
            avg_x = sum(pos.x for pos in buildings) // len(buildings)
            avg_y = sum(pos.y for pos in buildings) // len(buildings)
            return Location(avg_x, avg_y)

        first_actor = next(iter(snapshot.get("my_actors", [])), None)
        if first_actor and isinstance(getattr(first_actor, "position", None), Location):
            return getattr(first_actor, "position")

        return Location(0, 0)

    def _is_cache_valid(self, cached_time: float, ttl: float) -> bool:
        return (time.time() - cached_time) <= ttl

    def _fetch_snapshot(self) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {}

        try:
            snapshot["my_actors"] = self.api.query_actor(TargetsQueryParam(faction="自己"))
        except GameAPIError as exc:
            logger.warning("获取我方单位失败: %s", exc)
            snapshot["my_actors"] = []

        try:
            snapshot["enemy_actors"] = self.api.query_actor(TargetsQueryParam(faction="敌人"))
        except GameAPIError as exc:
            logger.info("获取敌方单位失败: %s", exc)
            snapshot["enemy_actors"] = []

        try:
            snapshot["base_info"] = self.api.player_base_info_query()
        except GameAPIError as exc:
            logger.info("获取基地信息失败: %s", exc)
            snapshot["base_info"] = None

        snapshot["t"] = time.time()
        return snapshot

    def _fetch_map_info(self) -> Optional[MapQueryResult]:
        return self.api.map_query()

    def _get_production_queues(self) -> Dict[str, Any]:
        queues: Dict[str, Any] = {}
        queue_types = ("Building", "Defense", "Infantry", "Vehicle", "Aircraft")
        now = time.time()
        for qtype in queue_types:
            cached = self.memory.queues_cache.get(qtype)
            if cached and self._is_cache_valid(cached[0], self.queues_ttl):
                queues[qtype] = cached[1]
                continue
            try:
                raw = self.api.query_production_queue(qtype)
            except GameAPIError as exc:
                logger.info("获取生产队列失败 [%s]: %s", qtype, exc)
                continue

            simplified_items = []
            for item in raw.get("queue_items", []):
                simplified_items.append(
                    {
                        "name": item.get("name"),
                        "display_name": item.get("chineseName"),
                        "progress": item.get("progress_percent"),
                        "status": item.get("status"),
                        "paused": item.get("paused"),
                        "owner_actor_id": item.get("owner_actor_id"),
                        "remaining_time": item.get("remaining_time"),
                        "total_time": item.get("total_time"),
                        "done": item.get("done"),
                    }
                )
            queues[qtype] = {
                "queue_type": raw.get("queue_type", qtype),
                "items": simplified_items,
                "has_ready_item": raw.get("has_ready_item", False),
                "queue_blocked_reason": self._detect_queue_block(raw),
            }
            self.memory.queues_cache[qtype] = (now, queues[qtype])
        return queues

    def _detect_queue_block(self, raw_queue: Dict[str, Any]) -> Optional[str]:
        if not raw_queue:
            return None
        # API 有时只给 has_ready_item，不一定能取到完整队列
        if raw_queue.get("has_ready_item") and raw_queue.get("queue_type") in ("Building", "Defense"):
            return "ready_not_placed"
        items = raw_queue.get("queue_items") or []
        if not items:
            return None
        head = items[0]
        # 就绪但未放置的建筑/防御
        if head.get("done") and raw_queue.get("queue_type") in ("Building", "Defense"):
            return "ready_not_placed"
        if all(item.get("paused") for item in items):
            return "paused"
        return None

    def _get_unit_attributes(self, actors: List[Actor]) -> Dict[str, Any]:
        # 仅对少量作战单位做属性查询，避免高频 RPC
        if not actors:
            return {}
        limited = actors[:15]
        actor_ids = tuple(str(getattr(a, "actor_id", getattr(a, "id", ""))) for a in limited)
        cached = self.memory.attributes_cache
        if cached and self._is_cache_valid(cached[0], self.attributes_ttl) and cached[2] == actor_ids:
            return cached[1]
        try:
            result = self.api.unit_attribute_query(limited)
            self.memory.attributes_cache = (time.time(), result, actor_ids)
            return result
        except GameAPIError as exc:
            logger.info("查询单位属性失败: %s", exc)
            return {}

    def _build_intel(
        self,
        snapshot: Dict[str, Any],
        map_info: Optional[MapQueryResult],
        queues: Dict[str, Any],
        unit_attrs: Dict[str, Any],
    ) -> IntelModel:
        my_views = [ActorView.from_actor(actor) for actor in snapshot.get("my_actors", [])]
        enemy_views = [ActorView.from_actor(actor) for actor in snapshot.get("enemy_actors", [])]
        base_center = self.get_base_center(snapshot)

        my_summary = self._summarize_actors(my_views)
        enemy_summary = self._summarize_actors(enemy_views)

        enemy_summary["threats"] = self._compute_threats(enemy_views, base_center)

        map_summary, explored_ratio = self._summarize_map(map_info, base_center)
        economy_summary = self._summarize_economy(snapshot.get("base_info"), my_summary, map_info, base_center, queues)
        tech_summary = self._summarize_tech(my_summary)
        forces = {
            "my": self._build_force_summary(my_views, my_summary),
            "enemy": self._build_force_summary(enemy_views, enemy_summary),
        }
        forces["enemy"]["threats"] = enemy_summary.get("threats", [])
        enemy_last_seen = self._update_enemy_memory(enemy_views)
        forces["enemy"]["last_seen"] = enemy_last_seen
        battle = self._build_battle_section(enemy_views, base_center, unit_attrs)
        opportunities = self._build_opportunities(enemy_views, base_center, forces["my"].get("centroid"))
        map_control = self._build_map_control(map_summary, map_info, base_center)
        alerts, scout_stalled = self._build_alerts(
            economy_summary,
            my_summary,
            forces,
            queues,
            explored_ratio,
        )

        meta = self._build_meta(snapshot, explored_ratio, scout_stalled)
        legacy = {
            "match": {},
        }
        
        return IntelModel(
            meta=meta,
            economy=economy_summary,
            tech=tech_summary,
            forces=forces,
            battle=battle,
            opportunities=opportunities,
            map_control=map_control,
            alerts=alerts,
            legacy=legacy,
        )

    def _summarize_actors(self, views: List[ActorView]) -> Dict[str, Any]:
        building_counts: Dict[str, int] = {}
        unit_counts: Dict[str, int] = {}
        unknown = 0

        for view in views:
            category = UNIT_CATEGORY_RULES.get(view.type)
            is_building = view.type in self._building_names_norm or category in ("building", "defense") or view.type.endswith(("厂", "站", "中心"))
            is_unit = view.type in self._unit_names_norm or category in ("infantry", "vehicle", "air", "harvester", "support", "mcv")
            if is_building:
                building_counts[view.type] = building_counts.get(view.type, 0) + 1
            elif is_unit:
                unit_counts[view.type] = unit_counts.get(view.type, 0) + 1
            else:
                unknown += 1

        return {
            "total": len(views),
            "buildings": building_counts,
            "units": unit_counts,
            "unknown": unknown,
        }

    def _summarize_map(self, map_info: Optional[MapQueryResult], base_center: Location) -> Tuple[Dict[str, Any], Optional[float]]:
        if not map_info:
            return (
                {
                    "size": None,
                    "explored_ratio": None,
                    "nearby_unexplored": [],
                    "frontier_points": [],
                    "frontier_count": 0,
                    "nearby_unexplored_count": 0,
                    "resource_summary": None,
                },
                None,
            )

        width = map_info.MapWidth
        height = map_info.MapHeight
        explored_ratio = None
        if map_info.IsExplored and width and height:
            explored_cells = sum(1 for column in map_info.IsExplored for explored in column if explored)
            total_cells = width * height
            explored_ratio = explored_cells / total_cells if total_cells else None

        unexplored = []
        try:
            unexplored_positions = self.api.get_unexplored_nearby_positions(map_info, base_center, max_distance=10)
            unexplored = [pos.to_dict() for pos in unexplored_positions[:5]]
        except GameAPIError as exc:
            logger.info("获取未探索区域失败: %s", exc)

        frontier_points = self._compute_frontier(map_info)
        resource_summary = self._summarize_resources(map_info, base_center)

        return (
            {
                "size": {"width": width, "height": height},
                "explored_ratio": explored_ratio,
                "nearby_unexplored": unexplored,
                "frontier_points": frontier_points,
                "frontier_count": len(frontier_points),
                "nearby_unexplored_count": len(unexplored),
                "resource_summary": resource_summary,
            },
            explored_ratio,
        )

    def _summarize_resources(self, map_info: MapQueryResult, base_center: Location) -> Optional[Dict[str, Any]]:
        resources_grid = map_info.Resources or [[]]
        positions: List[Location] = []
        for y, row in enumerate(resources_grid):
            for x, val in enumerate(row):
                if val and isinstance(val, (int, float)) and val > 0:
                    positions.append(Location(x, y))
        if not positions:
            return None

        total = len(positions)
        avg_x = sum(p.x for p in positions) / total
        avg_y = sum(p.y for p in positions) / total
        centroid = Location(int(avg_x), int(avg_y))
        nearest = min(positions, key=lambda p: p.manhattan_distance(base_center))
        return {
            "tiles": total,
            "centroid": {"x": centroid.x, "y": centroid.y},
            "nearest_to_base": nearest.to_dict(),
        }

    def _compute_frontier(self, map_info: MapQueryResult, limit: int = 12) -> List[Dict[str, int]]:
        frontier: List[Location] = []
        explored = map_info.IsExplored or []
        width = map_info.MapWidth or 0
        height = map_info.MapHeight or 0
        for y in range(min(height, len(explored))):
            row = explored[y] if y < len(explored) else []
            for x in range(min(width, len(row))):
                if not row[x]:
                    continue
                # 邻居存在未探索则视为前沿
                neighbors = [
                    (x - 1, y),
                    (x + 1, y),
                    (x, y - 1),
                    (x, y + 1),
                ]
                for nx, ny in neighbors:
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    if ny < len(explored) and nx < len(explored[ny]) and not explored[ny][nx]:
                        frontier.append(Location(x, y))
                        break
        # 抽样，避免过长
        sampled = frontier[:limit]
        return [pos.to_dict() for pos in sampled]

    def _summarize_economy(
        self,
        base_info: Any,
        my_summary: Dict[str, Any],
        map_info: Optional[MapQueryResult],
        base_center: Location,
        queues: Dict[str, Any],
    ) -> Dict[str, Any]:
        buildings = my_summary.get("buildings", {})
        units = my_summary.get("units", {})

        power_info = None
        if base_info:
            provided = getattr(base_info, "PowerProvided", None)
            drained = getattr(base_info, "PowerDrained", None)
            surplus = getattr(base_info, "Power", None)
            power_info = {"surplus": surplus, "provided": provided, "drained": drained}

        now = time.time()
        resources_now = getattr(base_info, "Resources", None) if base_info else None
        income_rate = None
        if self.memory.last_resources is not None and resources_now is not None and self.memory.last_time:
            dt = now - self.memory.last_time
            if dt > 0:
                income_rate = (resources_now - self.memory.last_resources) / dt
        self.memory.last_resources = resources_now
        self.memory.last_time = now

        harvest = {
            "miners": units.get("矿车", 0),
            "idle_miners": None,
            "nearby_resource": None,
        }
        if map_info:
            resource_summary = self._summarize_resources(map_info, base_center)
            if resource_summary:
                harvest["nearby_resource"] = resource_summary.get("nearest_to_base")

        production_queues = queues

        return {
            "cash": getattr(base_info, "Cash", None) if base_info else None,
            "resources": resources_now,
            "power": power_info,
            "refineries": buildings.get("矿场", 0),
            "power_plants": buildings.get("电厂", 0) + buildings.get("核电", 0),
            "war_factories": buildings.get("车间", 0),
            "miners": units.get("矿车", 0),
            "income_rate_est": income_rate,
            "harvest": harvest,
            "production_queues": production_queues,
        }

    def _summarize_tech(self, my_summary: Dict[str, Any]) -> Dict[str, Any]:
        can_build = []
        can_train = []

        for name in self.TECH_PROBE_BUILDINGS:
            try:
                if self.api.can_produce(name):
                    can_build.append(name)
            except GameAPIError:
                break

        for name in self.TECH_PROBE_UNITS:
            try:
                if self.api.can_produce(name):
                    can_train.append(name)
            except GameAPIError:
                break

        buildings = my_summary.get("buildings", {})
        key_buildings = {
            "兵营": buildings.get("兵营", 0),
            "车间": buildings.get("车间", 0),
            "雷达": buildings.get("雷达", 0),
            "科技中心": buildings.get("科技中心", 0),
            "机场": buildings.get("机场", 0),
            "维修中心": buildings.get("维修中心", 0),
        }

        tech_level = 0
        if key_buildings["兵营"] > 0:
            tech_level = 1
        if key_buildings["车间"] > 0:
            tech_level = 2
        if key_buildings["雷达"] > 0:
            tech_level = 3
        if key_buildings["科技中心"] > 0:
            tech_level = 4
        if key_buildings["机场"] > 0:
            tech_level = max(tech_level, 4)

        return {
            "can_build": can_build,
            "can_train": can_train,
            "owned_key_buildings": key_buildings,
            "tech_level_est": tech_level,
        }

    def _compute_threats(self, enemy_views: List[ActorView], base_center: Location) -> List[Dict[str, Any]]:
        threats = []
        for view in enemy_views:
            if not isinstance(view.pos, Location):
                continue
            dist = view.pos.manhattan_distance(base_center)
            value = self._estimate_unit_value(view.type)
            score = value * max(view.hp_percent, 1) / 100
            threats.append(
                {
                    "id": view.id,
                    "type": view.type,
                    "distance": dist,
                    "pos": view.pos.to_dict(),
                    "hp": view.hp_percent,
                    "value_est": value,
                    "threat_score": score,
                }
            )

        threats.sort(key=lambda item: item["distance"])
        # 简单聚类：距离阈值合并
        cluster_id = 0
        clustered: List[Dict[str, Any]] = []
        last_pos: Optional[Dict[str, int]] = None
        for t in threats:
            if last_pos:
                last_location = Location(last_pos["x"], last_pos["y"])
                current_location = Location(t["pos"]["x"], t["pos"]["y"])
                if current_location.manhattan_distance(last_location) > 8:
                    cluster_id += 1
            t["cluster_id"] = cluster_id
            clustered.append(t)
            last_pos = t["pos"]
        return clustered[:8]

    def _build_alerts(
        self,
        economy: Dict[str, Any],
        my_summary: Dict[str, Any],
        forces: Dict[str, Any],
        queues: Dict[str, Any],
        explored_ratio: Optional[float],
    ) -> Tuple[List[str], bool]:
        alerts: List[str] = []
        scout_stalled = False

        power = economy.get("power")
        if power and isinstance(power.get("surplus"), (int, float)) and power["surplus"] < 0:
            alerts.append("电力不足")

        if economy.get("refineries", 0) == 0:
            alerts.append("尚未建造矿场")

        if economy.get("miners", 0) == 0:
            alerts.append("缺少矿车")

        if my_summary.get("buildings", {}).get("兵营", 0) == 0:
            alerts.append("没有兵营无法训练步兵")

        # 队列阻塞
        for q in queues.values():
            if q.get("queue_blocked_reason"):
                alerts.append(f"生产队列阻塞:{q['queue_blocked_reason']}")
                break

        # 防空不足
        enemy_air = forces.get("enemy", {}).get("counts_by_category", {}).get("air", 0)
        my_aa = forces.get("my", {}).get("anti_air_est", 0)
        if enemy_air > 0 and my_aa < enemy_air:
            alerts.append("防空不足")

        # 军力落后
        my_value = forces.get("my", {}).get("army_value_est", 0)
        enemy_value = forces.get("enemy", {}).get("army_value_est", 0)
        if my_value and enemy_value and enemy_value > my_value * 1.4:
            alerts.append("军力落后")

        # 侦察停滞
        if self.memory.last_explored_ratio is not None and explored_ratio is not None:
            if explored_ratio - self.memory.last_explored_ratio < 0.001:
                alerts.append("侦察停滞")
                scout_stalled = True
        self.memory.last_explored_ratio = explored_ratio

        return alerts, scout_stalled

    def _build_force_summary(self, views: List[ActorView], summary: Dict[str, Any]) -> Dict[str, Any]:
        counts_by_type = summary.get("buildings", {}).copy()
        counts_by_type.update(summary.get("units", {}))

        category_counts: Dict[str, int] = {}
        value_total = 0
        anti_air = 0
        anti_armor = 0
        anti_inf = 0
        positions: List[Location] = []
        hp_sum = 0
        low_hp = 0

        for view in views:
            category = self._categorize_unit(view.type)
            category_counts[category] = category_counts.get(category, 0) + 1

            value = self._estimate_unit_value(view.type)
            value_total += value

            # 简单能力估算
            if category in ("vehicle", "air", "defense"):
                anti_armor += value * 0.6
            if "防空" in view.type or category == "defense":
                anti_air += value * 0.8
            if category == "infantry":
                anti_inf += value * 0.5

            if category not in ("harvester", "mcv", "building", "support"):
                if isinstance(view.pos, Location):
                    positions.append(view.pos)

            if isinstance(view.hp_percent, int):
                hp_sum += max(view.hp_percent, 0)
                if view.hp_percent < 30:
                    low_hp += 1

        centroid = self._compute_centroid(positions)
        hp_avg = (hp_sum / len(views)) if views else None

        return {
            "counts_by_type": counts_by_type,
            "counts_by_category": category_counts,
            "army_value_est": value_total,
            "anti_air_est": anti_air,
            "anti_armor_est": anti_armor,
            "anti_inf_est": anti_inf,
            "centroid": centroid.to_dict() if centroid else None,
            "hp_distribution": {"avg_hp_percent": hp_avg, "low_hp_units": low_hp},
            "visible_units": len(views),
        }

    def _update_enemy_memory(self, enemy_views: List[ActorView]) -> Dict[str, Any]:
        now = time.time()
        for view in enemy_views:
            if not isinstance(view.pos, Location):
                continue
            self.memory.enemy_last_seen[view.id] = {
                "type": view.type,
                "pos": view.pos.to_dict(),
                "time": now,
                "hp": view.hp_percent,
            }
        return self.memory.enemy_last_seen

    def _build_battle_section(
        self,
        enemy_views: List[ActorView],
        base_center: Location,
        unit_attrs: Dict[str, Any],
    ) -> Dict[str, Any]:
        threats = self._compute_threats(enemy_views, base_center)
        engagements = {"engaged_units": 0, "target_types": {}, "reachable_enemies": []}

        attrs = unit_attrs.get("attributes") or []
        reachable_ids: set = set()
        for attr in attrs:
            targets = attr.get("targets", [])
            if targets:
                engagements["engaged_units"] += 1
                for t in targets:
                    reachable_ids.add(str(t))
        engagements["reachable_enemies"] = list(reachable_ids)[:10]

        for view in enemy_views:
            if view.id in reachable_ids:
                engagements["target_types"][view.type] = engagements["target_types"].get(view.type, 0) + 1

        return {
            "threats_to_base": threats,
            "engagements": engagements,
        }

    def _build_opportunities(
        self,
        enemy_views: List[ActorView],
        base_center: Location,
        my_centroid: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if my_centroid:
            my_point = Location(my_centroid["x"], my_centroid["y"])
        else:
            my_point = base_center

        opportunities: List[Dict[str, Any]] = []
        for view in enemy_views:
            if view.type not in HIGH_VALUE_TARGETS:
                continue
            if not isinstance(view.pos, Location):
                continue
            distance = view.pos.manhattan_distance(my_point)
            value_score = self._estimate_unit_value(view.type)
            risk_score = 0
            # 粗略风险：距离越远风险越高
            risk_score += distance * 0.5

            opportunity_score = max(value_score - risk_score, 0)
            opportunities.append(
                {
                    "id": view.id,
                    "type": view.type,
                    "pos": view.pos.to_dict(),
                    "distance": distance,
                    "value_score": value_score,
                    "risk_score": risk_score,
                    "opportunity_score": opportunity_score,
                }
            )

        opportunities.sort(key=lambda o: o["opportunity_score"], reverse=True)
        return opportunities[:10]

    def _build_map_control(self, map_summary: Dict[str, Any], map_info: Optional[MapQueryResult], base_center: Location) -> Dict[str, Any]:
        return map_summary

    def _build_meta(self, snapshot: Dict[str, Any], explored_ratio: Optional[float], scout_stalled: bool) -> Dict[str, Any]:
        now = time.time()
        sample_interval = None
        if self.memory.prev_snapshot_time and self.memory.last_snapshot_time:
            sample_interval = self.memory.last_snapshot_time - self.memory.prev_snapshot_time
        cache_age = None
        if self._snapshot_cache:
            cache_age = now - self._snapshot_cache[0]
        return {
            "game_time": snapshot.get("t", now),
            "sample_interval": sample_interval,
            "cache_age": cache_age,
            "explored_ratio": explored_ratio,
            "scout_stalled": scout_stalled,
            "version": "v2",
        }

    def _categorize_unit(self, unit_type: Optional[str]) -> str:
        unit_type = normalize_unit_name(unit_type)
        if not unit_type:
            return "unknown"
        return UNIT_CATEGORY_RULES.get(unit_type, "unknown")

    def _estimate_unit_value(self, unit_type: Optional[str]) -> float:
        unit_type = normalize_unit_name(unit_type)
        if not unit_type:
            return 10.0
        return float(UNIT_VALUE_WEIGHTS.get(unit_type, 10.0))

    def _compute_centroid(self, positions: List[Location]) -> Optional[Location]:
        if not positions:
            return None
        avg_x = sum(p.x for p in positions) / len(positions)
        avg_y = sum(p.y for p in positions) / len(positions)
        return Location(int(avg_x), int(avg_y))


class MacroActions:
    """宏观技能封装"""

    RALLY_BUILDINGS = ("兵营", "车间", "机场")
    SUPPORT_UNITS = {"矿车", "工程师", "mcv", "基地车"}
    SCOUT_PRIORITY = ("步兵", "狗", "工程师", "火箭兵")

    def __init__(self, api: GameAPI, intel: IntelService) -> None:
        self.api = api
        self.intel_service = intel

    def opening_economy(self) -> SkillResult:
        actions: List[Dict[str, Any]] = []
        try:
            self.api.deploy_mcv_and_wait()
            actions.append({"step": "deploy_mcv"})

            for building in ("电厂", "矿场", "车间"):
                ok = self.api.ensure_can_build_wait(building)
                actions.append({"step": "ensure_building", "name": building, "ok": ok})
                if not ok:
                    return SkillResult.fail(
                        reason=f"无法建造{building}",
                        actions=actions,
                        observations={"missing": building},
                    )
            return SkillResult.success(reason="经济开局就绪", actions=actions)
        except GameAPIError as exc:
            return SkillResult.fail(
                reason=f"经济开局失败: {exc}",
                actions=actions,
                observations={"error": str(exc)},
            )

    def ensure_buildings(self, buildings: List[str]) -> SkillResult:
        actions: List[Dict[str, Any]] = []
        try:
            for name in buildings:
                ok = self.api.ensure_can_build_wait(name)
                actions.append({"building": name, "ok": ok})
                if not ok:
                    return SkillResult.fail(
                        reason=f"无法确保建筑 {name}",
                        actions=actions,
                        observations={"missing": name},
                    )
            return SkillResult.success(reason="所需建筑已准备", actions=actions)
        except GameAPIError as exc:
            return SkillResult.fail(
                reason=f"建造链失败: {exc}",
                actions=actions,
                observations={"error": str(exc)},
            )

    def ensure_units(self, units: Dict[str, int]) -> SkillResult:
        actions: List[Dict[str, Any]] = []
        try:
            for name, count in units.items():
                if count <= 0:
                    continue
                if not self.api.ensure_can_produce_unit(name):
                    return SkillResult.fail(
                        reason=f"无法生产单位 {name}",
                        actions=actions,
                        observations={"missing_prereq": name},
                    )
                self.api.produce_wait(name, count, auto_place_building=True)
                actions.append({"unit": name, "count": count})
            return SkillResult.success(reason="单位生产完成", actions=actions)
        except GameAPIError as exc:
            return SkillResult.fail(
                reason=f"生产单位失败: {exc}",
                actions=actions,
                observations={"error": str(exc)},
            )

    def scout_unexplored(self, max_scouts: int = 1, radius: int = 30) -> SkillResult:
        snapshot = self.intel_service.get_snapshot()
        map_info: Optional[MapQueryResult] = self.intel_service.get_map_info()

        if not map_info:
            return SkillResult.fail(reason="无法获取地图信息", need_replan=False)

        scouts = self._select_scouts(snapshot.get("my_actors", []), max_scouts)
        if not scouts:
            return SkillResult.fail(reason="没有可用侦察单位", need_replan=True)

        base_center = self.intel_service.get_base_center(snapshot)
        try:
            targets = self.api.get_unexplored_nearby_positions(map_info, base_center, radius)
        except GameAPIError as exc:
            return SkillResult.fail(reason=f"侦察路径失败: {exc}", need_replan=False)

        if not targets:
            return SkillResult.success(reason="附近已探索完毕", actions=[], need_replan=False)

        actions: List[Dict[str, Any]] = []
        for actor, target in zip(scouts, targets):
            ok = self.api.move_units_by_location_and_wait([actor], target, max_wait_time=radius / 5)
            actions.append(
                {
                    "unit": getattr(actor, "actor_id", None),
                    "type": getattr(actor, "type", None),
                    "target": target.to_dict(),
                    "ok": ok,
                }
            )

        return SkillResult.success(reason="侦察任务执行完毕", actions=actions)

    def defend_base(self, radius: int = 25) -> SkillResult:
        intel = self.intel_service.get_intel()
        threats = intel.enemy.get("threats", [])
        if not threats:
            return SkillResult.success(reason="暂无威胁", player_message="暂无威胁", need_replan=False)

        target_info = threats[0]
        target_pos = target_info.get("pos")
        if not target_pos:
            return SkillResult.fail(reason="威胁数据无效", need_replan=False)

        target_location = Location(target_pos["x"], target_pos["y"])
        snapshot = self.intel_service.get_snapshot()
        defenders = self._select_combat_units(snapshot.get("my_actors", []))

        if not defenders:
            return SkillResult.fail(reason="缺少可用防守单位", need_replan=True)

        try:
            self.api.move_units_by_location(defenders, target_location, attack_move=True)
        except GameAPIError as exc:
            return SkillResult.fail(reason=f"调动防守单位失败: {exc}")

        player_message = f"已派出 {len(defenders)} 个单位防守，目标距基地 {target_info.get('distance')} 格"
        actions = [{"target": target_pos, "defenders": len(defenders)}]
        return SkillResult.success(reason="已执行基地防守", actions=actions, player_message=player_message)

    def rally_production_to(self, pos: Location) -> SkillResult:
        target = pos if isinstance(pos, Location) else Location(pos["x"], pos["y"])
        snapshot = self.intel_service.get_snapshot()
        buildings = [
            actor
            for actor in snapshot.get("my_actors", [])
            if getattr(actor, "type", None) in self.RALLY_BUILDINGS
        ]

        if not buildings:
            return SkillResult.fail(reason="没有可设置集结点的建筑", need_replan=True)

        try:
            self.api.set_rally_point(buildings, target)
        except GameAPIError as exc:
            return SkillResult.fail(reason=f"设置集结点失败: {exc}")

        return SkillResult.success(
            reason="集结点已更新",
            actions=[{"buildings": len(buildings), "pos": target.to_dict()}],
        )

    def _select_scouts(self, actors: List[Actor], max_scouts: int) -> List[Actor]:
        sorted_units = sorted(
            actors,
            key=lambda act: self._scout_priority_index(getattr(act, "type", "")),
        )
        selected = []
        for actor in sorted_units:
            if len(selected) >= max_scouts:
                break
            if getattr(actor, "type", None) is None:
                continue
            selected.append(actor)
        return selected

    def _scout_priority_index(self, unit_type: Optional[str]) -> int:
        if not unit_type:
            return len(self.SCOUT_PRIORITY) + 1
        try:
            return self.SCOUT_PRIORITY.index(unit_type)
        except ValueError:
            return len(self.SCOUT_PRIORITY)

    def _select_combat_units(self, actors: List[Actor]) -> List[Actor]:
        combatants = []
        for actor in actors:
            unit_type = getattr(actor, "type", None)
            if not unit_type:
                continue
            if unit_type.lower() in self.SUPPORT_UNITS or unit_type in self.SUPPORT_UNITS:
                continue
            combatants.append(actor)
        return combatants


class RTSMiddleLayer:
    """RTS 中间层门面"""

    def __init__(self, api: GameAPI, cache_ttl: float = 0.25) -> None:
        self.api = api
        self.intel_service = IntelService(api, cache_ttl=cache_ttl)
        self.skills = MacroActions(api, self.intel_service)

    def intel(self, force: bool = False, mode: str = "brief") -> Dict[str, Any]:
        """默认 brief（LLM 决策摘要），debug 输出完整结构"""
        model = self.intel_service.get_intel(force=force)
        if mode == "debug":
            return IntelSerializer.to_debug(model)
        return IntelSerializer.to_brief(model)

    def intel_debug(self, force: bool = False) -> Dict[str, Any]:
        """显式拉取 debug 版结构化情报"""
        return self.intel(force=force, mode="debug")

    def battle_details(self, force: bool = False) -> Dict[str, Any]:
        """包含完整威胁与交战细节（保留坐标），供需要坐标的上层按需拉取"""
        intel = self.intel_service.get_intel(force=force)
        return intel.battle

    def map_control_details(self, force: bool = False) -> Dict[str, Any]:
        """包含完整地图控制与资源细节（含前沿点/未探索点坐标），按需拉取"""
        intel = self.intel_service.get_intel(force=force)
        return intel.map_control


if __name__ == "__main__":
    # 示例：实际运行需有可用的游戏服务器
    api = GameAPI("localhost")
    mid = RTSMiddleLayer(api)
    print(mid.intel())

