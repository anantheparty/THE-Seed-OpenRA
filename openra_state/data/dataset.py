from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class UnitInfo:
    id: str
    name_cn: str
    cost: int
    power: int = 0
    prerequisites: List[str] = field(default_factory=list)
    category: str = "Unknown"
    faction: str = "Both"


@dataclass(frozen=True)
class DemoCapabilityTruth:
    unit_type: str
    queue_type: str | None
    display_name: str
    prompt_display_name: str
    prerequisites: tuple[str, ...]
    faction: str | None
    in_demo_roster: bool


CN_NAME_MAP = {
    "POWR": "发电厂",
    "APWR": "核电站",
    "PROC": "矿场",
    "SILO": "储存罐",
    "BARR": "兵营", # 苏盟兵营引擎返回值相同，因此无法区分
    "TENT": "兵营", # 所以现在即使是苏军兵营也会显示为"tent"
    "WEAP": "战车工厂",
    "FACT": "建造厂",
    "FIX": "维修厂",
    "SYRD": "船坞",
    "SPEN": "潜艇基地",
    "AFLD": "空军基地",
    "HPAD": "直升机坪",
    "DOME": "雷达站",
    "ATEK": "盟军科技中心",
    "STEK": "科技中心",
    "KENN": "军犬窝",
    "BIO": "生物实验室",
    "GAP": "裂缝产生器",
    "PDOX": "超时空传送仪",
    "TSLA": "特斯拉塔",
    "IRON": "铁幕装置",
    "MSLO": "核弹发射井",
    "PBOX": "碉堡",
    "HBOX": "伪装碉堡",
    "GUN": "炮塔",
    "FTUR": "火焰塔",
    "SAM": "防空导弹",
    "AGUN": "防空炮",
    "E1": "步兵",
    "E2": "掷弹兵",
    "E3": "火箭兵",
    "E4": "喷火兵",
    "E6": "工程师",
    "E7": "谭雅",
    "DOG": "军犬",
    "MEDIC": "医疗兵",
    "MECH": "机械师",
    "SPY": "间谍",
    "THIEF": "小偷",
    "SHOK": "磁暴步兵",
    "HARV": "采矿车",
    "MCV": "基地车",
    "JEEP": "吉普车",
    "APC": "装甲运输车",
    "ARTY": "榴弹炮",
    "V2RL": "V2火箭发射车",
    "1TNK": "轻坦克",
    "2TNK": "中型坦克",
    "3TNK": "重型坦克",
    "CTNK": "超时空坦克",
    "4TNK": "超重型坦克",
    "MGG": "移动裂缝产生器",
    "MRJ": "雷达干扰车",
    "DTRK": "自爆卡车",
    "TTNK": "特斯拉坦克",
    "FTRK": "防空车",
    "MNLY": "地雷部署车",
    "QTNK": "震荡坦克",
    "YAK": "雅克战机",
    "MIG": "米格战机",
    "HIND": "雌鹿直升机",
    "HELI": "长弓武装直升机",
    "BADR": "贝德獾轰炸机",
    "U2": "侦察机",
    "MH60": "黑鹰直升机",
    "TRAN": "运输直升机",
    "SS": "潜艇",
    "MSUB": "导弹潜艇",
    "DD": "驱逐舰",
    "CA": "巡洋舰",
    "LST": "运输艇",
    "PT": "炮艇",
}

_CN_NAME_TO_UNIT_ID = {
    cn_name: unit_id.lower()
    for unit_id, cn_name in CN_NAME_MAP.items()
}

# Shared runtime hint tables used by routing / reservation code.
_HINT_TO_UNIT: dict[str, tuple[str, str]] = {
    # (unit_type, queue_type)
    "重坦": ("3tnk", "Vehicle"), "重型坦克": ("3tnk", "Vehicle"), "坦克": ("3tnk", "Vehicle"),
    "天启": ("4tnk", "Vehicle"), "天启坦克": ("4tnk", "Vehicle"),
    "磁暴": ("ttnk", "Vehicle"), "磁暴坦克": ("ttnk", "Vehicle"),
    "火箭车": ("v2rl", "Vehicle"), "V2": ("v2rl", "Vehicle"), "v2rl": ("v2rl", "Vehicle"),
    "矿车": ("harv", "Vehicle"), "采矿车": ("harv", "Vehicle"),
    "地雷": ("mnly", "Vehicle"),
    "步兵": ("e1", "Infantry"), "步枪兵": ("e1", "Infantry"),
    "火箭兵": ("e3", "Infantry"), "火箭步兵": ("e3", "Infantry"),
    "工程师": ("e6", "Infantry"),
    "狗": ("dog", "Infantry"), "军犬": ("dog", "Infantry"),
    "电厂": ("powr", "Building"), "发电厂": ("powr", "Building"),
    "兵营": ("barr", "Building"),
    "矿场": ("proc", "Building"), "精炼厂": ("proc", "Building"),
    "战车工厂": ("weap", "Building"), "坦克厂": ("weap", "Building"),
    "雷达": ("dome", "Building"), "雷达站": ("dome", "Building"),
}

_UNIT_TYPE_TO_QUEUE: dict[str, str] = {}
for _unit_type, _queue_type in _HINT_TO_UNIT.values():
    _UNIT_TYPE_TO_QUEUE.setdefault(_unit_type, _queue_type)

_CATEGORY_DEFAULTS: dict[str, tuple[str, str]] = {
    "infantry": ("e1", "Infantry"),
    "vehicle": ("3tnk", "Vehicle"),
    "building": ("powr", "Building"),
}

_CATEGORY_TO_ACTOR_CATEGORY: dict[str, str] = {
    "infantry": "infantry",
    "vehicle": "vehicle",
    "building": "building",
}


