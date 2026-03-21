from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from ..actor_view import ActorView
from ..game_api import GameAPI, GameAPIError
from ..models import Actor, Location, MapQueryResult, TargetsQueryParam
from .memory import IntelMemory
from .model import IntelModel
from .names import normalize_unit_name
from .rules import (
    DEFAULT_HIGH_VALUE_TARGETS,
    DEFAULT_UNIT_CATEGORY_RULES,
    DEFAULT_UNIT_VALUE_WEIGHTS,
)

logger = logging.getLogger(__name__)

HIGH_VALUE_TARGETS = DEFAULT_HIGH_VALUE_TARGETS
UNIT_CATEGORY_RULES = DEFAULT_UNIT_CATEGORY_RULES
UNIT_VALUE_WEIGHTS = DEFAULT_UNIT_VALUE_WEIGHTS


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
        self._building_names_norm = {normalize_unit_name(n) for n in self._building_names}
        self._unit_names_norm = {normalize_unit_name(n) for n in self._unit_names}
        self.memory = IntelMemory()

    def get_snapshot(self, force: bool = False) -> Dict[str, Any]:
        cached = self._get_cached(self._snapshot_cache, self.cache_ttl, force)
        if cached is not None:
            return cached

        snapshot = self._fetch_snapshot()
        self._snapshot_cache = (time.time(), snapshot)
        self.memory.prev_snapshot_time = self.memory.last_snapshot_time
        self.memory.last_snapshot_time = snapshot.get("t")
        return snapshot

    def get_map_info(self, force: bool = False) -> Optional[MapQueryResult]:
        cached = self._get_cached(self.memory.map_cache, self.map_ttl, force)
        if cached is not None:
            return cached
        try:
            info = self._fetch_map_info()
            self.memory.map_cache = (time.time(), info)
            return info
        except GameAPIError as exc:
            logger.info("获取地图信息失败: %s", exc)
            return None

    def get_intel(self, force: bool = False) -> IntelModel:
        cached = self._get_cached(self._intel_cache, self.cache_ttl, force)
        if cached is not None:
            return cached

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

    def _get_cached(self, cache: Optional[Tuple[float, Any]], ttl: float, force: bool) -> Optional[Any]:
        if force or not cache:
            return None
        ts, value = cache
        if self._is_cache_valid(ts, ttl):
            return value
        return None

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
        if raw_queue.get("has_ready_item") and raw_queue.get("queue_type") in ("Building", "Defense"):
            return "ready_not_placed"
        items = raw_queue.get("queue_items") or []
        if not items:
            return None
        head = items[0]
        if head.get("done") and raw_queue.get("queue_type") in ("Building", "Defense"):
            return "ready_not_placed"
        if all(item.get("paused") for item in items):
            return "paused"
        return None

    def _get_unit_attributes(self, actors: List[Actor]) -> Dict[str, Any]:
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

    # ---- analyzer（保留原逻辑，后续可继续再拆） ----
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
        legacy = {"match": {}}
        actors_actions = self._build_actors_actions_intel(my_views, enemy_views)

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
            actors_actions=actors_actions,
        )

    def _build_actors_actions_intel(
        self, my_views: List[ActorView], enemy_views: List[ActorView]
    ) -> Dict[str, Any]:
        def summarize(views: List[ActorView]) -> Dict[str, Any]:
            actors: List[Dict[str, Any]] = []
            by_activity: Dict[str, int] = {}
            by_order: Dict[str, int] = {}

            for v in views:
                act = (getattr(v, "activity", "") or "").strip()
                ords = (getattr(v, "order", "") or "").strip()

                actors.append(
                    {
                        "id": v.id,
                        "type": v.type,
                        "faction": v.faction,
                        "pos": v.pos.to_dict() if hasattr(v.pos, "to_dict") else {"x": v.pos.x, "y": v.pos.y},
                        "hp_percent": v.hp_percent,
                        "activity": act,
                        "order": ords,
                    }
                )

                # 统计时做一点归一：空 activity 视为 Idle；order 取 "->" 前的部分
                act_key = "Idle" if not act else act
                order_key = ""
                if ords:
                    order_key = ords.split("->", 1)[0].strip()
                    # 去掉 "(Queued)" 标签，方便聚合
                    if order_key.endswith("(Queued)"):
                        order_key = order_key.replace("(Queued)", "").strip()
                order_key = order_key or "None"

                by_activity[act_key] = by_activity.get(act_key, 0) + 1
                by_order[order_key] = by_order.get(order_key, 0) + 1

            # 输出时把统计按数量降序排列，便于阅读
            top_activities = sorted(by_activity.items(), key=lambda x: x[1], reverse=True)[:12]
            top_orders = sorted(by_order.items(), key=lambda x: x[1], reverse=True)[:12]

            return {
                "actors": actors,
                "counts": {
                    "total": len(actors),
                    "by_activity": dict(top_activities),
                    "by_order": dict(top_orders),
                },
            }

        return {
            "my": summarize(my_views),
            "enemy": summarize(enemy_views),
            "note": "activity 来自 CurrentActivity.DebugLabelComponents().JoinWith('.')；order 为最近一次 Resolve 的 Order（不是严格意义的“当前执行中 order”）",
        }

    def _summarize_actors(self, views: List[ActorView]) -> Dict[str, Any]:
        building_counts: Dict[str, int] = {}
        unit_counts: Dict[str, int] = {}
        unknown = 0

        for view in views:
            category = UNIT_CATEGORY_RULES.get(view.type)
            is_building = (
                view.type in self._building_names_norm
                or category in ("building", "defense")
                or view.type.endswith(("厂", "站", "中心"))
            )
            is_unit = view.type in self._unit_names_norm or category in (
                "infantry",
                "vehicle",
                "air",
                "harvester",
                "support",
                "mcv",
            )
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

    def _summarize_map(
        self, map_info: Optional[MapQueryResult], base_center: Location
    ) -> Tuple[Dict[str, Any], Optional[float]]:
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
                neighbors = [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]
                for nx, ny in neighbors:
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    if ny < len(explored) and nx < len(explored[ny]) and not explored[ny][nx]:
                        frontier.append(Location(x, y))
                        break
        return [pos.to_dict() for pos in frontier[:limit]]

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
            "production_queues": queues,
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

        for q in queues.values():
            if q.get("queue_blocked_reason"):
                alerts.append(f"生产队列阻塞:{q['queue_blocked_reason']}")
                break

        enemy_air = forces.get("enemy", {}).get("counts_by_category", {}).get("air", 0)
        my_aa = forces.get("my", {}).get("anti_air_est", 0)
        if enemy_air > 0 and my_aa < enemy_air:
            alerts.append("防空不足")

        my_value = forces.get("my", {}).get("army_value_est", 0)
        enemy_value = forces.get("enemy", {}).get("army_value_est", 0)
        if my_value and enemy_value and enemy_value > my_value * 1.4:
            alerts.append("军力落后")

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
        value_total = 0.0
        anti_air = 0.0
        anti_armor = 0.0
        anti_inf = 0.0
        positions: List[Location] = []
        hp_sum = 0
        low_hp = 0

        for view in views:
            category = self._categorize_unit(view.type)
            category_counts[category] = category_counts.get(category, 0) + 1

            value = self._estimate_unit_value(view.type)
            value_total += value

            if category in ("vehicle", "air", "defense"):
                anti_armor += value * 0.6
            if "防空" in view.type or category == "defense":
                anti_air += value * 0.8
            if category == "infantry":
                anti_inf += value * 0.5

            if category not in ("harvester", "mcv", "building", "support"):
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

        return {"threats_to_base": threats, "engagements": engagements}

    def _build_opportunities(
        self,
        enemy_views: List[ActorView],
        base_center: Location,
        my_centroid: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        my_point = Location(my_centroid["x"], my_centroid["y"]) if my_centroid else base_center
        opportunities: List[Dict[str, Any]] = []
        for view in enemy_views:
            if view.type not in HIGH_VALUE_TARGETS:
                continue
            distance = view.pos.manhattan_distance(my_point)
            value_score = self._estimate_unit_value(view.type)
            risk_score = distance * 0.5
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


