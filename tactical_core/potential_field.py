# -*- coding: utf-8 -*-
import math
from typing import Dict, List, Tuple, Optional
from .entity_manager import EntityManager, TacticalEntity
from .constants import UnitCategory

class PotentialField:
    """
    基于 APF (Artificial Potential Field) 的实时微操控制器
    计算合力 -> 输出移动方向
    """
    def __init__(self, entity_manager: EntityManager):
        self.em = entity_manager
        self.cell_size = 10  # 空间网格单元大小
        
        # ---------------------------------------------------------
        # 势场参数配置
        # ---------------------------------------------------------
        
        # 1. 引力参数 (Attraction)
        self.W_ATT_TARGET_LLM = 0.5      # LLM分配目标的基础引力 (仅作引导，主要靠引擎)
        self.W_ATT_HV_ARTY = 2.5         # AFV -> 敌方ARTY (切后排)
        self.W_ATT_FODDER = 1.5          # INF_MEAT -> 敌方普通单位 (炮灰冲锋)
        self.W_ATT_FODDER_PRIORITY = 3.0 # INF_MEAT -> 敌方ARTY/INF_AT (高优冲锋)
        self.W_ATT_SHIELD = 1.2          # MBT -> 己方ARTY前方 (盾墙)
        self.W_ATT_ARMOR_HARVEST = 2.0   # MBT -> 攻击范围外的残血载具 (装甲收割引力)

        # 2. 斥力参数 (Repulsion)
        self.W_REP_DEATHZONE = 4.0       # MBT/AFV <-> 敌方INF_AT (避开反坦克步兵)
        self.W_REP_FRIENDLY = 6.0        # 友军防碰撞 (步兵散开/防碾压)
        
        # 3. 距离阈值
        self.DIST_FRIENDLY_REP = 2.0     # 友军斥力生效距离
        self.DIST_DEATHZONE = 5.0        # 死亡区域斥力生效距离
        
        # 攻击距离参数 (保守值 - 使用浮点数以便精确计算)
        self.ranges = {
            "v2rl": 10.0,
            "3tnk": 4.75,
            "4tnk": 4.75,
            "ftrk": 6.0,
            "e1": 5.0,
            "e3": 5.0
        }

    def calculate_moves(self, allies: List[TacticalEntity]) -> Dict[int, Tuple[str, int, str]]:
        """
        为一批单位计算下一帧的移动指令
        :return: {actor_id: (direction_str, distance, reason)} 
        注意：distance 限制为 1，实现逐步微调
        """
        # 获取所有敌军列表
        enemies = list(self.em.enemies.values())
        
        # 构建己方空间网格 (优化友军斥力计算)
        spatial_grid: Dict[Tuple[int, int], List[TacticalEntity]] = {}
        for ally in allies:
            if not ally.is_active:
                continue
            cx, cy = ally.position[0] // self.cell_size, ally.position[1] // self.cell_size
            key = (cx, cy)
            if key not in spatial_grid:
                spatial_grid[key] = []
            spatial_grid[key].append(ally)

        moves = {}
        for ally in allies:
            if not ally.is_active:
                continue
                
            # 计算合力
            fx, fy = self._compute_force(ally, enemies, spatial_grid)
            
            # 阈值过滤 (防止抖动)
            if abs(fx) < 0.1 and abs(fy) < 0.1:
                continue
                
            # 转换为离散方向
            direction = self._vector_to_direction(fx, fy)
            if direction:
                # 强制步长为 1，确保微操的平滑性
                moves[ally.actor_id] = (direction, 1, "[势场微操]")
                
        return moves

    def _compute_force(self, me: TacticalEntity, enemies: List[TacticalEntity], 
                       spatial_grid: Dict[Tuple[int, int], List[TacticalEntity]]) -> Tuple[float, float]:
        """计算作用在单位上的合力 (引力 - 斥力)"""
        fx, fy = 0.0, 0.0
        mx, my = me.position
        
        # =========================================================================
        # 1. 引力场 (Attraction)
        # =========================================================================
        
        # 1.1 LLM 目标引力 (Target Attraction)
        # 提供基础牵引，但如果已经在射程内，则引力减弱或为0
        if me.assigned_target_id:
            target = self.em.get_entity(me.assigned_target_id)
            if target and target.is_active:
                tx, ty = target.position
                
                dist = abs(tx - mx) + abs(ty - my)
                # 获取该单位攻击范围
                atk_range = self.ranges.get(me.unit_code, 3)
                
                # 如果距离大于射程，则产生引力
                if dist > atk_range:
                    dx, dy = tx - mx, ty - my
                    # 归一化
                    norm = max(abs(dx) + abs(dy), 0.1)
                    fx += (dx / norm) * self.W_ATT_TARGET_LLM
                    fy += (dy / norm) * self.W_ATT_TARGET_LLM
                    
        # 1.2 高价值目标引力 (High Value Attraction)
        # AFV -> ARTY (切后排)
        if me.category == UnitCategory.AFV:
            target_arty = self._find_nearest(me, [e for e in enemies if e.category == UnitCategory.ARTY])
            if target_arty:
                tx, ty = target_arty.position
                dx, dy = tx - mx, ty - my
                norm = max(abs(dx) + abs(dy), 0.1)
                fx += (dx / norm) * self.W_ATT_HV_ARTY
                fy += (dy / norm) * self.W_ATT_HV_ARTY

        # 1.3 炮灰冲锋引力 (Fodder Charge)
        # INF_MEAT -> Enemy
        if me.category == UnitCategory.INF_MEAT:
            # 优先找高优目标 (ARTY/INF_AT)
            target_prio = self._find_nearest(me, [e for e in enemies if e.category in (UnitCategory.ARTY, UnitCategory.INF_AT)])
            if target_prio:
                tx, ty = target_prio.position
                dx, dy = tx - mx, ty - my
                norm = max(abs(dx) + abs(dy), 0.1)
                fx += (dx / norm) * self.W_ATT_FODDER_PRIORITY
                fy += (dy / norm) * self.W_ATT_FODDER_PRIORITY
            else:
                # 否则找最近的任意敌人
                target_any = self._find_nearest(me, enemies)
                if target_any:
                    tx, ty = target_any.position
                    dx, dy = tx - mx, ty - my
                    norm = max(abs(dx) + abs(dy), 0.1)
                    fx += (dx / norm) * self.W_ATT_FODDER
                    fy += (dy / norm) * self.W_ATT_FODDER
        
        # 1.4 装甲收割引力 (Armor Harvest Attraction)
        # MBT -> 攻击范围外的残血载具
        if me.category == UnitCategory.MBT:
            # 寻找附近的残血载具
            # 只对范围在 (Range, Range + 4) 之间的单位产生引力
            # 范围内的由硬中断接管，范围外的太远不管
            my_range = self.ranges.get(me.unit_code, 4.0)
            attract_min_dist = my_range
            attract_max_dist = my_range + 4.0
            
            # 筛选符合条件的敌人
            candidates = []
            for e in enemies:
                if e.category in (UnitCategory.MBT, UnitCategory.ARTY, UnitCategory.AFV) and e.health_ratio < 0.35:
                    ex, ey = e.position
                    # 距离计算
                    dist = math.hypot(mx - ex, my - ey)
                    if attract_min_dist < dist < attract_max_dist:
                        candidates.append((dist, e))
            
            # 选最近的一个产生引力
            if candidates:
                candidates.sort(key=lambda x: x[0])
                nearest_low_hp = candidates[0][1]
                ex, ey = nearest_low_hp.position
                dx, dy = ex - mx, ey - my
                norm = max(abs(dx) + abs(dy), 0.1)
                fx += (dx / norm) * self.W_ATT_ARMOR_HARVEST
                fy += (dy / norm) * self.W_ATT_ARMOR_HARVEST

        # =========================================================================
        # 2. 斥力场 (Repulsion)
        # =========================================================================

        # 2.1 死亡区域斥力 (Death Zone Repulsion)
        # MBT/AFV 避开 INF_AT
        if me.category in (UnitCategory.MBT, UnitCategory.AFV):
            danger_infs = [e for e in enemies if e.category == UnitCategory.INF_AT]
            for inf in danger_infs:
                ex, ey = inf.position
                dist = abs(ex - mx) + abs(ey - my)
                
                if dist < self.DIST_DEATHZONE:
                    # 斥力
                    dx, dy = mx - ex, my - ey # 指向自己，远离敌人
                    weight = self.W_REP_DEATHZONE / (dist + 0.1)
                    fx += dx * weight
                    fy += dy * weight

        # 2.2 友方碰撞斥力 (Friendly Collision)
        # 步兵散开，避免被一锅端
        is_infantry = me.category in (UnitCategory.INF_MEAT, UnitCategory.INF_AT)
        
        if is_infantry:
            neighbors = self._get_neighbors(me, spatial_grid)
            for ally in neighbors:
                if ally.actor_id == me.actor_id:
                    continue
                
                # 判断是否需要排斥
                ally_is_infantry = ally.category in (UnitCategory.INF_MEAT, UnitCategory.INF_AT)
                
                should_repel = False
                # 步兵斥步兵
                if is_infantry and ally_is_infantry:
                    should_repel = True
                
                if should_repel:
                    ax, ay = ally.position
                    dist = abs(ax - mx) + abs(ay - my)
                    
                    if dist < self.DIST_FRIENDLY_REP: # 2格
                        dx, dy = mx - ax, my - ay
                        weight = self.W_REP_FRIENDLY / (dist + 0.1)
                        fx += dx * weight
                        fy += dy * weight

        return fx, fy

    def _get_neighbors(self, me: TacticalEntity, spatial_grid: Dict[Tuple[int, int], List[TacticalEntity]]) -> List[TacticalEntity]:
        """获取周围 3x3 网格内的友军"""
        cx, cy = me.position[0] // self.cell_size, me.position[1] // self.cell_size
        neighbors = []
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                key = (cx + dx, cy + dy)
                if key in spatial_grid:
                    neighbors.extend(spatial_grid[key])
        return neighbors

    def _find_nearest(self, me: TacticalEntity, candidates: List[TacticalEntity]) -> Optional[TacticalEntity]:
        """寻找最近的实体"""
        if not candidates:
            return None
        nearest = None
        min_dist = 99999.0
        mx, my = me.position
        for c in candidates:
            if not c.is_active: continue
            dist = abs(c.position[0] - mx) + abs(c.position[1] - my)
            if dist < min_dist:
                min_dist = dist
                nearest = c
        return nearest

    def _vector_to_direction(self, fx: float, fy: float) -> Optional[str]:
        """将合力向量转换为 4 方向指令"""
        if abs(fx) < 0.1 and abs(fy) < 0.1:
            return None
            
        # 优先选择分量大的轴
        if abs(fx) >= abs(fy):
            return "东" if fx > 0 else "西"
        else:
            return "南" if fy > 0 else "北"