def infer_unit_type_for_request(category: str, hint: str) -> tuple[str | None, str | None]:
    """Infer a concrete (unit_type, queue_type) pair from request category + hint."""
    for keyword, (unit_type, queue_type) in _HINT_TO_UNIT.items():
        if keyword in hint:
            return unit_type, queue_type
    default = _CATEGORY_DEFAULTS.get(category)
    if default:
        return default
    return None, None

_UNIT_TO_QUEUE_TYPE: dict[str, str] = {
    # buildings
    "powr": "Building", "apwr": "Building", "proc": "Building", "barr": "Building",
    "weap": "Building", "dome": "Building", "fix": "Building", "kenn": "Building",
    "silo": "Building",
    # infantry
    "e1": "Infantry", "e2": "Infantry", "e3": "Infantry", "e6": "Infantry", "dog": "Infantry",
    # vehicles
    "ftrk": "Vehicle", "v2rl": "Vehicle", "3tnk": "Vehicle", "4tnk": "Vehicle",
    "harv": "Vehicle", "mcv": "Vehicle", "mnly": "Vehicle", "ttnk": "Vehicle",
    "jeep": "Vehicle", "2tnk": "Vehicle",
    # aircraft
    "mig": "Aircraft", "yak": "Aircraft",
}

DATASET: Dict[str, UnitInfo] = {}


def register(unit: UnitInfo):
    DATASET[unit.id.upper()] = unit
    DATASET[unit.id.lower()] = unit


def cn_name_to_unit_id(name_cn: str) -> str | None:
    """Resolve a Chinese display name to the normalized unit/building id."""
    if not name_cn:
        return None
    return _CN_NAME_TO_UNIT_ID.get(str(name_cn))

# ==========================================
# STRUCTURES
# ==========================================

# Common
register(UnitInfo(id="POWR", name_cn="发电厂", cost=150, power=100, category="Building", prerequisites=["fact"]))
register(UnitInfo(id="APWR", name_cn="核电站", cost=250, power=200, category="Building", prerequisites=["dome", "fact"]))
register(UnitInfo(id="PROC", name_cn="矿场", cost=700, power=-30, category="Building", prerequisites=["powr", "fact"]))
# register(UnitInfo(id="SILO", name_cn="储存罐", cost=75, power=-10, category="Building", prerequisites=["proc", "fact"]))
register(UnitInfo(id="FACT", name_cn="建造厂", cost=1000, power=0, category="Building", prerequisites=[]))
register(UnitInfo(id="WEAP", name_cn="战车工厂", cost=1000, power=-30, category="Building", prerequisites=["proc", "fact"])) 
register(UnitInfo(id="FIX", name_cn="维修厂", cost=600, power=-30, category="Building", prerequisites=["weap", "fact"])) 
register(UnitInfo(id="DOME", name_cn="雷达站", cost=750, power=-40, category="Building", prerequisites=["proc", "fact"]))

# Allies
register(UnitInfo(id="TENT", name_cn="兵营", cost=250, power=-20, category="Building", faction="Allies", prerequisites=["powr", "fact"]))
register(UnitInfo(id="ATEK", name_cn="盟军科技中心", cost=750, power=-200, category="Building", faction="Allies", prerequisites=["weap", "dome", "fact"]))
# register(UnitInfo(id="GAP", name_cn="控制点", cost=400, power=-60, category="Building", faction="Allies", prerequisites=["atek", "fact"]))
# register(UnitInfo(id="PDOX", name_cn="超时空传送仪", cost=750, power=-200, category="Building", faction="Allies", prerequisites=["atek", "fact"]))
register(UnitInfo(id="AGUN", name_cn="防空炮", cost=400, power=-50, category="Building", faction="Allies", prerequisites=["dome", "fact"]))
register(UnitInfo(id="PBOX", name_cn="碉堡", cost=300, power=-20, category="Building", faction="Allies", prerequisites=["tent", "fact"]))
# register(UnitInfo(id="HBOX", name_cn="伪装碉堡", cost=375, power=-20, category="Building", faction="Allies", prerequisites=["tent", "fact"]))
register(UnitInfo(id="GUN", name_cn="炮塔", cost=400, power=-40, category="Building", faction="Allies", prerequisites=["tent", "fact"]))
register(UnitInfo(id="HPAD", name_cn="直升机坪", cost=250, power=-10, category="Building", faction="Allies", prerequisites=["dome", "fact"]))
# register(UnitInfo(id="SYRD", name_cn="船坞", cost=500, power=-30, category="Building", faction="Allies", prerequisites=["powr", "fact"]))

# Soviet
register(UnitInfo(id="BARR", name_cn="兵营", cost=250, power=-20, category="Building", faction="Soviet", prerequisites=["powr", "fact"]))
# register(UnitInfo(id="SPEN", name_cn="潜艇基地", cost=400, power=-30, category="Building", faction="Soviet", prerequisites=["powr", "fact"]))
register(UnitInfo(id="STEK", name_cn="科技中心", cost=750, power=-100, category="Building", faction="Soviet", prerequisites=["weap", "dome", "fact"]))
register(UnitInfo(id="TSLA", name_cn="特斯拉塔", cost=600, power=-100, category="Building", faction="Soviet", prerequisites=["weap", "fact"]))
register(UnitInfo(id="FTUR", name_cn="火焰塔", cost=300, power=-20, category="Building", faction="Soviet", prerequisites=["barr", "fact"]))
register(UnitInfo(id="SAM", name_cn="防空导弹", cost=350, power=-40, category="Building", faction="Soviet", prerequisites=["dome", "fact"]))
# register(UnitInfo(id="MSLO", name_cn="核弹发射井", cost=1250, power=-150, category="Building", prerequisites=["stek", "atek", "fact"]))
# register(UnitInfo(id="IRON", name_cn="铁幕装置", cost=750, power=-200, category="Building", faction="Soviet", prerequisites=["stek", "fact"]))
register(UnitInfo(id="AFLD", name_cn="空军基地", cost=250, power=-20, category="Building", faction="Soviet", prerequisites=["dome", "fact"])) 
# register(UnitInfo(id="KENN", name_cn="军犬窝", cost=100, power=-10, category="Building", faction="Soviet", prerequisites=["powr", "fact"]))

