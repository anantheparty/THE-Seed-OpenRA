# -*- coding: utf-8 -*-
from typing import List, Tuple, Dict, Optional
import math

from .entity_manager import EntityManager, TacticalEntity
from .constants import UnitCategory

class DecisionGuard:
    """
    目标管理与协同回退 (Target Management & Group Cohesion)
    
    1. 接收上游（LLM/战斗专家）的攻击对分配，更新 EntityManager。
    2. 检查每个我方单位的目标有效性（存活、存在）。
    3. 对失去目标或无目标的单位，执行“协同回退”：
       寻找附近同类友军的有效目标并跟随。
    """
    def __init__(self, entity_manager: EntityManager):
        self.em = entity_manager
        # 协同搜索半径（曼哈顿距离）
        self.cohesion_radius = 10 

    def process_decisions(self, expert_pairs: List[Tuple[int, int]]) -> List[Tuple[int, int, str]]:
        """
        处理一帧的决策流程
        :param expert_pairs: 上游传入的 [(attacker_id, target_id), ...] (流式增量或全量)
        :return: 最终修正后的攻击对 [(attacker_id, target_id, reason), ...]
        """
        # 1. 注册上游分配
        for aid, tid in expert_pairs:
            self.em.update_assignment(aid, tid)
            
        # 2. 遍历所有存活的我方战斗单位，检查并修补目标
        final_pairs: List[Tuple[int, int, str]] = []
        
        # 收集所有活动单位的引用，避免重复字典查找
        active_allies = [
            a for a in self.em.allies.values() 
            if a.category != UnitCategory.OTHER and a.is_active
        ]
        
        # 第一遍扫描：验证现有目标有效性
        for ally in active_allies:
            # 检查当前分配的目标是否有效
            if not self._is_target_valid(ally.assigned_target_id):
                # 目标失效（死/消失），清除状态，等待后续填补
                self.em.update_assignment(ally.actor_id, None)
            elif ally.assigned_target_id is not None:
                # 有效的专家/存量指令直接放行
                final_pairs.append((ally.actor_id, ally.assigned_target_id, "[协同回退:上游指令]"))

        # 3. 对无有效目标的单位执行协同回退
        for ally in active_allies:
            # 如果已有有效目标，跳过
            if ally.assigned_target_id is not None:
                continue
                
            # 尝试协同回退
            new_target_id = self._find_fallback_target(ally, active_allies)
            
            if new_target_id:
                # 更新状态
                self.em.update_assignment(ally.actor_id, new_target_id)
                final_pairs.append((ally.actor_id, new_target_id, "[协同回退:自动接管]"))
            # else: 确实无目标可用，保持待命
                
        return final_pairs

    def _is_target_valid(self, target_id: Optional[int]) -> bool:
        """检查目标ID是否在敌方列表中且存活"""
        if target_id is None:
            return False
        enemy = self.em.enemies.get(target_id)
        if not enemy:
            return False
        if not enemy.is_active:
            return False
        return True

    def _find_fallback_target(self, me: TacticalEntity, candidates: List[TacticalEntity]) -> Optional[int]:
        """
        寻找协同目标：
        1. 遍历所有友军
        2. 筛选条件：同类型(unit_code) + 有有效目标 + 不是自己
        3. 决策：选择距离自己最近的友军，继承其目标
        """
        best_target_id = None
        min_dist = 999999
        
        mx, my = me.position
        
        for buddy in candidates:
            # 排除自己
            if buddy.actor_id == me.actor_id:
                continue
            
            # 必须是同类型单位 (原代码隐式限制在 _controllers 集合中，这里显式判断)
            if buddy.unit_code != me.unit_code:
                continue
                
            # 友军必须有有效目标
            if not self._is_target_valid(buddy.assigned_target_id):
                continue
                
            bx, by = buddy.position
            dist = abs(mx - bx) + abs(my - by)
            
            # 协同半径限制 (可选)
            # if dist > self.cohesion_radius:
            #    continue
            
            if dist < min_dist:
                min_dist = dist
                best_target_id = buddy.assigned_target_id
                
        return best_target_id
