# -*- coding: utf-8 -*-
from typing import Dict, List, Tuple, Optional
from .entity_manager import EntityManager, TacticalEntity
from .constants import UnitCategory

class InterruptLogic:
    """
    战术硬中断 (Hard Interrupt Logic)
    处理高优先级的条件触发行为，其指令优先级高于普通势场移动和常规目标分配。
    """
    def __init__(self, entity_manager: EntityManager):
        self.em = entity_manager
        # 冷却时间记录 {actor_id: cooldown_end_time}
        self._retreat_cooldowns = {}
        import time
        self._time = time

        # 攻击锁定状态 {attacker_id: (target_id, timestamp)}
        self._attack_locks = {}

    def check_interrupts(self) -> Tuple[Dict[int, Tuple[str, int, str]], List[Tuple[int, int, str]]]:
        """
        检查所有硬中断条件
        :return: (override_moves, override_attacks)
            - override_moves: {actor_id: (direction, distance, reason)} 强制移动
            - override_attacks: [(attacker_id, target_id, reason), ...] 强制攻击
        """
        moves = {}
        attacks = []
        
        # 缓存当前有效的敌军列表，避免重复遍历
        active_enemies = [e for e in self.em.enemies.values() if e.is_active]
        active_enemy_ids = {e.actor_id for e in active_enemies}
        
        # 1. 维护攻击锁定状态
        # 虽然 L2/L3 已改为动态机制，但为了兼容性或未来其他逻辑可能需要锁定，保留此清理逻辑
        # 如果 self._attack_locks 为空，此循环开销极小
        for attacker_id in list(self._attack_locks.keys()):
            target_id, _ = self._attack_locks[attacker_id]
            if target_id not in active_enemy_ids:
                del self._attack_locks[attacker_id]
        
        for ally in self.em.allies.values():
            if not ally.is_active:
                continue
                
            # L1: 脆皮脱离 (High Priority)
            if ally.category in (UnitCategory.ARTY, UnitCategory.AFV):
                if ally.health_ratio < 0.35:
                    # 检查冷却时间，避免过于频繁触发撤退导致无法重新投入战斗或来回鬼畜
                    now = self._time.time()
                    cooldown_end = self._retreat_cooldowns.get(ally.actor_id, 0.0)
                    if now < cooldown_end:
                        pass # 冷却中，继续检查其他逻辑（如是否有锁定目标需要继续攻击）
                    else:
                        threat_dir = self._calculate_threat_direction(ally, active_enemies, range_limit=6)
                        if threat_dir:
                            # 向威胁的反方向移动
                            escape_dir = self._invert_direction(threat_dir)
                            if escape_dir:
                                # 逃跑不再受限于单步微调，distance=3以快速脱离
                                moves[ally.actor_id] = (escape_dir, 3, "[战术硬中断:脆皮脱离]")
                                # 设置冷却时间 5 秒，给单位时间执行移动和重新评估
                                self._retreat_cooldowns[ally.actor_id] = now + 5.0
                                # 逃跑时不再执行其他逻辑，也不再维持攻击锁定
                                if ally.actor_id in self._attack_locks:
                                    del self._attack_locks[ally.actor_id]
                                continue

            # 优先检查是否存在有效的攻击锁定
            # 如果之前已经锁定了某个硬中断目标，且该目标仍存活，则继续攻击该目标，避免频繁切换
            if ally.actor_id in self._attack_locks:
                target_id, _ = self._attack_locks[ally.actor_id]
                # 再次确认目标是否在射程内/符合条件（可选，这里简化为只要存活就继续打，直到死）
                attacks.append((ally.actor_id, target_id, "[战术硬中断:锁定追击]"))
                continue

            # L2: 装甲收割 (High Priority - Promotion)
            # 敌载具 (MBT/ARTY/AFV) HP<35% -> 范围内 MBT 强制集火
            if ally.category == UnitCategory.MBT:
                # 寻找攻击范围内的残血敌军载具
                target_low_hp = self._find_low_hp_enemy_in_range(ally, active_enemies)
                if target_low_hp:
                    attacks.append((ally.actor_id, target_low_hp.actor_id, "[战术硬中断:装甲收割]"))
                    # 动态机制：不设置锁定，每一帧都重新评估，确保总是攻击血量最低的单位
                    # self._attack_locks[ally.actor_id] = (target_low_hp.actor_id, current_time)
                    continue

            # L3: 威胁剥离 (Medium Priority - Demotion)
            # 4TNK 范围内有 UnitCategory.INF_AT (如 E3) -> 强制攻击
            # 注意：因为 4TNK 有副武器可有效对付步兵，或者需要优先清除高威胁单位（比如以后可以加飞机）
            if ally.unit_code == "4tnk":
                # 寻找最近的反坦克步兵 (INF_AT)
                target_at = self._find_nearest_enemy_by_category(ally, active_enemies, UnitCategory.INF_AT, range_limit=6)
                if target_at:
                    attacks.append((ally.actor_id, target_at.actor_id, "[战术硬中断:威胁剥离]"))
                    # 动态机制：不设置锁定，每一帧都重新评估，确保总是攻击最近的威胁
                    # self._attack_locks[ally.actor_id] = (target_at.actor_id, current_time)
                    continue

        return moves, attacks

    def _calculate_threat_direction(self, me: TacticalEntity, enemies: List[TacticalEntity], range_limit: int) -> Optional[str]:
        """计算最近威胁的方位"""
        mx, my = me.position
        nearest = None
        min_dist = range_limit + 1
        
        for e in enemies:
            # 仅认为 MBT 是主要威胁，避免因步兵等低威胁单位导致过度撤退
            if e.category == UnitCategory.MBT:
                ex, ey = e.position
                dist = abs(mx - ex) + abs(my - ey)
                if dist < min_dist:
                    min_dist = dist
                    nearest = e
                
        if nearest and min_dist <= range_limit:
            dx = nearest.position[0] - mx
            dy = nearest.position[1] - my
            if abs(dx) >= abs(dy):
                return "东" if dx > 0 else "西"
            else:
                return "南" if dy > 0 else "北"
        return None

    def _invert_direction(self, direction: str) -> Optional[str]:
        mapping = {"东": "西", "西": "东", "南": "北", "北": "南"}
        return mapping.get(direction)

    def _find_nearest_enemy_by_category(self, me: TacticalEntity, enemies: List[TacticalEntity], target_category: UnitCategory, range_limit: int) -> Optional[TacticalEntity]:
        mx, my = me.position
        nearest = None
        min_dist = range_limit + 1
        
        for e in enemies:
            if e.category == target_category:
                ex, ey = e.position
                dist = abs(mx - ex) + abs(my - ey)
                if dist < min_dist:
                    min_dist = dist
                    nearest = e
        
        return nearest if min_dist <= range_limit else None

    def _find_low_hp_enemy_in_range(self, me: TacticalEntity, enemies: List[TacticalEntity]) -> Optional[TacticalEntity]:
        """
        寻找攻击范围内的残血敌军载具
        优先选择血量最低的单位
        """
        import math
        
        # 攻击范围定义 (保守值)
        ranges = {
            "v2rl": 10.0,
            "3tnk": 4.75,
            "4tnk": 4.75,
            "ftrk": 6.0,
            "e1": 5.0,
            "e3": 5.0
        }
        
        my_range = ranges.get(me.unit_code, 4.0)
        mx, my = me.position
        
        candidates = []
        
        for e in enemies:
            # 筛选残血载具 (MBT/ARTY/AFV)
            if e.category in (UnitCategory.MBT, UnitCategory.ARTY, UnitCategory.AFV):
                if e.health_ratio < 0.35:
                    ex, ey = e.position
                    # 使用欧几里得距离进行精确范围判定
                    dist = math.hypot(mx - ex, my - ey)
                    
                    if dist <= my_range:
                        # 获取绝对血量用于排序 (若无则用比例)
                        hp = e.raw_actor.get("hp", 9999)
                        candidates.append((hp, dist, e))
        
        if not candidates:
            return None
            
        # 排序优先级: 1. 血量最低 2. 距离最近
        candidates.sort(key=lambda x: (x[0], x[1]))
        
        return candidates[0][2]
