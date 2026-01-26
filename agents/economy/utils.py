import os
from enum import Enum
from typing import Dict, List, Optional
from .data.dataset import DATASET, UnitInfo
from .data.combat_data import UnitCategory, CombatData

class Faction(Enum):
    Soviet = "Soviet"
    Allies = "Allies"

def get_my_faction() -> Faction:
    """
    Get current player faction from environment variable.
    Defaults to Soviet.
    """
    f_str = os.getenv("OPENRA_FACTION", "Soviet")
    if f_str.lower() == "allies":
        return Faction.Allies
    return Faction.Soviet

# Standard Build Order Items (IDs)
class UnitType:
    # Common / Base
    ConstructionYard = "FACT"
    PowerPlant = "POWR"
    AdvPowerPlant = "APWR"
    OreRefinery = "PROC"
    Barracks_Soviet = "BARR"
    Barracks_Allies = "TENT"
    WarFactory = "WEAP"
    ServiceDepot = "FIX"
    Radar_Soviet = "DOME"
    Airfield = "AFLD"
    Helipad = "HPAD"
    
    TechCenter_Soviet = "STEK"
    TechCenter_Allies = "ATEK"
    
    # Defenses
    TeslaCoil = "TSLA"
    FlameTower = "FTUR"
    SAMSite = "SAM"
    
    Pillbox = "PBOX"
    Turret = "GUN"
    AA_Gun = "AGUN"
    
    # Units
    Harvester = "HARV"
    MCV = "MCV"
    
    # Soviet Units
    RifleInfantry_Soviet = "E1"
    RocketInfantry_Soviet = "E3"
    HeavyTank = "3TNK"
    V2Rocket = "V2RL"
    MammothTank = "4TNK"
    Yak = "YAK"
    Mig = "MIG"
    Heli = "HELI"
    
    # Allied Units
    RifleInfantry_Allies = "E1"
    RocketInfantry_Allies = "E3"
    LightTank = "1TNK"
    MediumTank = "2TNK"
    Artillery = "ARTY"
    Ranger = "JEEP"
    ChronoTank = "CTNK"
    APC = "APC"
    FlakTrack = "FTRK"
    BlackHawk = "MH60"

# Import CN_NAME_MAP from dataset to ensure we have full mapping
from .data.dataset import CN_NAME_MAP
CN_NAME_TO_ID: Dict[str, str] = {}
for k, v in CN_NAME_MAP.items():
    if v not in CN_NAME_TO_ID:
        CN_NAME_TO_ID[v] = k

CN_NAME_TO_ID_BY_FACTION: Dict[str, Dict[Faction, str]] = {
    "科技中心": {Faction.Soviet: "STEK", Faction.Allies: "ATEK"},
    "兵营": {Faction.Soviet: "BARR", Faction.Allies: "TENT"},
}

def get_unit_info(unit_id: str) -> Optional[UnitInfo]:
    return DATASET.get(unit_id.upper())

def get_unit_cn_name(unit_id: str) -> str:
    """
    Get Chinese name for a unit ID.
    Priority:
    1. Check CN_NAME_MAP directly (fastest).
    2. Check UnitInfo from DATASET.
    3. Return ID itself if not found.
    """
    if unit_id.upper() in CN_NAME_MAP:
        return CN_NAME_MAP[unit_id.upper()]
        
    info = get_unit_info(unit_id)
    return info.name_cn if info else unit_id

def get_unit_category(unit_id: str) -> str:
    """
    Resolve unit category for production ratio logic.
    """
    cat, _ = CombatData.get_combat_info(unit_id)
    return cat

def normalize_unit_id(name_or_id: str, faction: Optional[Faction] = None) -> str:
    if not name_or_id or not isinstance(name_or_id, str):
        return ""
    name = name_or_id.strip()
    if not name:
        return ""
    upper = name.upper()
    if upper in DATASET:
        return upper
    if faction and name in CN_NAME_TO_ID_BY_FACTION:
        return CN_NAME_TO_ID_BY_FACTION[name].get(faction, "")
    mapped = CN_NAME_TO_ID.get(name)
    if mapped:
        return mapped
    return upper
