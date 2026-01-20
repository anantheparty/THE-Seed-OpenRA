from __future__ import annotations
from typing import List, Tuple, Set, Dict
import math
import collections
import random

from openra_api.models import Location

class SpatialClustering:
    """
    提供空间聚类算法，用于地图区域划分。
    不依赖 scipy/sklearn，纯 Python 实现。
    """

    @staticmethod
    def dbscan_grid(points: List[Location], eps: float, min_samples: int) -> List[List[Location]]:
        """
        基于网格优化的 DBSCAN 算法。
        
        Args:
            points: 待聚类的点集 (矿石坐标)
            eps: 邻域半径 (格)
            min_samples: 核心点所需的最小邻居数
            
        Returns:
            List[List[Location]]: 聚类结果列表，每个元素是一个簇的点集
        """
        if not points:
            return []

        # 1. 建立空间索引 (Grid Index)
        # 将空间划分为 eps 大小的网格，加速邻域查询
        grid_size = eps
        grid: Dict[Tuple[int, int], List[int]] = collections.defaultdict(list)
        
        for idx, p in enumerate(points):
            gx, gy = int(p.x / grid_size), int(p.y / grid_size)
            grid[(gx, gy)].append(idx)

        # 辅助函数：获取邻居索引
        def get_neighbors(p_idx: int) -> List[int]:
            p = points[p_idx]
            gx, gy = int(p.x / grid_size), int(p.y / grid_size)
            neighbors = []
            
            # 搜索 3x3 网格
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    cell = (gx + dx, gy + dy)
                    if cell in grid:
                        for neighbor_idx in grid[cell]:
                            # 距离检查 (Euclidean squared for speed)
                            np = points[neighbor_idx]
                            dist_sq = (p.x - np.x)**2 + (p.y - np.y)**2
                            if dist_sq <= eps**2:
                                neighbors.append(neighbor_idx)
            return neighbors

        # 2. DBSCAN 主逻辑
        labels = [-1] * len(points) # -1: Unvisited/Noise
        cluster_id = 0
        clusters = []
        
        for i in range(len(points)):
            if labels[i] != -1:
                continue
                
            neighbors = get_neighbors(i)
            
            if len(neighbors) < min_samples:
                labels[i] = 0 # Mark as Noise (temporarily 0, actual clusters start from 1)
            else:
                # Found a core point, expand cluster
                cluster_id += 1
                labels[i] = cluster_id
                current_cluster_indices = [i]
                
                # Expand
                seed_set = collections.deque(neighbors)
                # Use a set to track what's in queue to avoid duplicates
                in_queue = set(neighbors)
                
                while seed_set:
                    n_idx = seed_set.popleft()
                    
                    if labels[n_idx] == 0: # Was noise, now border point
                        labels[n_idx] = cluster_id
                        current_cluster_indices.append(n_idx)
                        
                    if labels[n_idx] != -1: # Already processed
                        continue
                        
                    labels[n_idx] = cluster_id
                    current_cluster_indices.append(n_idx)
                    
                    n_neighbors = get_neighbors(n_idx)
                    if len(n_neighbors) >= min_samples:
                        for nn_idx in n_neighbors:
                            if nn_idx not in in_queue and labels[nn_idx] == -1: # Only add unvisited
                                seed_set.append(nn_idx)
                                in_queue.add(nn_idx)
                                
                clusters.append([points[idx] for idx in current_cluster_indices])
                
        return clusters

    @staticmethod
    def kmeans_split(points: List[Location], k: int = 2, max_iter: int = 10) -> List[List[Location]]:
        """
        简单的 K-Means 算法，用于将大簇切分。
        """
        if len(points) < k:
            return [points]
            
        # 1. 初始化质心 (随机选择)
        centroids = random.sample(points, k)
        clusters = [[] for _ in range(k)]
        
        for _ in range(max_iter):
            # Assignment step
            clusters = [[] for _ in range(k)]
            for p in points:
                # Find nearest centroid
                best_idx = 0
                min_dist_sq = float('inf')
                for i, c in enumerate(centroids):
                    d_sq = (p.x - c.x)**2 + (p.y - c.y)**2
                    if d_sq < min_dist_sq:
                        min_dist_sq = d_sq
                        best_idx = i
                clusters[best_idx].append(p)
                
            # Update step
            new_centroids = []
            diff = 0
            for i in range(k):
                if not clusters[i]: # Handle empty cluster
                    new_centroids.append(centroids[i]) 
                    continue
                    
                avg_x = sum(p.x for p in clusters[i]) / len(clusters[i])
                avg_y = sum(p.y for p in clusters[i]) / len(clusters[i])
                new_c = Location(int(avg_x), int(avg_y))
                new_centroids.append(new_c)
                
                diff += (new_c.x - centroids[i].x)**2 + (new_c.y - centroids[i].y)**2
                
            centroids = new_centroids
            if diff < 1: # Converged
                break
                
        return [c for c in clusters if c]

    @staticmethod
    def calculate_bounding_box(points: List[Location]) -> Tuple[int, int, int, int]:
        """返回 (min_x, min_y, width, height)"""
        if not points:
            return 0, 0, 0, 0
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        return min_x, min_y, max_x - min_x, max_y - min_y
