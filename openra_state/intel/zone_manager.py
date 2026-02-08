from __future__ import annotations
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
import logging
import math

from openra_api.models import Location, MapQueryResult, Actor
from ..data.structure_data import StructureData
from ..data.combat_data import CombatData
from .clustering import SpatialClustering
logger = logging.getLogger(__name__)


@dataclass
class ZoneInfo:
    id: int
    center: Location
    type: str
    subtype: str = "ORE"
    radius: int = 10
    resource_value: float = 0.0
    owner_faction: Optional[str] = None
    neighbors: List[int] = field(default_factory=list)
    bounding_box: Tuple[int, int, int, int] = (0, 0, 0, 0)
    my_strength: float = 0.0
    enemy_strength: float = 0.0
    ally_strength: float = 0.0
    my_units: Dict[str, int] = field(default_factory=dict)
    enemy_units: Dict[str, int] = field(default_factory=dict)
    ally_units: Dict[str, int] = field(default_factory=dict)
    my_structures: Dict[str, int] = field(default_factory=dict)
    enemy_structures: Dict[str, int] = field(default_factory=dict)
    ally_structures: Dict[str, int] = field(default_factory=dict)
    is_visible: Optional[bool] = None
    is_explored: Optional[bool] = None


class ZoneManager:
    def __init__(self):
        self.zones: Dict[int, ZoneInfo] = {}
        self.map_width = 0
        self.map_height = 0
        self.screen_width = 24
        self._zone_map: Dict[Tuple[int, int], int] = {}
        self._next_zone_id = 1

    def update_from_map_query(self, map_data: MapQueryResult, mine_actors: List[Actor] = None) -> None:
        self.map_width = map_data.MapWidth
        self.map_height = map_data.MapHeight
        self.zones.clear()
        self._zone_map.clear()
        self._next_zone_id = 1
        patches = self._find_resource_clusters(map_data)
        logger.info(f"Identified {len(patches)} resource clusters via DBSCAN.")
        for center, total_value, bbox in patches:
            zone_id = self._next_zone_id
            self._next_zone_id += 1
            final_center = center
            resource_subtype = "ORE"
            if mine_actors:
                best_mine = None
                min_dist = float("inf")
                for mine in mine_actors:
                    if not mine.position:
                        continue
                    in_box = bbox[0] - 5 <= mine.position.x <= bbox[2] + 5 and bbox[1] - 5 <= mine.position.y <= bbox[3] + 5
                    if in_box:
                        dist = mine.position.euclidean_distance(center)
                        is_gem = "gmine" in str(mine.type).lower()
                        best_is_gem = best_mine is not None and "gmine" in str(best_mine.type).lower()
                        if is_gem and not best_is_gem:
                            best_mine = mine
                            min_dist = dist
                        elif is_gem == best_is_gem:
                            if dist < min_dist:
                                min_dist = dist
                                best_mine = mine
                if best_mine:
                    final_center = best_mine.position
                    if "gmine" in str(best_mine.type).lower():
                        resource_subtype = "GEM"
                    else:
                        resource_subtype = "ORE"
                    logger.debug(f"Zone {zone_id} snapped to {resource_subtype} Mine {best_mine.id} at {final_center}")
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            radius = int(math.sqrt(width * width + height * height) / 2)
            new_zone = ZoneInfo(
                id=zone_id,
                center=final_center,
                type="RESOURCE",
                subtype=resource_subtype,
                radius=max(radius, 5),
                bounding_box=bbox,
            )
            self.zones[zone_id] = new_zone
        self.update_resource_values(map_data, mine_actors=mine_actors)
        self._build_topology()

    def _create_zones_from_mines(self, map_data: MapQueryResult, mine_actors: List[Actor]):
        pass

    def update_resource_values(self, map_data: MapQueryResult, mine_actors: List[Actor] = None) -> None:
        width = map_data.MapWidth
        height = map_data.MapHeight
        resources = map_data.Resources
        resource_types = map_data.ResourcesType
        zone_mines: Dict[int, List[Actor]] = {}
        if mine_actors:
            for mine in mine_actors:
                if not mine.position:
                    continue
                z_id = self.get_zone_id(mine.position)
                if z_id != 0:
                    if z_id not in zone_mines:
                        zone_mines[z_id] = []
                    zone_mines[z_id].append(mine)
        for zone in self.zones.values():
            ore_count = 0
            gem_count = 0
            bbox = zone.bounding_box
            min_x = max(0, bbox[0])
            min_y = max(0, bbox[1])
            max_x = min(width - 1, bbox[2])
            max_y = min(height - 1, bbox[3])
            for y in range(min_y, max_y + 1):
                for x in range(min_x, max_x + 1):
                    if x >= len(resources) or y >= len(resources[0]):
                        continue
                    val = resources[x][y]
                    if val > 0:
                        if self.get_zone_id(Location(x, y)) == zone.id:
                            r_type = resource_types[x][y]
                            r_type_str = str(r_type).lower()
                            if r_type_str == "2" or "gem" in r_type_str:
                                gem_count += 1
                            else:
                                ore_count += 1
            ore_mines = 0
            gem_mines = 0
            if zone.id in zone_mines:
                for m in zone_mines[zone.id]:
                    if "gmine" in str(m.type).lower():
                        gem_mines += 1
                    else:
                        ore_mines += 1
            tile_score = ore_count * 1.0 + gem_count * 2.5
            mine_score = ore_mines * 50.0 + gem_mines * 150.0
            zone.resource_value = tile_score + mine_score
            if zone.subtype == "ORE":
                if gem_count > ore_count * 0.5 and gem_count > 5:
                    zone.subtype = "GEM"
                elif gem_count > 0:
                    zone.subtype = "MIXED"

    def update_bases(self, all_units: List[Actor], my_faction: str = None, ally_factions: List[str] = None) -> None:
        friendly_set = set()
        if my_faction:
            friendly_set.add(my_faction)
        if ally_factions:
            friendly_set.update(ally_factions)
        for zone in self.zones.values():
            zone.my_structures.clear()
            zone.enemy_structures.clear()
            zone.ally_structures.clear()
        buildings = []
        for u in all_units:
            if not u.type:
                continue
            if StructureData.is_valid_structure(u.type):
                buildings.append(u)
        zone_buildings: Dict[int, List[Actor]] = {}

        def get_side(u_faction: str) -> str:
            if not u_faction:
                return "ENEMY"
            if my_faction and u_faction == my_faction:
                return "MY"
            if ally_factions and u_faction in ally_factions:
                return "ALLY"
            f_lower = u_faction.lower()
            if f_lower in ["player", "self", "己方", "my"]:
                return "MY"
            if f_lower in ["ally", "friendly", "友方", "friend"]:
                return "ALLY"
            if f_lower in ["enemy", "hostile", "敌方"]:
                return "ENEMY"
            if f_lower in ["neutral", "中立"]:
                return "NEUTRAL"
            return "ENEMY"

        for b in buildings:
            if not b.position:
                continue
            z_id = self.get_zone_id(b.position)
            if z_id == 0:
                continue
            if z_id not in zone_buildings:
                zone_buildings[z_id] = []
            zone_buildings[z_id].append(b)
            zone = self.zones.get(z_id)
            if zone:
                side = get_side(b.faction)
                info = StructureData.get_info(b.type)
                s_id = info.get("type", b.type.lower())
                if side == "MY":
                    zone.my_structures[s_id] = zone.my_structures.get(s_id, 0) + 1
                elif side == "ENEMY":
                    zone.enemy_structures[s_id] = zone.enemy_structures.get(s_id, 0) + 1
                elif side == "ALLY":
                    zone.ally_structures[s_id] = zone.ally_structures.get(s_id, 0) + 1
        for z_id, actors in zone_buildings.items():
            zone = self.zones.get(z_id)
            if not zone:
                continue
            has_fact = False
            for u in actors:
                info = StructureData.get_info(u.type)
                if info.get("is_base_provider"):
                    has_fact = True
                    break
            factions = [u.faction for u in actors if u.faction]
            if not factions:
                continue
            dominant_faction = max(set(factions), key=factions.count)
            if has_fact:
                if zone.type != "MAIN_BASE":
                    logger.info(f"Zone {z_id} upgraded to MAIN_BASE (Fact detected). Owner: {dominant_faction}")
                zone.type = "MAIN_BASE"
            else:
                if zone.type == "RESOURCE":
                    logger.info(f"Zone {z_id} upgraded to SUB_BASE (Buildings detected). Owner: {dominant_faction}")
                    zone.type = "SUB_BASE"
            zone.owner_faction = dominant_faction

    def update_combat_strength(self, all_units: List[Actor], my_faction: str = None, ally_factions: List[str] = None) -> None:
        for zone in self.zones.values():
            zone.my_strength = 0.0
            zone.enemy_strength = 0.0
            zone.ally_strength = 0.0
            zone.my_units.clear()
            zone.enemy_units.clear()
            zone.ally_units.clear()
        if not all_units:
            return
        if ally_factions is None:
            ally_factions = []

        def get_side(u_faction: str) -> str:
            if not u_faction:
                return "ENEMY"
            if my_faction and u_faction == my_faction:
                return "MY"
            if ally_factions and u_faction in ally_factions:
                return "ALLY"
            f_lower = u_faction.lower()
            if f_lower in ["player", "self", "己方", "my"]:
                return "MY"
            if f_lower in ["ally", "friendly", "友方", "friend"]:
                return "ALLY"
            if f_lower in ["enemy", "hostile", "敌方"]:
                return "ENEMY"
            if f_lower in ["neutral", "中立"]:
                return "NEUTRAL"
            return "ENEMY"

        for unit in all_units:
            if not unit.position:
                continue
            if unit.is_dead:
                continue
            category, score = CombatData.get_combat_info(unit.type)
            if score <= 0:
                continue
            z_id = self.get_zone_id(unit.position)
            if z_id == 0:
                continue
            zone = self.zones.get(z_id)
            if not zone:
                continue
            u_id = CombatData.resolve_id(unit.type) or unit.type.lower()
            side = get_side(unit.faction)
            if side == "MY":
                zone.my_strength += score
                zone.my_units[u_id] = zone.my_units.get(u_id, 0) + 1
            elif side == "ENEMY":
                zone.enemy_strength += score
                zone.enemy_units[u_id] = zone.enemy_units.get(u_id, 0) + 1
            elif side == "ALLY":
                zone.ally_strength += score
                zone.ally_units[u_id] = zone.ally_units.get(u_id, 0) + 1

    def get_zone_id(self, location: Location) -> int:
        if not self.zones:
            return 0
        pos_tuple = (location.x, location.y)
        if pos_tuple in self._zone_map:
            return self._zone_map[pos_tuple]
        best_zone = 0
        min_dist = float("inf")
        for zone in self.zones.values():
            dist = location.euclidean_distance(zone.center)
            if dist < min_dist:
                min_dist = dist
                best_zone = zone.id
        self._zone_map[pos_tuple] = best_zone
        return best_zone

    def get_zone(self, zone_id: int) -> Optional[ZoneInfo]:
        return self.zones.get(zone_id)

    def _find_resource_clusters(self, map_data: MapQueryResult) -> List[Tuple[Location, int, Tuple[int, int, int, int]]]:
        width = map_data.MapWidth
        height = map_data.MapHeight
        resources = map_data.Resources
        points = []
        point_values = {}
        array_width = len(resources)
        array_height = len(resources[0]) if array_width > 0 else 0
        scan_width = min(width, array_width)
        scan_height = min(height, array_height)
        for x in range(scan_width):
            for y in range(scan_height):
                val = resources[x][y]
                if val > 0:
                    loc = Location(x, y)
                    points.append(loc)
                    point_values[(x, y)] = val
        if not points:
            return []
        initial_clusters = SpatialClustering.dbscan_grid(points, eps=4.0, min_samples=5)
        final_clusters = []
        split_threshold = self.screen_width * 0.8
        for cluster in initial_clusters:
            if not cluster:
                continue
            bbox = SpatialClustering.calculate_bounding_box(cluster)
            c_width = bbox[2] - bbox[0]
            c_height = bbox[3] - bbox[1]
            if c_width > split_threshold:
                k = math.ceil(c_width / split_threshold)
                k = min(k, 4)
                if k > 1:
                    sub_clusters = SpatialClustering.kmeans_split(cluster, k=k)
                    final_clusters.extend(sub_clusters)
                else:
                    final_clusters.append(cluster)
            else:
                final_clusters.append(cluster)
        result = []
        for cluster in final_clusters:
            if not cluster:
                continue
            avg_x = sum(p.x for p in cluster) // len(cluster)
            avg_y = sum(p.y for p in cluster) // len(cluster)
            center = Location(avg_x, avg_y)
            total_value = sum(point_values.get((p.x, p.y), 0) for p in cluster)
            bbox = SpatialClustering.calculate_bounding_box(cluster)
            result.append((center, total_value, bbox))
        return result

    def _build_topology(self):
        zone_ids = list(self.zones.keys())
        count = len(zone_ids)
        for z in self.zones.values():
            z.neighbors.clear()
        if count < 2:
            return
        dists_sq = {}
        for i in range(count):
            id_a = zone_ids[i]
            pos_a = self.zones[id_a].center
            for j in range(count):
                id_b = zone_ids[j]
                pos_b = self.zones[id_b].center
                dx = pos_a.x - pos_b.x
                dy = pos_a.y - pos_b.y
                dists_sq[(id_a, id_b)] = dx * dx + dy * dy
        for i in range(count):
            id_a = zone_ids[i]
            for j in range(i + 1, count):
                id_b = zone_ids[j]
                dist_ab_sq = dists_sq[(id_a, id_b)]
                is_gabriel = True
                for k in range(count):
                    id_k = zone_ids[k]
                    if id_k == id_a or id_k == id_b:
                        continue
                    dist_ak_sq = dists_sq[(id_a, id_k)]
                    dist_bk_sq = dists_sq[(id_b, id_k)]
                    if dist_ak_sq + dist_bk_sq < dist_ab_sq:
                        is_gabriel = False
                        break
                if is_gabriel:
                    self.zones[id_a].neighbors.append(id_b)
                    self.zones[id_b].neighbors.append(id_a)