# ==========================================
# VEHICLES
# ==========================================

# Allies
register(UnitInfo(id="1TNK", name_cn="轻坦克", cost=350, category="Vehicle", faction="Allies", prerequisites=["weap"]))
register(UnitInfo(id="2TNK", name_cn="中型坦克", cost=425, category="Vehicle", faction="Allies", prerequisites=["fix", "weap"]))
register(UnitInfo(id="JEEP", name_cn="吉普车", cost=250, category="Vehicle", faction="Allies", prerequisites=["weap"]))
register(UnitInfo(id="ARTY", name_cn="榴弹炮", cost=425, category="Vehicle", faction="Allies", prerequisites=["dome", "weap"])) 
# register(UnitInfo(id="MRJ", name_cn="雷达干扰车", cost=500, category="Vehicle", faction="Allies", prerequisites=["atek", "weap"]))
# register(UnitInfo(id="MGG", name_cn="移动裂缝产生器", cost=500, category="Vehicle", faction="Allies", prerequisites=["atek", "weap"]))
register(UnitInfo(id="CTNK", name_cn="超时空坦克", cost=675, category="Vehicle", faction="Allies", prerequisites=["atek", "weap"]))
# register(UnitInfo(id="STNK", name_cn="相位运输车", cost=500, category="Vehicle", faction="Allies", prerequisites=["atek", "weap"])) 

# Soviet
register(UnitInfo(id="3TNK", name_cn="重型坦克", cost=575, category="Vehicle", faction="Soviet", prerequisites=["fix", "weap"]))
register(UnitInfo(id="4TNK", name_cn="超重型坦克", cost=1000, category="Vehicle", faction="Soviet", prerequisites=["fix", "stek", "weap"]))
register(UnitInfo(id="V2RL", name_cn="V2火箭发射车", cost=450, category="Vehicle", faction="Soviet", prerequisites=["dome", "weap"]))
register(UnitInfo(id="APC", name_cn="装甲运输车", cost=425, category="Vehicle", faction="Soviet", prerequisites=["weap"]))
register(UnitInfo(id="FTRK", name_cn="防空车", cost=300, category="Vehicle", faction="Soviet", prerequisites=["weap"]))
# register(UnitInfo(id="TTNK", name_cn="特斯拉坦克", cost=675, category="Vehicle", faction="Soviet", prerequisites=["stek", "weap"]))
# register(UnitInfo(id="DTRK", name_cn="自爆卡车", cost=1250, category="Vehicle", faction="Soviet", prerequisites=["stek", "weap"]))
# register(UnitInfo(id="QTNK", name_cn="震荡坦克", cost=1000, category="Vehicle", faction="Soviet", prerequisites=["stek", "weap"]))

# Shared
register(UnitInfo(id="HARV", name_cn="采矿车", cost=550, category="Vehicle", prerequisites=["proc", "weap"]))
register(UnitInfo(id="MCV", name_cn="基地车", cost=1000, category="Vehicle", prerequisites=["fix", "weap"]))
# register(UnitInfo(id="MNLY", name_cn="地雷部署车", cost=400, category="Vehicle", prerequisites=["fix", "weap"]))

# # ==========================================
# INFANTRY
# ==========================================

# Allies
register(UnitInfo(id="E1", name_cn="步兵", cost=50, category="Infantry", faction="Allies", prerequisites=["tent"]))
register(UnitInfo(id="E3", name_cn="火箭兵", cost=150, category="Infantry", faction="Allies", prerequisites=["tent"]))
register(UnitInfo(id="E6", name_cn="工程师", cost=200, category="Infantry", faction="Allies", prerequisites=["tent"]))
# register(UnitInfo(id="E7", name_cn="谭雅", cost=750, category="Infantry", faction="Allies", prerequisites=["atek", "tent"]))
# register(UnitInfo(id="MEDIC", name_cn="医疗兵", cost=100, category="Infantry", faction="Allies", prerequisites=["tent"]))
# register(UnitInfo(id="MECH", name_cn="机械师", cost=250, category="Infantry", faction="Allies", prerequisites=["fix", "tent"]))
# register(UnitInfo(id="SPY", name_cn="间谍", cost=250, category="Infantry", faction="Allies", prerequisites=["dome", "tent"]))
# register(UnitInfo(id="THIEF", name_cn="小偷", cost=250, category="Infantry", faction="Allies", prerequisites=["tech", "tent"])) 

# Soviet 
register(UnitInfo(id="E1", name_cn="步兵", cost=50, category="Infantry", faction="Soviet", prerequisites=["barr"]))
# register(UnitInfo(id="E2", name_cn="掷弹兵", cost=80, category="Infantry", faction="Soviet", prerequisites=["barr"]))
register(UnitInfo(id="E3", name_cn="火箭兵", cost=150, category="Infantry", faction="Soviet", prerequisites=["barr"]))
# register(UnitInfo(id="E4", name_cn="喷火兵", cost=150, category="Infantry", faction="Soviet", prerequisites=["ftur", "barr"]))
register(UnitInfo(id="E6", name_cn="工程师", cost=200, category="Infantry", faction="Soviet", prerequisites=["barr"]))
# register(UnitInfo(id="DOG", name_cn="军犬", cost=100, category="Infantry", faction="Soviet", prerequisites=["kenn", "barr"]))
# register(UnitInfo(id="SHOK", name_cn="磁暴步兵", cost=175, category="Infantry", faction="Soviet", prerequisites=["stek", "tsla", "barr"]))

