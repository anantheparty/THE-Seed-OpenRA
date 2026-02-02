# -*- coding: utf-8 -*-
from typing import Dict, List, Optional, Set, Tuple, Any, Protocol
import time
import math
from dataclasses import dataclass, field

from .constants import UnitCategory, UNIT_CATEGORY_MAP, IGNORED_UNIT_CODES, STANDARD_NAME_MAP

@dataclass
class TacticalEntity:
    """战术实体：封装原始Actor并附加战术状态"""
    actor_id: int
    raw_actor: dict
    unit_code: str
    category: UnitCategory
    health_ratio: float = 1.0
    position: Tuple[int, int] = (0, 0)
    
    # 动态战术属性
    threat_level: float = 0.0  # 威胁值（基于周围敌军）
    is_active: bool = True     # 是否存活/有效
    assigned_target_id: Optional[int] = None  # 上游或自动分配的当前攻击目标ID

class EntityManager:
    """
    实时数据哨兵 (State Sentinel)
    负责维护战场实体的生命周期、状态同步与衍生数据计算。
    """
    def __init__(self, client):
        self.client = client
        
        # 实体仓库 {actor_id: TacticalEntity}
        self.allies: Dict[int, TacticalEntity] = {}
        self.enemies: Dict[int, TacticalEntity] = {}
        
        # 距离矩阵缓存 (id_a, id_b) -> distance
        # 注意：为节省内存，仅存储必要的交互对，或在每帧计算时临时生成
        self._distance_cache: Dict[Tuple[int, int], int] = {}
        
        self.last_update_time: float = 0.0

    def _get_unit_code(self, actor_type: str) -> str:
        """
        获取标准化的单位代码
        1. 使用内部 fallback 映射 (STANDARD_NAME_MAP)
        2. 默认返回小写原始名称
        """
        raw_or_mapped = str(actor_type).lower()
        
        # 内部标准化 (将中文别名等转为标准代码)
        return STANDARD_NAME_MAP.get(raw_or_mapped, raw_or_mapped)

    def _create_or_update_entity(self, actor_data: dict, store: Dict[int, TacticalEntity]) -> Optional[TacticalEntity]:
        """处理单个Actor的更新逻辑"""
        # 1. 基础有效性检查
        # 引擎特性：若单位在迷雾中，query_actor 根本不会返回该单位的数据
        # 因此，只要收到了数据，就必定包含有效信息 (position 等)
        if not actor_data or not actor_data.get("position"):
            return None
            
        # 2. 获取代码并检查黑名单
        atype = actor_data.get("type", "")
        code = self._get_unit_code(atype)
        
        # 检查黑名单：只要包含任意黑名单关键字即过滤
        if any(ignored in code for ignored in IGNORED_UNIT_CODES):
            return None
            
        aid = actor_data["id"]
        
        # 3. 计算生命值比例
        hp_ratio = 1.0
        hp = actor_data.get("hp")
        max_hp = actor_data.get("maxHp")
        if max_hp and max_hp > 0 and hp is not None:
            hp_ratio = float(hp) / float(max_hp)
            
        pos = (actor_data["position"]["x"], actor_data["position"]["y"])
        
        # 4. 更新或创建
        if aid in store:
            # 更新现有实体
            entity = store[aid]
            entity.raw_actor = actor_data
            entity.health_ratio = hp_ratio
            entity.position = pos
            entity.is_active = True
        else:
            # 创建新实体
            # 默认归类为 OTHER
            cat = UNIT_CATEGORY_MAP.get(code, UnitCategory.OTHER)
            
            entity = TacticalEntity(
                actor_id=aid,
                raw_actor=actor_data,
                unit_code=code,
                category=cat,
                health_ratio=hp_ratio,
                position=pos
            )
            store[aid] = entity
            
        return entity

    def update(self) -> None:
        """
        每Tick调用的主更新循环
        1. 拉取最新API数据
        2. 同步本地实体列表（标记消失的单位）
        3. 重建距离矩阵（可选/按需）
        4. 计算威胁等级
        """
        self.last_update_time = time.time()
        
        # --- 1. 拉取数据 (使用独立 client) ---
        raw_allies = []
        raw_enemies = []
        try:
            # 查询己方
            resp_allies = self.client.query_all_units(faction="己方")
            if resp_allies is None:
                raise ConnectionError("Failed to query allies")
            raw_allies = resp_allies
            
            # 查询敌方
            resp_enemies = self.client.query_all_units(faction="敌方")
            if resp_enemies is None:
                raise ConnectionError("Failed to query enemies")
            raw_enemies = resp_enemies

        except Exception:
            # 关键修复：如果连接断开，应该清空所有实体，而不是保持僵尸状态
            self.allies.clear()
            self.enemies.clear()
            return

        # --- 2. 同步状态 (Mark & Sweep) ---
        
        # 己方同步
        current_ally_ids = set()
        for actor in raw_allies:
            ent = self._create_or_update_entity(actor, self.allies)
            if ent:
                current_ally_ids.add(ent.actor_id)
        
        # 清理消失的己方单位
        for aid in list(self.allies.keys()):
            if aid not in current_ally_ids:
                del self.allies[aid]
                
        # 敌方同步
        current_enemy_ids = set()
        for actor in raw_enemies:
            ent = self._create_or_update_entity(actor, self.enemies)
            if ent:
                current_enemy_ids.add(ent.actor_id)
                
        # 清理消失的敌方单位
        for aid in list(self.enemies.keys()):
            if aid not in current_enemy_ids:
                del self.enemies[aid]

        # --- 3. 计算衍生数据 (威胁值与距离) ---
        self._calculate_threat_levels()

    def _manhattan_dist(self, pos1: Tuple[int, int], pos2: Tuple[int, int]) -> int:
        return abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1])

    def _calculate_threat_levels(self):
        """
        计算每个己方单位的威胁等级
        Threat = sum(Enemy_Weight / Distance) for enemies in range
        """
        # 简单的威胁评估示例：
        # 对每个己方单位，遍历所有敌方单位，如果距离够近，则累加威胁值
        # 优化：仅对核心战斗单位计算
        
        for ally in self.allies.values():
            if ally.category == UnitCategory.OTHER:
                continue
                
            threat = 0.0
            ax, ay = ally.position
            
            for enemy in self.enemies.values():
                ex, ey = enemy.position
                dist = abs(ax - ex) + abs(ay - ey)
                
                # 忽略过远的敌人 (例如 > 15格)
                if dist > 15:
                    continue
                
                # 基础威胁权重 (可根据 enemy.category 细化)
                base_threat = 10.0
                if enemy.category == UnitCategory.ARTY:
                    base_threat = 15.0 # 火炮高威胁
                elif enemy.category == UnitCategory.INF_AT and ally.category in (UnitCategory.MBT, UnitCategory.AFV):
                    base_threat = 20.0 # 反坦克步兵对载具高威胁
                
                # 距离衰减：距离越近威胁越大，防止除零
                weight = base_threat / max(1.0, float(dist))
                threat += weight
            
            ally.threat_level = threat

    def get_entity(self, actor_id: int) -> Optional[TacticalEntity]:
        """通过ID获取实体（己方或敌方）"""
        return self.allies.get(actor_id) or self.enemies.get(actor_id)

    def get_distance(self, id_a: int, id_b: int) -> int:
        """获取两个单位间的曼哈顿距离"""
        ea = self.get_entity(id_a)
        eb = self.get_entity(id_b)
        if ea and eb:
            return self._manhattan_dist(ea.position, eb.position)
        return 9999

    def update_assignment(self, attacker_id: int, target_id: Optional[int]) -> None:
        """更新攻击者分配的目标ID"""
        attacker = self.allies.get(attacker_id)
        if attacker:
            attacker.assigned_target_id = target_id
