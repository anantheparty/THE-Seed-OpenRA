from typing import Dict, Tuple

class UnitCategory:
    ARTY = "ARTY"
    MBT = "MBT"
    AFV = "AFV"
    INF_MEAT = "INF_MEAT"
    INF_AT = "INF_AT"
    DEFENSE = "DEFENSE"
    AIRCRAFT = "AIRCRAFT"
    OTHER = "OTHER"

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
    # Defense structures - for exclusion logic if needed
    "pbox": (UnitCategory.DEFENSE, 8.0),
    "gun": (UnitCategory.DEFENSE, 15.0),
    "ftur": (UnitCategory.DEFENSE, 12.0),
    "sam": (UnitCategory.DEFENSE, 10.0),
    "agun": (UnitCategory.DEFENSE, 12.0),
    "tsla": (UnitCategory.DEFENSE, 25.0),
}

def get_combat_info(unit_type: str) -> Tuple[str, float]:
    """
    Get (Category, Score) for a unit type.
    Returns (OTHER, 0.0) if unknown.
    """
    if not unit_type:
        return UnitCategory.OTHER, 0.0
    
    u_id = unit_type.lower()
    return UNIT_COMBAT_INFO.get(u_id, (UnitCategory.OTHER, 0.0))
