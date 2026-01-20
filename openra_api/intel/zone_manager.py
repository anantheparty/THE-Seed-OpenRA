from __future__ import annotations
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass, field
import math
import collections

from openra_api.models import Location, MapQueryResult, Actor
from openra_api.data.structure_data import StructureData
from .clustering import SpatialClustering
from the_seed.utils import LogManager

logger = LogManager.get_logger()

@dataclass
class ZoneInfo:
    id: int
    center: Location
    type: str  # "RESOURCE", "BASE", "CHOKEPOINT"
    radius: int = 10
    resource_value: int = 0  # 该区域资源总量估算
    owner_faction: Optional[str] = None # 如果是 Base，归属方
    is_friendly: bool = False # 是否为我方或盟友控制
    neighbors: List[int] = field(default_factory=list)
    bounding_box: Tuple[int, int, int, int] = (0, 0, 0, 0) # min_x, min_y, max_x, max_y

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
        """
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
            
            if mine_actors:
                # 寻找该矿区内最近的矿柱
                best_mine = None
                min_dist = float('inf')
                
                # 优化：只检查包围盒内的矿柱
                # 但为了简便，先遍历所有 mine_actors (数量通常不多 < 100)
                for mine in mine_actors:
                    if not mine.position: continue
                    
                    # 检查是否在包围盒内 (扩大一点容差)
                    if (bbox[0] - 2 <= mine.position.x <= bbox[2] + 2 and 
                        bbox[1] - 2 <= mine.position.y <= bbox[3] + 2):
                        
                        dist = mine.position.euclidean_distance(center)
                        if dist < min_dist:
                            min_dist = dist
                            best_mine = mine
                
                if best_mine:
                    final_center = best_mine.position
                    snapped = True
                    logger.debug(f"Zone {zone_id} snapped to Mine {best_mine.id}")

            # 计算半径
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            radius = int(math.sqrt(width*width + height*height) / 2)
            
            self.zones[zone_id] = ZoneInfo(
                id=zone_id,
                center=final_center,
                type="RESOURCE",
                resource_value=total_value,
                radius=max(radius, 5),
                bounding_box=bbox
            )
            
        # 3. 构建拓扑关系
        self._build_topology()

    def _create_zones_from_mines(self, map_data: MapQueryResult, mine_actors: List[Actor]):
        """Deprecated: Merged into update_from_map_query as hybrid approach"""
        pass
            
        # 3. 构建拓扑关系
        self._build_topology()

    def update_resource_values(self, map_data: MapQueryResult) -> None:
        """更新现有 Zone 的资源储量 (不改变 Zone 结构)"""
        width = map_data.MapWidth
        height = map_data.MapHeight
        resources = map_data.Resources
        
        for zone in self.zones.values():
            if zone.type != "RESOURCE":
                continue
                
            # 重新扫描包围盒内的资源点
            # 优化：如果包围盒很大，可能效率低，但比全图好
            current_value = 0
            bbox = zone.bounding_box
            
            # 限制扫描范围在地图内
            min_x = max(0, bbox[0])
            min_y = max(0, bbox[1])
            max_x = min(width - 1, bbox[2])
            max_y = min(height - 1, bbox[3])
            
            for y in range(min_y, max_y + 1):
                for x in range(min_x, max_x + 1):
                    val = resources[y][x]
                    if val > 0:
                        # 检查是否属于该 Zone (通过 Voronoi 或简单距离)
                        # 为了性能，这里简单把包围盒内的都算作该 Zone 的潜在资源
                        # 更准确的做法是 check get_zone_id(x,y) == zone.id
                        # 但 get_zone_id 需要计算距离。
                        # 鉴于 Zone 互斥，我们可以只统计属于该 Zone 的点。
                        
                        if self.get_zone_id(Location(x, y)) == zone.id:
                            current_value += val
                            
            zone.resource_value = current_value

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

        # 1. 提取所有建筑 (使用 StructureData 过滤，排除围墙和非建筑)
        buildings = []
        for u in all_units:
            if not u.type: continue
            # 排除围墙
            if StructureData.is_wall(u.type):
                continue
            # 必须是已知建筑
            if StructureData.is_valid_structure(u.type):
                buildings.append(u)
        
        # 统计每个 Zone 内的建筑情况
        zone_buildings: Dict[int, List[Actor]] = {}
        
        for b in buildings:
            if not b.position:
                continue
            
            # 归属到最近的 Zone
            z_id = self.get_zone_id(b.position)
            if z_id == 0: continue
            
            if z_id not in zone_buildings:
                zone_buildings[z_id] = []
            zone_buildings[z_id].append(b)
            
        # 更新 Zone 属性
        for z_id, actors in zone_buildings.items():
            zone = self.zones.get(z_id)
            if not zone: continue
            
            has_fact = any(u.type == "fact" for u in actors)
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
            zone.is_friendly = dominant_faction in friendly_set

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
        
        for y in range(height):
            for x in range(width):
                val = resources[y][x]
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

    def find_path(self, start_zone_id: int, end_zone_id: int) -> List[int]:
        """
        寻找两个 Zone 之间的路径 (A* 算法)。
        返回: List[ZoneID] (start -> ... -> end)
        """
        if start_zone_id not in self.zones or end_zone_id not in self.zones:
            return []
            
        if start_zone_id == end_zone_id:
            return [start_zone_id]
            
        # A* Algorithm
        open_set = {start_zone_id}
        came_from = {}
        
        # g_score: 从起点到当前点的实际代价
        g_score = {zone_id: float('inf') for zone_id in self.zones}
        g_score[start_zone_id] = 0
        
        # f_score: g_score + h_score (启发式代价)
        f_score = {zone_id: float('inf') for zone_id in self.zones}
        f_score[start_zone_id] = self.zones[start_zone_id].center.euclidean_distance(self.zones[end_zone_id].center)
        
        while open_set:
            # 获取 open_set 中 f_score 最小的节点
            current = min(open_set, key=lambda id: f_score[id])
            
            if current == end_zone_id:
                return self._reconstruct_path(came_from, current)
                
            open_set.remove(current)
            
            current_zone = self.zones[current]
            for neighbor_id in current_zone.neighbors:
                neighbor_zone = self.zones[neighbor_id]
                
                # dist = distance(current, neighbor)
                dist = current_zone.center.euclidean_distance(neighbor_zone.center)
                tentative_g_score = g_score[current] + dist
                
                if tentative_g_score < g_score[neighbor_id]:
                    came_from[neighbor_id] = current
                    g_score[neighbor_id] = tentative_g_score
                    f_score[neighbor_id] = tentative_g_score + neighbor_zone.center.euclidean_distance(self.zones[end_zone_id].center)
                    
                    if neighbor_id not in open_set:
                        open_set.add(neighbor_id)
                        
        return [] # No path found

    def _reconstruct_path(self, came_from: Dict[int, int], current: int) -> List[int]:
        total_path = [current]
        while current in came_from:
            current = came_from[current]
            total_path.append(current)
        return total_path[::-1]