# ==========================================
# AIRCRAFT
# ==========================================

register(UnitInfo(id="YAK", name_cn="雅克战机", cost=675, category="Aircraft", faction="Soviet", prerequisites=["afld"]))
register(UnitInfo(id="MIG", name_cn="米格战机", cost=1000, category="Aircraft", faction="Soviet", prerequisites=["afld"]))
# register(UnitInfo(id="HIND", name_cn="雌鹿直升机", cost=600, category="Aircraft", faction="Soviet", prerequisites=["afld"]))
register(UnitInfo(id="HELI", name_cn="长弓武装直升机", cost=1000, category="Aircraft", faction="Allies", prerequisites=["hpad", "atek"]))
# register(UnitInfo(id="BADR", name_cn="贝德獾轰炸机", cost=1000, category="Aircraft", faction="Soviet", prerequisites=["afld"]))
register(UnitInfo(id="MH60", name_cn="黑鹰直升机", cost=750, category="Aircraft", faction="Allies", prerequisites=["hpad"]))
# register(UnitInfo(id="TRAN", name_cn="运输直升机", cost=600, category="Aircraft", prerequisites=["hpad"]))

# ==========================================
# SHIPS
# ==========================================

# register(UnitInfo(id="SS", name_cn="潜艇", cost=475, category="Ship", faction="Soviet", prerequisites=["spen"]))
# register(UnitInfo(id="MSUB", name_cn="导弹潜艇", cost=825, category="Ship", faction="Soviet", prerequisites=["spen", "stek"]))
# register(UnitInfo(id="DD", name_cn="驱逐舰", cost=500, category="Ship", faction="Allies", prerequisites=["syrd"]))
# register(UnitInfo(id="CA", name_cn="巡洋舰", cost=1000, category="Ship", faction="Allies", prerequisites=["syrd", "atek"]))
# register(UnitInfo(id="LST", name_cn="运输艇", cost=350, category="Ship", prerequisites=["syrd"])) # or spen
# register(UnitInfo(id="PT", name_cn="炮艇", cost=250, category="Ship", faction="Allies", prerequisites=["syrd"]))


_DEMO_SHARED_CAPABILITY_ROSTER: dict[str, tuple[str, ...]] = {
    "Building": ("powr", "proc", "barr", "weap", "dome", "fix"),
    "Infantry": ("e1", "e3"),
    "Vehicle": ("harv",),
    "Aircraft": (),
}
_DEMO_FACTION_CAPABILITY_ROSTER: dict[str, dict[str, tuple[str, ...]]] = {
    "allied": {
        "Building": (),
        "Infantry": (),
        "Vehicle": ("jeep", "1tnk", "2tnk", "arty"),
        "Aircraft": (),
    },
    "soviet": {
        "Building": ("afld", "stek", "tsla", "ftur", "sam"),
        "Infantry": (),
        "Vehicle": ("ftrk", "v2rl", "3tnk", "4tnk"),
        "Aircraft": ("mig", "yak"),
    },
}
_DEMO_CAPABILITY_SUPPORTED_FACTIONS: tuple[str, ...] = ("soviet",)
_DEMO_CAPABILITY_ROSTER_ALL: dict[str, tuple[str, ...]] = {
    queue_type: tuple(
        dict.fromkeys(
            list(_DEMO_SHARED_CAPABILITY_ROSTER.get(queue_type, ()))
            + [
                unit_type
                for faction_roster in _DEMO_FACTION_CAPABILITY_ROSTER.values()
                for unit_type in faction_roster.get(queue_type, ())
            ]
        )
    )
    for queue_type in ("Building", "Infantry", "Vehicle", "Aircraft")
}
_DEMO_QUEUE_TYPE_BY_UNIT_TYPE: dict[str, str] = {
    unit_type: queue_type
    for queue_type, units in _DEMO_CAPABILITY_ROSTER_ALL.items()
    for unit_type in units
}

_DEMO_PROMPT_DISPLAY_NAME_OVERRIDES: dict[str, str] = {
    "powr": "电厂",
    "proc": "矿场",
    "tsla": "特斯拉塔",
    "ftur": "火焰塔",
    "sam": "防空塔",
    "ftrk": "防空履带车",
    "v2rl": "V2火箭车",
    "3tnk": "重坦",
    "4tnk": "猛犸坦克",
    "harv": "矿车",
    "mig": "MIG",
    "yak": "YAK",
}
_DEMO_QUEUE_ORDER: tuple[str, ...] = ("Building", "Infantry", "Vehicle", "Aircraft")
_DEMO_QUEUE_LABELS: dict[str, str] = {
    "Building": "建筑",
    "Infantry": "步兵",
    "Vehicle": "车辆",
    "Aircraft": "飞机",
}
_DEMO_BASE_COUNTER_FIELD_BY_UNIT_TYPE: dict[str, str] = {
    "fact": "has_construction_yard",
    "const": "has_construction_yard",
    "powr": "power_plant_count",
    "apwr": "power_plant_count",
    "barr": "barracks_count",
    "tent": "barracks_count",
    "proc": "refinery_count",
    "weap": "war_factory_count",
    "dome": "radar_count",
    "stek": "tech_center_count",
    "atek": "tech_center_count",
    "fix": "repair_facility_count",
    "afld": "airfield_count",
}
_DEMO_BROAD_PHASE_ORDER: tuple[str, ...] = ("powr", "proc", "barr", "weap")
_DEMO_MOBILE_SCOUT_UNIT_TYPE = "ftrk"
_DEMO_TRUTH_OVERRIDES: dict[str, DemoCapabilityTruth] = {
    "barr": DemoCapabilityTruth(
        unit_type="barr",
        queue_type="Building",
        display_name="兵营",
        prompt_display_name="兵营",
        prerequisites=("powr", "fact"),
        faction=None,
        in_demo_roster=True,
    ),
    "tent": DemoCapabilityTruth(
        unit_type="tent",
        queue_type=None,
        display_name="兵营",
        prompt_display_name="兵营",
        prerequisites=("powr", "fact"),
        faction="allied",
        in_demo_roster=False,
    ),
    # Shared infantry are registered twice in DATASET (Allies/Soviet).  Demo truth
    # must not inherit the last-write-wins Soviet row, otherwise Capability and
    # faction inference treat common units as Soviet-only.
    "e1": DemoCapabilityTruth(
        unit_type="e1",
        queue_type="Infantry",
        display_name="步兵",
        prompt_display_name="步兵",
        prerequisites=("barr",),
        faction=None,
        in_demo_roster=True,
    ),
    "e3": DemoCapabilityTruth(
        unit_type="e3",
        queue_type="Infantry",
        display_name="火箭兵",
        prompt_display_name="火箭兵",
        prerequisites=("barr",),
        faction=None,
        in_demo_roster=True,
    ),
    "e6": DemoCapabilityTruth(
        unit_type="e6",
        queue_type=None,
        display_name="工程师",
        prompt_display_name="工程师",
        prerequisites=("barr",),
        faction=None,
        in_demo_roster=False,
    ),
}

