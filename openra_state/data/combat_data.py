from typing import Dict, Tuple, Optional
from .dataset import cn_name_to_unit_id
from openra_api.production_names import production_name_unit_id


class UnitCategory:
    ARTY = "ARTY"
    MBT = "MBT"
    AFV = "AFV"
    INF_MEAT = "INF_MEAT"
    INF_AT = "INF_AT"
    DEFENSE = "DEFENSE"
    AIRCRAFT = "AIRCRAFT"
    OTHER = "OTHER"


DEFAULT_CATEGORY_SCORES = {
    UnitCategory.ARTY: 8.0,
    UnitCategory.MBT: 10.0,
    UnitCategory.AFV: 4.0,
    UnitCategory.INF_MEAT: 1.0,
    UnitCategory.INF_AT: 3.0,
    UnitCategory.DEFENSE: 15.0,
    UnitCategory.AIRCRAFT: 12.0,
    UnitCategory.OTHER: 1.0,
}


UNIT_COMBAT_INFO: Dict[str, Tuple[str, float]] = {
    "e1": (UnitCategory.INF_MEAT, 1.0),
    "e3": (UnitCategory.INF_AT, 3.0),
    "e6": (UnitCategory.OTHER, 0.0),
    "jeep": (UnitCategory.AFV, 4.0),
    "ftrk": (UnitCategory.AFV, 5.0),
    "1tnk": (UnitCategory.MBT, 6.0),
    "2tnk": (UnitCategory.MBT, 8.0),
    "3tnk": (UnitCategory.MBT, 10.0),
    "4tnk": (UnitCategory.MBT, 18.0),
    "ctnk": (UnitCategory.MBT, 15.0),
    "v2rl": (UnitCategory.ARTY, 8.0),
    "arty": (UnitCategory.ARTY, 8.0),
    "apc": (UnitCategory.AFV, 5.0),
    "harv": (UnitCategory.OTHER, 0.0),
    "mcv": (UnitCategory.OTHER, 0.0),
    "yak": (UnitCategory.AIRCRAFT, 8.0),
    "mig": (UnitCategory.AIRCRAFT, 12.0),
    "heli": (UnitCategory.AIRCRAFT, 12.0),
    "mh60": (UnitCategory.AIRCRAFT, 12.0),
    "pbox": (UnitCategory.DEFENSE, 8.0),
    "gun": (UnitCategory.DEFENSE, 15.0),
    "ftur": (UnitCategory.DEFENSE, 12.0),
    "sam": (UnitCategory.DEFENSE, 10.0),
    "agun": (UnitCategory.DEFENSE, 12.0),
    "tsla": (UnitCategory.DEFENSE, 25.0),
}


class CombatData:
    @classmethod
    def resolve_id(cls, unit_type: str) -> Optional[str]:
        if not unit_type:
            return None
        resolved = cn_name_to_unit_id(unit_type)
        if resolved:
            return resolved
        u_id = unit_type.lower()
        if u_id in UNIT_COMBAT_INFO:
            return u_id
        return production_name_unit_id(unit_type)

    @classmethod
    def get_combat_info(cls, unit_type: str) -> Tuple[str, float]:
        if not unit_type:
            return UnitCategory.OTHER, 0.0
        u_id = cn_name_to_unit_id(unit_type) or unit_type.lower()
        if u_id not in UNIT_COMBAT_INFO:
            u_id = production_name_unit_id(unit_type) or u_id
        if u_id in UNIT_COMBAT_INFO:
            category, score = UNIT_COMBAT_INFO[u_id]
            if score is None:
                score = DEFAULT_CATEGORY_SCORES.get(category, 0.0)
            return category, score
        return UnitCategory.OTHER, 0.0


def get_unit_combat_info(unit_type: str) -> Tuple[str, float]:
    return CombatData.get_combat_info(unit_type)
