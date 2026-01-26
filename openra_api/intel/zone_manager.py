from __future__ import annotations
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass, field
import math
import collections

from openra_api.models import Location, MapQueryResult, Actor
from openra_api.data.structure_data import StructureData
from openra_api.data.combat_data import CombatData
from .clustering import SpatialClustering
from the_seed.utils import LogManager

logger = LogManager.get_logger()

@dataclass
class ZoneInfo:
    id: int
    center: Location
    type: str  # "RESOURCE", "BASE", "CHOKEPOINT"
    subtype: str = "ORE"  # "ORE", "GEM", "MIXED", "NONE"
    radius: int = 10
    resource_value: float = 0.0  # 综合资源评分 (Weighted Score: Tiles + Mines)
    owner_faction: Optional[str] = None # 如果是 Base，归属方
    neighbors: List[int] = field(default_factory=list)
    bounding_box: Tuple[int, int, int, int] = (0, 0, 0, 0) # min_x, min_y, max_x, max_y

    # Combat Stats
    my_strength: float = 0.0
    enemy_strength: float = 0.0
    ally_strength: float = 0.0
    my_units: Dict[str, int] = field(default_factory=dict)
    enemy_units: Dict[str, int] = field(default_factory=dict)
    ally_units: Dict[str, int] = field(default_factory=dict)

    # Structure Stats
    my_structures: Dict[str, int] = field(default_factory=dict)
    enemy_structures: Dict[str, int] = field(default_factory=dict)
    ally_structures: Dict[str, int] = field(default_factory=dict)