_DEMO_CAPABILITY_CANONICAL_ALIASES: dict[str, str] = {
    "tent": "barr",
}


def _normalize_demo_faction_name(faction: str | None) -> str | None:
    key = str(faction or "").strip().lower()
    if not key:
        return None
    if key in {"allied", "allies"}:
        return "allied"
    if key in {"soviet", "soviets"}:
        return "soviet"
    if key in {"all", "any", "union"}:
        return "all"
    return key


def _merge_demo_capability_rosters(*rosters: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    merged: dict[str, tuple[str, ...]] = {}
    for queue_type in ("Building", "Infantry", "Vehicle", "Aircraft"):
        ordered: list[str] = []
        seen: set[str] = set()
        for roster in rosters:
            for unit_type in roster.get(queue_type, ()):
                if unit_type in seen:
                    continue
                seen.add(unit_type)
                ordered.append(unit_type)
        merged[queue_type] = tuple(ordered)
    return merged


def dataset_entry(unit_type: str) -> UnitInfo | None:
    """Return the canonical dataset row for a unit/building code."""
    if not unit_type:
        return None
    return DATASET.get(unit_type.lower())


def dataset_cost_for(unit_type: str | None) -> int | None:
    """Return the canonical dataset cost for a unit/building id."""
    canonical = dataset_unit_type_for(unit_type)
    if canonical is None:
        return None
    entry = dataset_entry(canonical)
    if entry is None:
        return None
    return int(entry.cost)


def demo_capability_roster() -> dict[str, tuple[str, ...]]:
    """Return the capability-facing demo roster grouped by queue."""
    return demo_capability_roster_for_faction("soviet")


def demo_capability_roster_for_faction(faction: str | None) -> dict[str, tuple[str, ...]]:
    """Return the demo capability roster for the current faction.

    `None` falls back to the default soviet demo roster. `all` returns the
    union across supported factions for prompt surfaces that need the full
    demo vocabulary.
    """
    normalized = _normalize_demo_faction_name(faction)
    if normalized == "all":
        roster = _merge_demo_capability_rosters(
            _DEMO_SHARED_CAPABILITY_ROSTER,
            *_DEMO_FACTION_CAPABILITY_ROSTER.values(),
        )
    elif normalized in _DEMO_FACTION_CAPABILITY_ROSTER:
        roster = _merge_demo_capability_rosters(
            _DEMO_SHARED_CAPABILITY_ROSTER,
            _DEMO_FACTION_CAPABILITY_ROSTER[normalized],
        )
    else:
        roster = _merge_demo_capability_rosters(
            _DEMO_SHARED_CAPABILITY_ROSTER,
            _DEMO_FACTION_CAPABILITY_ROSTER["soviet"],
        )
    return {queue_type: units for queue_type, units in roster.items()}


def demo_capability_supported_factions() -> tuple[str, ...]:
    """Return factions with an explicit demo capability roster."""
    return _DEMO_CAPABILITY_SUPPORTED_FACTIONS


def demo_capability_queue_types() -> tuple[str, ...]:
    """Return the stable display order for demo capability queues."""
    return _DEMO_QUEUE_ORDER


def demo_capability_units_for_queue(queue_type: str) -> tuple[str, ...]:
    """Return the allowed demo roster for a queue type."""
    return demo_capability_units_for_queue_for_faction(queue_type, "soviet")


def demo_capability_units_for_queue_for_faction(queue_type: str, faction: str | None) -> tuple[str, ...]:
    """Return the allowed demo roster for a queue type and faction."""
    return demo_capability_roster_for_faction(faction).get(queue_type, ())


def demo_capability_unit_types() -> tuple[str, ...]:
    """Return the flattened set of demo capability unit/building ids."""
    return tuple(_DEMO_QUEUE_TYPE_BY_UNIT_TYPE.keys())


def demo_queue_type_for(unit_type: str) -> str | None:
    """Return the demo queue type for a unit/building id, if it is demo-supported."""
    if not unit_type:
        return None
    return _DEMO_QUEUE_TYPE_BY_UNIT_TYPE.get(str(unit_type).lower())


def queue_type_for_unit_type(unit_type: str | None) -> str:
    """Return the production queue type for a canonical unit/building id."""
    if not unit_type:
        return ""
    return _UNIT_TYPE_TO_QUEUE.get(str(unit_type).lower(), "")


def demo_base_counter_field_for(unit_type: str | None) -> str | None:
    """Return the runtime base-counter field for a canonical structure id."""
    canonical = dataset_unit_type_for(unit_type)
    if canonical is None:
        return None
    return _DEMO_BASE_COUNTER_FIELD_BY_UNIT_TYPE.get(canonical)


def demo_capability_unit_type_for(name: str | None) -> str | None:
    """Resolve an observed name to a canonical demo capability unit/building id."""
    raw = str(name or "").strip()
    if not raw:
        return None

    key = raw.lower()
    alias = _DEMO_CAPABILITY_CANONICAL_ALIASES.get(key)
    if alias:
        return alias
    if key in _DEMO_QUEUE_TYPE_BY_UNIT_TYPE:
        return key

    entry = dataset_entry(key)
    if entry is not None and entry.id.lower() in _DEMO_QUEUE_TYPE_BY_UNIT_TYPE:
        return entry.id.lower()

    # Import lazily to avoid imposing registry/prod-name startup work on callers
    # that only need the static dataset table.
    from openra_api.production_names import production_name_unit_id

    canonical = production_name_unit_id(raw)
    if canonical and canonical in _DEMO_QUEUE_TYPE_BY_UNIT_TYPE:
        return canonical
    return None


def dataset_unit_type_for(name: str | None) -> str | None:
    """Resolve an observed name to a canonical dataset unit/building id."""
    raw = str(name or "").strip()
    if not raw:
        return None

    key = raw.lower()
    entry = dataset_entry(key)
    if entry is not None:
        return entry.id.lower()

    from openra_api.production_names import production_name_unit_id

    canonical = production_name_unit_id(raw)
    if canonical:
        return canonical
    return None


def dataset_actor_category_for(name: str | None) -> str | None:
    """Return the normalized actor category used by runtime/intel layers."""
    canonical = dataset_unit_type_for(name)
    if not canonical:
        return None
    entry = dataset_entry(canonical)
    if entry is None:
        return None
    unit_id = entry.id.lower()
    if unit_id == "harv":
        return "harvester"
    if unit_id == "mcv":
        return "mcv"
    category = str(entry.category or "").lower()
    if category in {"building", "defense"}:
        return "building"
    if category == "infantry":
        return "infantry"
    if category in {"vehicle", "aircraft", "ship"}:
        return "vehicle"
    return category or None


def demo_capability_supports(name: str | None) -> bool:
    """Return True when the observed name resolves to the demo capability roster."""
    return demo_capability_unit_type_for(name) is not None


def demo_prerequisites_for(unit_type: str) -> list[str]:
    """Return normalized prerequisites for the demo capability/buildability layer."""
    truth = demo_capability_truth_for(unit_type)
    if truth is None:
        return []
    return [str(prereq).lower() for prereq in truth.prerequisites]


def demo_capability_truth_for(unit_type: str) -> DemoCapabilityTruth | None:
    """Return the normalized demo-truth view for a unit/building id.

    This accessor is the single capability-facing source for:
    - queue type
    - display name
    - prompt display name
    - prerequisites
    - faction restriction
    - whether the id belongs to the demo roster
    """
    key = str(unit_type or "").lower()
    if not key:
        return None
    override = _DEMO_TRUTH_OVERRIDES.get(key)
    if override is not None:
        return override
    entry = dataset_entry(key)
    if entry is None:
        return None
    queue_type = _DEMO_QUEUE_TYPE_BY_UNIT_TYPE.get(key)
    display_name = entry.name_cn or CN_NAME_MAP.get(key.upper(), key)
    prompt_display_name = _DEMO_PROMPT_DISPLAY_NAME_OVERRIDES.get(key) or display_name
    faction = _normalize_demo_faction_name(entry.faction)
    if faction == "both":
        faction = None
    return DemoCapabilityTruth(
        unit_type=key,
        queue_type=queue_type,
        display_name=display_name,
        prompt_display_name=prompt_display_name,
        prerequisites=tuple(str(prereq).lower() for prereq in entry.prerequisites),
        faction=faction,
        in_demo_roster=queue_type is not None,
    )


def demo_capability_roster_lines(
    *,
    queue_style: str = "compact",
    queue_types: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Return prompt-ready roster lines derived from the demo truth table.

    queue_style:
      - compact: ``- Building: powr=发电厂, ...``
      - detailed: ``建筑(queue_type=Building)：`` followed by indented ids
    """
    return _format_roster_lines(
        queue_style=queue_style,
        queue_types=queue_types or _DEMO_QUEUE_ORDER,
        display_name_for=demo_display_name_for,
        include_buildings=True,
    )


def demo_capability_broad_phase_order() -> tuple[str, ...]:
    """Return the minimum broad economy/tech progression for the demo runtime."""
    return _DEMO_BROAD_PHASE_ORDER


def demo_base_progression(
    *,
    has_construction_yard: bool,
    mcv_count: int,
    power_plant_count: int,
    refinery_count: int,
    barracks_count: int,
    war_factory_count: int,
    buildable: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Return the current demo base progression/readiness snapshot.

    This keeps top-level coordinator hints aligned with the same demo roster and
    prerequisite truth used by capability buildability.
    """
    buildable_buildings = {
        str(unit_type).lower()
        for unit_type in list((buildable or {}).get("Building", []) or [])
        if unit_type is not None
    }
    if not has_construction_yard:
        if int(mcv_count or 0) > 0:
            return {
                "phase": "deploy_mcv",
                "status": "基地车待展开",
                "missing": ["construction_yard"],
                "next_unit_type": "",
                "next_queue_type": "",
                "action_required": "deploy_mcv",
                "buildable_now": False,
            }
        return {
            "phase": "no_build_core",
            "status": "缺少建造核心",
            "missing": ["construction_yard", "mcv"],
            "next_unit_type": "",
            "next_queue_type": "",
            "action_required": "",
            "buildable_now": False,
        }

    phase_map = {
        "powr": "bootstrap_power",
        "proc": "bootstrap_economy",
        "barr": "bootstrap_production",
        "weap": "vehicle_gateway_gap",
    }
    count_map = {
        "powr": int(power_plant_count or 0),
        "proc": int(refinery_count or 0),
        "barr": int(barracks_count or 0),
        "weap": int(war_factory_count or 0),
    }
    for unit_type in demo_capability_broad_phase_order():
        if count_map.get(unit_type, 0) > 0:
            continue
        queue_type = demo_queue_type_for(unit_type) or "Building"
        buildable_now = unit_type in buildable_buildings
        display_name = demo_prompt_display_name_for(unit_type)
        return {
            "phase": phase_map.get(unit_type, "bootstrap"),
            "status": f"下一步：{display_name}" if buildable_now else f"等待能力层补前置：{display_name}",
            "missing": [unit_type],
            "next_unit_type": unit_type,
            "next_queue_type": queue_type,
            "action_required": "",
            "buildable_now": buildable_now,
        }

    return {
        "phase": "base_online",
        "status": "基地运转中",
        "missing": [],
        "next_unit_type": "",
        "next_queue_type": "",
        "action_required": "",
        "buildable_now": False,
    }


def demo_capability_buildability_snapshot(
    *,
    has_construction_yard: bool,
    mcv_count: int,
    power_plant_count: int,
    refinery_count: int,
    barracks_count: int,
    war_factory_count: int,
    radar_count: int,
    repair_facility_count: int = 0,
    tech_center_count: int = 0,
    airfield_count: int = 0,
    faction: str | None = None,
) -> dict[str, Any]:
    """Return the shared capability-facing buildability snapshot.

    This is the single demo-truth projection used by runtime/context layers for:
    - current buildable units
    - current base progression / next minimum step
    """
    owned_buildings: set[str] = set()
    if has_construction_yard:
        owned_buildings.add("fact")
    if power_plant_count > 0:
        owned_buildings.add("powr")
    if barracks_count > 0:
        owned_buildings.add("barr")
    if war_factory_count > 0:
        owned_buildings.add("weap")
    if radar_count > 0:
        owned_buildings.add("dome")
    if refinery_count > 0:
        owned_buildings.add("proc")
    if repair_facility_count > 0:
        owned_buildings.add("fix")
    if tech_center_count > 0:
        owned_buildings.add("stek")
    if airfield_count > 0:
        owned_buildings.add("afld")

    buildable: dict[str, list[str]] = {}
    for queue_type, units in demo_capability_roster_for_faction(faction).items():
        queue_buildable = [
            unit_type
            for unit_type in units
            if set(demo_prerequisites_for(unit_type)).issubset(owned_buildings)
        ]
        if queue_buildable:
            buildable[queue_type] = queue_buildable

    progression = demo_base_progression(
        has_construction_yard=has_construction_yard,
        mcv_count=mcv_count,
        power_plant_count=power_plant_count,
        refinery_count=refinery_count,
        barracks_count=barracks_count,
        war_factory_count=war_factory_count,
        buildable=buildable,
    )
    return {
        "owned_building_flags": sorted(owned_buildings),
        "buildable": buildable,
        "base_progression": progression,
    }


def demo_mobile_scout_unit_type(faction: str | None = None) -> str | None:
    """Return the demo-safe vehicle used when a cheap mobile scout is needed."""
    normalized = _normalize_demo_faction_name(faction)
    if normalized == "allied":
        unit_type = "jeep"
    else:
        unit_type = _DEMO_MOBILE_SCOUT_UNIT_TYPE
    if unit_type in _DEMO_QUEUE_TYPE_BY_UNIT_TYPE:
        return unit_type
    return None


def demo_faction_restriction_for(unit_type: str) -> str | None:
    """Return allied/soviet restriction or None when both factions can build it."""
    truth = demo_capability_truth_for(unit_type)
    if truth is not None:
        if truth.faction == "allies":
            return "allied"
        if truth.faction == "soviets":
            return "soviet"
        return truth.faction
    entry = dataset_entry(unit_type)
    if entry is None:
        return None
    faction = (entry.faction or "Both").lower()
    if faction == "both":
        return None
    if faction == "allies":
        return "allied"
    if faction == "soviets":
        return "soviet"
    return faction


def demo_faction_hint_for_unit_types(unit_types: list[str] | tuple[str, ...]) -> str | None:
    """Infer the current side from known faction-specific unit/building ids.

    Returns:
      - "allied" / "soviet" when only one side is evidenced
      - None when still ambiguous or mixed
    """
    seen: set[str] = set()
    for unit_type in unit_types:
        faction = demo_faction_restriction_for(unit_type)
        if faction:
            seen.add(faction)
    if len(seen) == 1:
        return next(iter(seen))
    return None


def demo_display_name_for(unit_type: str) -> str:
    """Return the demo-facing Chinese display name for a unit/building id."""
    entry = dataset_entry(unit_type)
    if entry is not None and entry.name_cn:
        return entry.name_cn
    if not unit_type:
        return ""
    return CN_NAME_MAP.get(str(unit_type).upper(), str(unit_type))


def demo_prompt_display_name_for(unit_type: str) -> str:
    """Return the prompt-facing display name for a demo roster id.

    Prompt wording should stay consistent with the simplified OpenRA demo the
    agents are expected to reason over, without re-hardcoding the roster in
    multiple prompt strings.
    """
    key = str(unit_type or "").lower()
    if not key:
        return ""
    return _DEMO_PROMPT_DISPLAY_NAME_OVERRIDES.get(key) or demo_display_name_for(key)


def demo_prompt_roster_lines(
    *,
    include_buildings: bool = True,
    include_prerequisites: bool = False,
    faction: str | None = "all",
) -> list[str]:
    """Return demo roster lines suitable for direct prompt injection."""
    return list(
        _format_roster_lines(
            queue_style="prompt",
            queue_types=("Building", "Infantry", "Vehicle", "Aircraft"),
            display_name_for=demo_prompt_display_name_for,
            include_buildings=include_buildings,
            include_prerequisites=include_prerequisites,
            faction=faction,
        )
    )


def filter_demo_capability_buildable(
    buildable: dict[str, list[str]],
    *,
    faction: str | None = None,
) -> dict[str, list[str]]:
    """Filter a buildable payload down to the capability-facing demo roster."""
    filtered: dict[str, list[str]] = {}
    for queue_type, allowed in demo_capability_roster_for_faction(faction).items():
        units = buildable.get(queue_type, [])
        keep = [unit for unit in units if unit in allowed]
        if keep:
            filtered[queue_type] = keep
    return filtered


def filter_demo_capability_production_queues(
    production_queues: dict[str, list[dict]],
    *,
    faction: str | None = None,
) -> dict[str, list[dict]]:
    """Filter queued production items down to the demo capability roster.

    Returned entries use canonical demo unit ids so Capability sees one stable
    vocabulary even if the runtime queue payload uses localized names.
    """
    filtered: dict[str, list[dict]] = {}
    for queue_type in _DEMO_QUEUE_ORDER:
        items = list(production_queues.get(queue_type, []) or [])
        if not items:
            continue
        keep: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            canonical = demo_capability_unit_type_for(item.get("unit_type"))
            if not canonical:
                continue
            truth = demo_capability_truth_for(canonical)
            if truth is None or truth.queue_type != queue_type:
                continue
            allowed_units = set(demo_capability_units_for_queue_for_faction(queue_type, faction))
            if canonical not in allowed_units:
                continue
            normalized = dict(item)
            normalized["unit_type"] = canonical
            keep.append(normalized)
        if keep:
            filtered[queue_type] = keep
    return filtered


def filter_demo_capability_ready_items(
    ready_items: list[dict],
    *,
    faction: str | None = None,
) -> list[dict]:
    """Filter ready queue items down to the demo capability roster."""
    filtered: list[dict] = []
    for item in ready_items or []:
        if not isinstance(item, dict):
            continue
        canonical = demo_capability_unit_type_for(item.get("unit_type") or item.get("display_name"))
        if not canonical:
            continue
        truth = demo_capability_truth_for(canonical)
        queue_type = str(item.get("queue_type", "") or "")
        if truth is None or (queue_type and truth.queue_type != queue_type):
            continue
        allowed_units = set(demo_capability_units_for_queue_for_faction(queue_type or (truth.queue_type or ""), faction))
        if canonical not in allowed_units:
            continue
        normalized = dict(item)
        normalized["unit_type"] = canonical
        normalized["display_name"] = truth.display_name
        filtered.append(normalized)
    return filtered


def filter_demo_capability_reservations(
    reservations: list[dict],
    *,
    faction: str | None = None,
) -> list[dict]:
    """Filter future-unit reservations down to the demo capability roster."""
    filtered: list[dict] = []
    for reservation in reservations or []:
        if not isinstance(reservation, dict):
            continue
        canonical = demo_capability_unit_type_for(reservation.get("unit_type"))
        if not canonical:
            continue
        truth = demo_capability_truth_for(canonical)
        if truth is None:
            continue
        queue_type = str(reservation.get("queue_type") or truth.queue_type or "")
        allowed_units = set(demo_capability_units_for_queue_for_faction(queue_type, faction))
        if canonical not in allowed_units:
            continue
        normalized = dict(reservation)
        normalized["unit_type"] = canonical
        normalized["queue_type"] = queue_type
        filtered.append(normalized)
    return filtered


def demo_capability_buildable_lines(
    buildable: dict[str, list[str]],
    *,
    faction: str | None = None,
) -> tuple[str, ...]:
    """Return prompt-ready buildable lines derived from demo capability truth."""
    lines: list[str] = []
    filtered = filter_demo_capability_buildable(buildable, faction=faction)
    for queue_type in _DEMO_QUEUE_ORDER:
        units = filtered.get(queue_type, [])
        if not units:
            continue
        entries = []
        for unit_type in units:
            entries.append(f"{unit_type}({demo_prompt_display_name_for(unit_type)})")
        lines.append(f"{queue_type}=[{','.join(entries)}]")
    return tuple(lines)


def _format_roster_lines(
    *,
    queue_style: str,
    queue_types: tuple[str, ...],
    display_name_for,
    include_buildings: bool,
    include_prerequisites: bool = False,
    faction: str | None = None,
) -> tuple[str, ...]:
    lines: list[str] = []
    roster = demo_capability_roster_for_faction(faction)
    for queue_type in queue_types:
        if queue_type == "Building" and not include_buildings:
            continue
        units = roster.get(queue_type, ())
        if not units:
            continue
        rendered_entries: list[str] = []
        for unit in units:
            entry = f"{unit}={display_name_for(unit)}"
            if include_prerequisites:
                truth = demo_capability_truth_for(unit)
                prerequisites = list(truth.prerequisites if truth is not None else ())
                if prerequisites:
                    prereq_text = " + ".join(demo_prompt_display_name_for(prereq) for prereq in prerequisites)
                    entry += f"（前置: {prereq_text}）"
            rendered_entries.append(entry)
        entries = ", ".join(rendered_entries)
        if queue_style == "detailed":
            queue_label = _DEMO_QUEUE_LABELS.get(queue_type, queue_type)
            lines.append(f"{queue_label}(queue_type={queue_type})：")
            lines.append(f"  {entries.replace(', ', '  ')}")
        elif queue_style == "prompt":
            lines.append(f"- {queue_type}：{entries.replace(', ', '，')}")
        else:
            lines.append(f"- {queue_type}: {entries}")
    return tuple(lines)
