from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class UnitInfo:
    id: str
    name_cn: str
    cost: int
    power: int = 0
    prerequisites: List[str] = field(default_factory=list)
    category: str = "Unknown"
    faction: str = "Both"


CN_NAME_MAP = {
    "POWR": "发电厂",
    "APWR": "高级电厂",
    "PROC": "矿场",
    "SILO": "储存罐",
    "BARR": "兵营",
    "TENT": "兵营",
    "WEAP": "战车工厂",
    "FACT": "建造厂",
    "FIX": "维修厂",
    "SYRD": "船坞",
    "SPEN": "潜艇基地",
    "AFLD": "空军基地",
    "HPAD": "直升机坪",
    "DOME": "雷达站",
    "ATEK": "科技中心",
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
    "ARTY": "自行火炮",
    "V2RL": "V2火箭发射车",
    "1TNK": "轻坦克",
    "2TNK": "中型坦克",
    "3TNK": "重型坦克",
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

DATASET_SOVIET: Dict[str, UnitInfo] = {}
DATASET_ALLIES: Dict[str, UnitInfo] = {}

def register(unit: UnitInfo):
    # Register to specific faction dicts
    # Case insensitive keys
    uid_upper = unit.id.upper()
    uid_lower = unit.id.lower()
    
    if unit.faction == "Soviet":
        DATASET_SOVIET[uid_upper] = unit
        DATASET_SOVIET[uid_lower] = unit
    elif unit.faction == "Allies":
        DATASET_ALLIES[uid_upper] = unit
        DATASET_ALLIES[uid_lower] = unit
    else:
        # "Both" or None -> Register for both
        DATASET_SOVIET[uid_upper] = unit
        DATASET_SOVIET[uid_lower] = unit
        DATASET_ALLIES[uid_upper] = unit
        DATASET_ALLIES[uid_lower] = unit

# Backward compatibility (though we should use get_unit_info_by_faction)
# This might contain mixed data (last write wins), but we won't use it for logic.
DATASET = DATASET_SOVIET 

def get_dataset_by_faction(faction_str: str) -> Dict[str, UnitInfo]:
    if faction_str == "Allies":
        return DATASET_ALLIES
    return DATASET_SOVIET


register(UnitInfo(id="POWR", name_cn="发电厂", cost=150, power=100, category="Building", prerequisites=["fact"]))
register(UnitInfo(id="APWR", name_cn="高级电厂", cost=250, power=200, category="Building", prerequisites=["dome", "fact"]))
register(UnitInfo(id="PROC", name_cn="矿场", cost=700, power=-30, category="Building", prerequisites=["fact"]))
register(UnitInfo(id="FACT", name_cn="建造厂", cost=1000, power=0, category="Building", prerequisites=[]))
register(UnitInfo(id="WEAP", name_cn="战车工厂", cost=1000, power=-30, category="Building", prerequisites=["proc", "fact"]))
register(UnitInfo(id="FIX", name_cn="维修厂", cost=600, power=-30, category="Building", prerequisites=["weap", "fact"]))
register(UnitInfo(id="TENT", name_cn="兵营", cost=250, power=-20, category="Building", faction="Allies", prerequisites=["powr", "fact"])) # 苏盟兵营引擎返回同名
register(UnitInfo(id="DOME", name_cn="雷达站", cost=750, power=-40, category="Building", prerequisites=["proc", "fact"]))
register(UnitInfo(id="ATEK", name_cn="科技中心", cost=750, power=-200, category="Building", faction="Allies", prerequisites=["weap", "dome", "fact"]))
register(UnitInfo(id="AGUN", name_cn="防空炮", cost=400, power=-50, category="Building", faction="Allies", prerequisites=["dome", "fact"]))
register(UnitInfo(id="PBOX", name_cn="碉堡", cost=300, power=-20, category="Building", faction="Allies", prerequisites=["tent", "fact"]))
register(UnitInfo(id="GUN", name_cn="炮塔", cost=400, power=-40, category="Building", faction="Allies", prerequisites=["tent", "fact"]))
register(UnitInfo(id="HPAD", name_cn="直升机坪", cost=250, power=-10, category="Building", faction="Allies", prerequisites=["dome", "fact"]))
register(UnitInfo(id="BARR", name_cn="兵营", cost=250, power=-20, category="Building", faction="Soviet", prerequisites=["powr", "fact"]))
register(UnitInfo(id="STEK", name_cn="科技中心", cost=750, power=-100, category="Building", faction="Soviet", prerequisites=["weap", "dome", "fact"]))
register(UnitInfo(id="TSLA", name_cn="特斯拉塔", cost=600, power=-100, category="Building", faction="Soviet", prerequisites=["weap", "fact"]))
register(UnitInfo(id="FTUR", name_cn="火焰塔", cost=300, power=-20, category="Building", faction="Soviet", prerequisites=["barr", "fact"]))
register(UnitInfo(id="SAM", name_cn="防空导弹", cost=350, power=-40, category="Building", faction="Soviet", prerequisites=["dome", "fact"]))
register(UnitInfo(id="AFLD", name_cn="空军基地", cost=250, power=-20, category="Building", faction="Soviet", prerequisites=["dome", "fact"]))
register(UnitInfo(id="1TNK", name_cn="轻坦克", cost=350, category="Vehicle", faction="Allies", prerequisites=["weap"]))
register(UnitInfo(id="2TNK", name_cn="中型坦克", cost=425, category="Vehicle", faction="Allies", prerequisites=["fix", "weap"]))
register(UnitInfo(id="JEEP", name_cn="吉普车", cost=250, category="Vehicle", faction="Allies", prerequisites=["weap"]))
register(UnitInfo(id="ARTY", name_cn="自行火炮", cost=300, category="Vehicle", faction="Allies", prerequisites=["dome", "weap"]))
register(UnitInfo(id="CTNK", name_cn="超时空坦克", cost=675, category="Vehicle", faction="Allies", prerequisites=["atek", "weap"]))
register(UnitInfo(id="3TNK", name_cn="重型坦克", cost=575, category="Vehicle", faction="Soviet", prerequisites=["fix", "weap"]))
register(UnitInfo(id="4TNK", name_cn="超重型坦克", cost=1000, category="Vehicle", faction="Soviet", prerequisites=["fix", "stek", "weap"]))
register(UnitInfo(id="V2RL", name_cn="V2火箭发射车", cost=450, category="Vehicle", faction="Soviet", prerequisites=["dome", "weap"]))
register(UnitInfo(id="APC", name_cn="装甲运输车", cost=425, category="Vehicle", faction="Soviet", prerequisites=["weap"]))
register(UnitInfo(id="FTRK", name_cn="防空车", cost=300, category="Vehicle", faction="Soviet", prerequisites=["weap"]))
register(UnitInfo(id="HARV", name_cn="采矿车", cost=550, category="Vehicle", prerequisites=["proc", "weap"]))
register(UnitInfo(id="MCV", name_cn="基地车", cost=1000, category="Vehicle", prerequisites=["fix", "weap"]))
register(UnitInfo(id="E1", name_cn="步兵", cost=50, category="Infantry", faction="Allies", prerequisites=["tent"]))
register(UnitInfo(id="E3", name_cn="火箭兵", cost=150, category="Infantry", faction="Allies", prerequisites=["tent"]))
register(UnitInfo(id="E6", name_cn="工程师", cost=200, category="Infantry", faction="Allies", prerequisites=["tent"]))
register(UnitInfo(id="E1", name_cn="步兵", cost=50, category="Infantry", faction="Soviet", prerequisites=["barr"]))
register(UnitInfo(id="E3", name_cn="火箭兵", cost=150, category="Infantry", faction="Soviet", prerequisites=["barr"]))
register(UnitInfo(id="E6", name_cn="工程师", cost=200, category="Infantry", faction="Soviet", prerequisites=["barr"]))
register(UnitInfo(id="YAK", name_cn="雅克战机", cost=675, category="Aircraft", faction="Soviet", prerequisites=["afld"]))
register(UnitInfo(id="MIG", name_cn="米格战机", cost=1000, category="Aircraft", faction="Soviet", prerequisites=["afld"]))
register(UnitInfo(id="HELI", name_cn="长弓武装直升机", cost=1000, category="Aircraft", faction="Allies", prerequisites=["hpad", "atek"])) # 英文代码是否正确？
register(UnitInfo(id="MH60", name_cn="黑鹰直升机", cost=750, category="Aircraft", faction="Allies", prerequisites=["hpad"]))