class ZoneManager:
    """
    地图区域管理器。
    将连续的地图坐标划分为离散的战术区域 (Zones)。
    基于资源分布(Resource Patch)和基地(Base)位置进行划分。
    """
    
    def __init__(self):
        self.zones: Dict[int, ZoneInfo] = {}
        self.map_width = 0
        self.map_height = 0
        self.screen_width = 24 # Default, should be updated
        # 缓存：坐标 -> ZoneID
        self._zone_map: Dict[Tuple[int, int], int] = {} 
        self._next_zone_id = 1

    def update_from_map_query(self, map_data: MapQueryResult, mine_actors: List[Actor] = None) -> None:
        """
        根据全图数据重新划分区域 (主要识别矿区)。
        采用混合策略：
        1. 使用 DBSCAN 基于全图资源分布识别所有矿区 (Global Coverage)。
        2. 如果矿区内存在可见的矿柱 (Mine Actor)，则将 Zone 中心锚定到矿柱位置 (Local Precision)。
        3. 区分宝石矿 (GEM) 和普通矿 (ORE)，赋予不同战略价值。
        """
        # Always update map dimensions to handle dynamic map switching
        self.map_width = map_data.MapWidth
        self.map_height = map_data.MapHeight
        
        # 清除旧的 RESOURCE 区域
        self.zones.clear() 
        self._zone_map.clear()
        self._next_zone_id = 1
        
        # 1. 识别全图矿区连通域 (Base Layer)
        patches = self._find_resource_clusters(map_data)
        logger.info(f"Identified {len(patches)} resource clusters via DBSCAN.")
        
        # 2. 创建 Zones 并尝试锚定矿柱
        for center, total_value, bbox in patches:
            zone_id = self._next_zone_id
            self._next_zone_id += 1
            
            final_center = center
            snapped = False
            resource_subtype = "ORE" # Default
            
            # 分析矿区成分 (Map Tiles)
            # 统计包围盒内的 Ore(1) 和 Gem(2) 数量，以确定默认 subtype
            # 注意：total_value 已经是 sum，这里我们可能需要重新扫描一下比例，或者在 _find_resource_clusters 里返回
            # 为了简单，这里暂且默认 ORE，如果 mine_actors 存在则以 actor 为准，否则后续 update_resource_values 会修正
            
            if mine_actors:
                # 寻找该矿区内最近的矿柱
                best_mine = None
                min_dist = float('inf')
                
                # 优化：只检查包围盒内的矿柱
                for mine in mine_actors:
                    if not mine.position: continue
                    
                    # 检查是否在包围盒内 (扩大一点容差)
                    in_box = (bbox[0] - 5 <= mine.position.x <= bbox[2] + 5 and 
                              bbox[1] - 5 <= mine.position.y <= bbox[3] + 5)
                    
                    # logger.debug(f"Checking Mine {mine.id} at {mine.position} against bbox {bbox}: in_box={in_box}")
                    # logger.debug(f"Checking Mine {mine.id} at {mine.position} against bbox {bbox}: in_box={in_box}")
 
                    if in_box:
                        dist = mine.position.euclidean_distance(center)
                        
                        # 优先选择宝石矿柱 (gmine)
                        # 如果当前 best_mine 不是 gem 但这个是，或者距离更近
                        is_gem = "gmine" in str(mine.type).lower()
                        best_is_gem = (best_mine is not None) and ("gmine" in str(best_mine.type).lower())
                        
                        if is_gem and not best_is_gem:
                            # 发现宝石矿，无条件优先
                            best_mine = mine
                            min_dist = dist
                        elif is_gem == best_is_gem:
                            # 同级比较距离
                            if dist < min_dist:
                                min_dist = dist
                                best_mine = mine
                        # else: current is ore, best is gem -> ignore current
                
                if best_mine:
                    final_center = best_mine.position
                    snapped = True
                    # 确定 Subtype
                    if "gmine" in str(best_mine.type).lower():
                        resource_subtype = "GEM"
                    else:
                        resource_subtype = "ORE"
                        
                    logger.debug(f"Zone {zone_id} snapped to {resource_subtype} Mine {best_mine.id} at {final_center}")

            # 计算半径
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            radius = int(math.sqrt(width*width + height*height) / 2)
            
            # 初步创建 Zone
            new_zone = ZoneInfo(
                id=zone_id,
                center=final_center,
                type="RESOURCE",
                subtype=resource_subtype,
                # resource_value will be updated in step 3
                radius=max(radius, 5),
                bounding_box=bbox
            )
            self.zones[zone_id] = new_zone
            
        # 3. 重新计算精确资源价值和类型 (基于 Map Tiles)
        # 如果没有锚定到 Mine Actor (盲区)，我们需要根据 Tile 成分判断是 GEM 还是 ORE
        self.update_resource_values(map_data, mine_actors=mine_actors)
            
        # 4. 构建拓扑关系
        self._build_topology()

    def _create_zones_from_mines(self, map_data: MapQueryResult, mine_actors: List[Actor]):
        """Deprecated: Merged into update_from_map_query as hybrid approach"""
        pass
            
        # 3. 构建拓扑关系
        self._build_topology()

    def update_resource_values(self, map_data: MapQueryResult, mine_actors: List[Actor] = None) -> None:
        """
        更新现有 Zone 的资源储量和战略价值。
        基于 Map Tiles (Ore=1, Gem=2) 和 Mine Actors 计算。
        """
        width = map_data.MapWidth
        height = map_data.MapHeight
        resources = map_data.Resources
        # 注意：根据 Guide，ResourcesType 实测是 int[][]，但 Models 定义可能是 str[][]
        # 我们这里假设它是可以被转换为 int 的 (1=Ore, 2=Gem)
        resource_types = map_data.ResourcesType
        
        # 预处理矿柱归属
        zone_mines: Dict[int, List[Actor]] = {}
        if mine_actors:
            for mine in mine_actors:
                if not mine.position: continue
                z_id = self.get_zone_id(mine.position)
                if z_id != 0:
                    if z_id not in zone_mines:
                        zone_mines[z_id] = []
                    zone_mines[z_id].append(mine)
        
        for zone in self.zones.values():
            # Calculate resources for ALL zones, even bases
            
            # 重新扫描包围盒内的资源点
            # current_value = 0 # Raw value deprecated
            ore_count = 0
            gem_count = 0
            
            bbox = zone.bounding_box
            
            # 限制扫描范围在地图内
            min_x = max(0, bbox[0])
            min_y = max(0, bbox[1])
            max_x = min(width - 1, bbox[2])
            max_y = min(height - 1, bbox[3])
            
            for y in range(min_y, max_y + 1):
                for x in range(min_x, max_x + 1):
                    # Strict access: resources is [x][y] (Column-Major)
                    # verified by MapWidth=len(resources)
                    if x >= len(resources) or y >= len(resources[0]):
                        continue
                        
                    val = resources[x][y]
                    
                    if val > 0:
                        # 检查是否属于该 Zone
                        if self.get_zone_id(Location(x, y)) == zone.id:
                            # current_value += val # Raw value deprecated
                            
                            # 严谨解析资源类型
                            r_type = resource_types[x][y]
                            r_type_str = str(r_type).lower()
                            
                            # 仅支持明确的协议类型: 1=Ore, 2=Gem
                            # 同时兼容可能的字符串标识 (防御性)
                            if r_type_str == "2" or "gem" in r_type_str:
                                gem_count += 1
                            else:
                                # 默认为 Ore (1)
                                ore_count += 1
            
            # 统计矿柱数量
            ore_mines = 0
            gem_mines = 0
            if zone.id in zone_mines:
                for m in zone_mines[zone.id]:
                    if "gmine" in str(m.type).lower():
                        gem_mines += 1
                    else:
                        ore_mines += 1
            
            # 计算战略价值
            # 公式: (OreTiles * 1.0 + GemTiles * 2.5) + (OreMines * 50 + GemMines * 150)
            # 矿柱代表再生能力，给予高额固定加分
            tile_score = ore_count * 1.0 + gem_count * 2.5
            mine_score = ore_mines * 50.0 + gem_mines * 150.0
            
            # 直接使用加权评分作为该区域的 Resource Value，简化下游决策
            zone.resource_value = tile_score + mine_score
            
            # 如果之前没通过 Actor 确定类型 (比如在迷雾中)，或者 Actor 说是 ORE 但实际上有很多 Gem
            # 则根据 Tile 成分修正 subtype
            if zone.subtype == "ORE":
                if gem_count > ore_count * 0.5 and gem_count > 5:
                    zone.subtype = "GEM"
                elif gem_count > 0:
                    zone.subtype = "MIXED"
            elif zone.subtype == "GEM":
                pass

    def update_bases(self, all_units: List[Actor], my_faction: str = None, ally_factions: List[str] = None) -> None:
        """
        根据战场单位更新区域属性。
        :param all_units: 所有单位列表
        :param my_faction: 我方阵营名称
        :param ally_factions: 盟友阵营列表
        """
        friendly_set = set()
        if my_faction:
            friendly_set.add(my_faction)
        if ally_factions:
            friendly_set.update(ally_factions)

        # 0. Reset Structure Stats
        for zone in self.zones.values():
            zone.my_structures.clear()
            zone.enemy_structures.clear()
            zone.ally_structures.clear()

        # 1. 提取所有建筑 (使用 StructureData 过滤，排除围墙和非建筑)
        # 注意: 这里不需要过滤 is_frozen，冻结的建筑也是有效的情报，用于判定敌方基地位置
        buildings = []
        for u in all_units:
            if not u.type: continue
            # 必须是已知建筑
            if StructureData.is_valid_structure(u.type):
                buildings.append(u)
        
        # 统计每个 Zone 内的建筑情况
        zone_buildings: Dict[int, List[Actor]] = {}
        
        # Helper to determine side
        def get_side(u_faction: str) -> str:
            if not u_faction: return "ENEMY"
            # Check explicit arguments
            if my_faction and u_faction == my_faction: return "MY"
            if ally_factions and u_faction in ally_factions: return "ALLY"
            
            # Check keywords
            f_lower = u_faction.lower()
            if f_lower in ["player", "self", "己方", "my"]: return "MY"
            if f_lower in ["ally", "friendly", "友方", "friend"]: return "ALLY"
            if f_lower in ["enemy", "hostile", "敌方"]: return "ENEMY"
            if f_lower in ["neutral", "中立"]: return "NEUTRAL"
            
            return "ENEMY" # Default to enemy

        for b in buildings:
            if not b.position:
                continue
            
            # 归属到最近的 Zone
            z_id = self.get_zone_id(b.position)
            if z_id == 0: continue
            
            if z_id not in zone_buildings:
                zone_buildings[z_id] = []
            zone_buildings[z_id].append(b)

            # Update Structure Stats
            zone = self.zones.get(z_id)
            if zone:
                side = get_side(b.faction)
                # Resolve structure ID
                info = StructureData.get_info(b.type)
                s_id = info.get("type", b.type.lower())
                
                if side == "MY":
                    zone.my_structures[s_id] = zone.my_structures.get(s_id, 0) + 1
                elif side == "ENEMY":
                    zone.enemy_structures[s_id] = zone.enemy_structures.get(s_id, 0) + 1
                elif side == "ALLY":
                    zone.ally_structures[s_id] = zone.ally_structures.get(s_id, 0) + 1
            
        # 更新 Zone 属性
        for z_id, actors in zone_buildings.items():
            zone = self.zones.get(z_id)
            if not zone: continue
            
            # Check for base provider using updated StructureData which now supports Chinese names
            # StructureData.get_info(u.type).get("is_base_provider")
            has_fact = False
            for u in actors:
                info = StructureData.get_info(u.type)
                if info.get("is_base_provider"):
                    has_fact = True
                    break
            
            # 获取该区域的主导阵营
            factions = [u.faction for u in actors if u.faction]
            if not factions: continue
            
            dominant_faction = max(set(factions), key=factions.count)
            
            # 更新 Zone 类型
            if has_fact:
                if zone.type != "MAIN_BASE":
                    logger.info(f"Zone {z_id} upgraded to MAIN_BASE (Fact detected). Owner: {dominant_faction}")
                zone.type = "MAIN_BASE"
            else:
                # 只有建筑没有 Fact
                if zone.type == "RESOURCE":
                    logger.info(f"Zone {z_id} upgraded to SUB_BASE (Buildings detected). Owner: {dominant_faction}")
                    zone.type = "SUB_BASE"
            
            zone.owner_faction = dominant_faction

    def update_combat_strength(self, all_units: List[Actor], my_faction: str = None, ally_factions: List[str] = None) -> None:
        """
        根据战场单位更新区域兵力评分和单位统计。
        :param all_units: 所有单位列表
        :param my_faction: 我方阵营名称
        :param ally_factions: 盟友阵营列表
        """
        # 1. Reset Combat Stats
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
            
        # Helper to determine side
        def get_side(u_faction: str) -> str:
            if not u_faction: return "ENEMY"
            # Check explicit arguments
            if my_faction and u_faction == my_faction: return "MY"
            if ally_factions and u_faction in ally_factions: return "ALLY"
            
            # Check keywords
            f_lower = u_faction.lower()
            if f_lower in ["player", "self", "己方", "my"]: return "MY"
            if f_lower in ["ally", "friendly", "友方", "friend"]: return "ALLY"
            if f_lower in ["enemy", "hostile", "敌方"]: return "ENEMY"
            if f_lower in ["neutral", "中立"]: return "NEUTRAL"
            
            return "ENEMY" # Default to enemy
            
        for unit in all_units:
            if not unit.position: continue
            if unit.is_dead: continue
            
            # Get combat score (filters out non-combat units with score <= 0)
            category, score = CombatData.get_combat_info(unit.type)
            if score <= 0: continue
            
            # Get Zone
            z_id = self.get_zone_id(unit.position)
            if z_id == 0: continue
            
            zone = self.zones.get(z_id)
            if not zone: continue
            
            # Resolve Normalized ID
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
        """获取指定坐标所属的 Zone ID"""
        if not self.zones:
            return 0
            
        # 1. 检查缓存
        pos_tuple = (location.x, location.y)
        if pos_tuple in self._zone_map:
            return self._zone_map[pos_tuple]
            
        # 2. 寻找最近的 Zone Center
        best_zone = 0
        min_dist = float('inf')
        
        for zone in self.zones.values():
            dist = location.euclidean_distance(zone.center)
            if dist < min_dist:
                min_dist = dist
                best_zone = zone.id
                
        # 3. 写入缓存
        self._zone_map[pos_tuple] = best_zone
        return best_zone

    def get_zone(self, zone_id: int) -> Optional[ZoneInfo]:
        return self.zones.get(zone_id)

    def _find_resource_clusters(self, map_data: MapQueryResult) -> List[Tuple[Location, int, Tuple[int,int,int,int]]]:
        """
        使用 DBSCAN + K-Means 寻找资源聚类。
        返回: List[(CenterLocation, TotalValue, BoundingBox)]
        """
        width = map_data.MapWidth
        height = map_data.MapHeight
        resources = map_data.Resources
        
        # 1. 提取所有资源点
        points = []
        point_values = {} # (x,y) -> value
        
        # Determine actual bounds based on array structure [x][y] (Column-Major)
        # verified by testing: len(resources) = Width, len(resources[0]) = Height
        array_width = len(resources)
        array_height = len(resources[0]) if array_width > 0 else 0
        
        # Use the minimum of Map dimensions and Array dimensions to avoid index errors
        # but ensure we align Width with Width and Height with Height
        scan_width = min(width, array_width)
        scan_height = min(height, array_height)
        
        # Log dimensions for debugging
        # logger.info(f"Scanning resources: Map({width}x{height}), Array({array_width}x{array_height}), Scan({scan_width}x{scan_height})")
        
        for x in range(scan_width):
            for y in range(scan_height):
                val = resources[x][y]
                
                if val > 0:
                    loc = Location(x, y)
                    points.append(loc)
                    point_values[(x,y)] = val
                    
        if not points:
            return []
            
        # 2. 初始 DBSCAN 聚类
        # eps=4.0 (grid distance), min_samples=5
        initial_clusters = SpatialClustering.dbscan_grid(points, eps=4.0, min_samples=5)
        
        final_clusters = []
        
        # 3. 对过大的聚类进行 K-Means 分割
        split_threshold = self.screen_width * 0.8
        
        for cluster in initial_clusters:
            if not cluster: continue
            
            bbox = SpatialClustering.calculate_bounding_box(cluster)
            c_width = bbox[2] - bbox[0]
            c_height = bbox[3] - bbox[1]
            
            # 如果聚类尺寸过大，尝试分割
            # 这里简单判断宽度是否超过屏幕宽度的 80%
            if c_width > split_threshold:
                k = math.ceil(c_width / split_threshold)
                # 限制最大 k 防止过度分割
                k = min(k, 4) 
                if k > 1:
                    sub_clusters = SpatialClustering.kmeans_split(cluster, k=k)
                    final_clusters.extend(sub_clusters)
                else:
                    final_clusters.append(cluster)
            else:
                final_clusters.append(cluster)
                
        # 4. 计算每个最终聚类的中心和总资源值
        result = []
        for cluster in final_clusters:
            if not cluster: continue
            
            # 计算重心
            avg_x = sum(p.x for p in cluster) // len(cluster)
            avg_y = sum(p.y for p in cluster) // len(cluster)
            center = Location(avg_x, avg_y)
            
            # 计算总价值
            total_value = sum(point_values.get((p.x, p.y), 0) for p in cluster)
            
            # 计算包围盒
            bbox = SpatialClustering.calculate_bounding_box(cluster)
            
            result.append((center, total_value, bbox))
            
        return result

    def _build_topology(self):
        """
        构建 Zone 之间的拓扑关系 (Neighbor Graph)。
        使用 Gabriel Graph 算法构建结构化拓扑：
        两个 Zone (i, j) 相连，当且仅当以 ij 为直径的圆内不包含任何其他 Zone k。
        这保证了连接的"自然性"和"无阻挡性"，无需依赖人工设定的距离阈值。
        """
        zone_ids = list(self.zones.keys())
        count = len(zone_ids)
        
        # 清除旧的邻居关系
        for z in self.zones.values():
            z.neighbors.clear()
            
        if count < 2:
            return
            
        # Gabriel Graph O(N^3) implementation
        # 考虑到 Zone 数量通常较少 (<50)，N^3 是可以接受的 (50^3 = 125,000 ops)
        
        # 预计算所有点对距离平方
        dists_sq = {}
        for i in range(count):
            id_a = zone_ids[i]
            pos_a = self.zones[id_a].center
            for j in range(count):
                id_b = zone_ids[j]
                pos_b = self.zones[id_b].center
                
                dx = pos_a.x - pos_b.x
                dy = pos_a.y - pos_b.y
                dists_sq[(id_a, id_b)] = dx*dx + dy*dy

        for i in range(count):
            id_a = zone_ids[i]
            
            for j in range(i + 1, count):
                id_b = zone_ids[j]
                
                # Check Gabriel condition:
                # Edge (a, b) exists iff for all k != a, b:
                # d^2(a, k) + d^2(b, k) >= d^2(a, b)
                
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

