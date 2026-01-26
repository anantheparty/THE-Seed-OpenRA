from typing import Dict, Tuple, Optional
from openra_api.data.dataset import CN_NAME_MAP

# 战斗单位类别定义
class UnitCategory:
    ARTY = "ARTY"       # 超视距、脆皮、面伤（火炮类）
    MBT = "MBT"         # 高血量、中程、中坚力量（主战坦克）
    AFV = "AFV"         # 高机动、反轻甲（防空车、轻坦、吉普）
    INF_MEAT = "INF_MEAT" # 低价值、高火力吸收（步兵炮灰）
    INF_AT = "INF_AT"     # 低血量、高装甲伤害（反坦克步兵、特殊步兵）
    DEFENSE = "DEFENSE"   # 防御性建筑
    AIRCRAFT = "AIRCRAFT" # 空中单位
    OTHER = "OTHER"       # 其他战斗单位

# 默认评分 (如果未在表中特别指定)
DEFAULT_CATEGORY_SCORES = {
    UnitCategory.ARTY: 8.0,
    UnitCategory.MBT: 10.0,
    UnitCategory.AFV: 4.0,
    UnitCategory.INF_MEAT: 1.0,
    UnitCategory.INF_AT: 3.0,
    UnitCategory.DEFENSE: 15.0,
    UnitCategory.AIRCRAFT: 12.0,
    UnitCategory.OTHER: 1.0
}

# 详细单位战斗信息表
# Key: Unit ID (lowercase)
# Value: (Category, Score)
# 如果 Score 为 None，则使用该 Category 的默认评分
# 仅包含 Dataset 中启用的单位
UNIT_COMBAT_INFO: Dict[str, Tuple[str, float]] = {
    # --- Infantry ---
    "e1": (UnitCategory.INF_MEAT, 1.0),   # Rifle (Allies/Soviet)
    "e3": (UnitCategory.INF_AT, 3.0),     # Rocket Soldier (Allies/Soviet)
    "e6": (UnitCategory.OTHER, 0.0),      # Engineer (Non-combat)

    # --- Vehicles ---
    "jeep": (UnitCategory.AFV, 4.0),      # Ranger
    "ftrk": (UnitCategory.AFV, 5.0),      # Flak Truck
    "1tnk": (UnitCategory.MBT, 6.0),      # Light Tank
    "2tnk": (UnitCategory.MBT, 8.0),      # Medium Tank
    "3tnk": (UnitCategory.MBT, 10.0),     # Heavy Tank
    "4tnk": (UnitCategory.MBT, 18.0),     # Mammoth Tank
    "ctnk": (UnitCategory.MBT, 15.0),     # Chrono Tank (High tech, sniper)
    
    "v2rl": (UnitCategory.ARTY, 8.0),     # V2 Rocket
    "arty": (UnitCategory.ARTY, 8.0),     # Artillery
    
    "apc": (UnitCategory.AFV, 5.0),       # APC
    "harv": (UnitCategory.OTHER, 0.0),    # Harvester (Non-combat)
    "mcv": (UnitCategory.OTHER, 0.0),     # MCV (Non-combat)

    # --- Aircraft ---
    "yak": (UnitCategory.AIRCRAFT, 8.0),
    "mig": (UnitCategory.AIRCRAFT, 12.0),
    "heli": (UnitCategory.AIRCRAFT, 12.0), # Longbow
    "mh60": (UnitCategory.AIRCRAFT, 12.0), # Blackhawk

    # --- Defenses (Structures) ---
    "pbox": (UnitCategory.DEFENSE, 8.0),   # Pillbox
    "gun": (UnitCategory.DEFENSE, 15.0),   # Turret
    "ftur": (UnitCategory.DEFENSE, 12.0),  # Flame Tower
    "sam": (UnitCategory.DEFENSE, 10.0),   # SAM Site
    "agun": (UnitCategory.DEFENSE, 12.0),  # AA Gun
    "tsla": (UnitCategory.DEFENSE, 25.0),  # Tesla Coil
}

class CombatData:
    """
    Combat data provider handling Chinese name resolution.
    """
    _CN_TO_ID: Dict[str, str] = {}

    @classmethod
    def _ensure_init(cls):
        if not cls._CN_TO_ID:
            # Build reverse map from CN_NAME_MAP
            for u_id, cn_name in CN_NAME_MAP.items():
                cls._CN_TO_ID[cn_name] = u_id.lower()

    @classmethod
    def resolve_id(cls, unit_type: str) -> Optional[str]:
        """Resolve unit type to normalized ID."""
        if not unit_type:
            return None
        cls._ensure_init()
        u_id = unit_type.lower()
        if u_id in UNIT_COMBAT_INFO:
            return u_id
        if unit_type in cls._CN_TO_ID:
            return cls._CN_TO_ID[unit_type]
        return None

    @classmethod
    def get_combat_info(cls, unit_type: str) -> Tuple[str, float]:
        """
        获取单位的战斗分类和评分。
        支持中文名称和英文 ID。
        :param unit_type: 单位类型 (case-insensitive or Chinese name)
        :return: (Category, Score)
        """
        if not unit_type:
            return UnitCategory.OTHER, 0.0
            
        cls._ensure_init()
        
        # 1. Resolve ID
        u_id = unit_type.lower()
        if u_id not in UNIT_COMBAT_INFO:
            # Try Chinese map
            if unit_type in cls._CN_TO_ID:
                u_id = cls._CN_TO_ID[unit_type]
        
        # 2. Lookup Info
        if u_id in UNIT_COMBAT_INFO:
            category, score = UNIT_COMBAT_INFO[u_id]
            if score is None:
                score = DEFAULT_CATEGORY_SCORES.get(category, 0.0)
            return category, score
            
        # 3. Default
        return UnitCategory.OTHER, 0.0

def get_unit_combat_info(unit_type: str) -> Tuple[str, float]:
    """Deprecated wrapper for compatibility."""
    return CombatData.get_combat_info(unit_type)
