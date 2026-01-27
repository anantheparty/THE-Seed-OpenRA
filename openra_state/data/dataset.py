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

DATASET: Dict[str, UnitInfo] = {}


def register(unit: UnitInfo):
    DATASET[unit.id.upper()] = unit
    DATASET[unit.id.lower()] = unit

# ==========================================
# STRUCTURES
# ==========================================

# Common
register(UnitInfo(id="POWR", name_cn="发电厂", cost=150, power=100, category="Building", prerequisites=["fact"])) 
register(UnitInfo(id="APWR", name_cn="高级电厂", cost=250, power=200, category="Building", prerequisites=["dome", "fact"]))
register(UnitInfo(id="PROC", name_cn="矿场", cost=700, power=-30, category="Building", prerequisites=["fact"])) 
# register(UnitInfo(id="SILO", name_cn="储存罐", cost=75, power=-10, category="Building", prerequisites=["proc", "fact"]))
register(UnitInfo(id="FACT", name_cn="建造厂", cost=1000, power=0, category="Building", prerequisites=[]))
register(UnitInfo(id="WEAP", name_cn="战车工厂", cost=1000, power=-30, category="Building", prerequisites=["proc", "fact"])) 
register(UnitInfo(id="FIX", name_cn="维修厂", cost=600, power=-30, category="Building", prerequisites=["weap", "fact"])) 

# Allies
register(UnitInfo(id="TENT", name_cn="兵营", cost=250, power=-20, category="Building", faction="Allies", prerequisites=["powr", "fact"]))
register(UnitInfo(id="DOME", name_cn="雷达站", cost=750, power=-40, category="Building", prerequisites=["proc", "fact"]))
register(UnitInfo(id="ATEK", name_cn="科技中心", cost=750, power=-200, category="Building", faction="Allies", prerequisites=["weap", "dome", "fact"]))
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
# register(UnitInfo(id="MSLO", name_cn="核弹发射井", cost=1250, power=-150, category="Building", prerequisites=["stek", "atek", "fact"])) # 核弹苏盟共用，但是这么写有问题
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
register(UnitInfo(id="ARTY", name_cn="自行火炮", cost=300, category="Vehicle", faction="Allies", prerequisites=["dome", "weap"])) 
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

# Allies 这里苏盟都只保留了E1/E3/E6这三个单位，但是现在这么分开会不会有问题？盟军兵营是tent。
register(UnitInfo(id="E1", name_cn="盟军步枪兵", cost=50, category="Infantry", faction="Allies", prerequisites=["tent"]))
register(UnitInfo(id="E3", name_cn="盟军火箭兵", cost=150, category="Infantry", faction="Allies", prerequisites=["tent"]))
register(UnitInfo(id="E6", name_cn="盟军工程师", cost=200, category="Infantry", faction="Allies", prerequisites=["tent"]))
# register(UnitInfo(id="E7", name_cn="谭雅", cost=750, category="Infantry", faction="Allies", prerequisites=["atek", "tent"]))
# register(UnitInfo(id="MEDIC", name_cn="医疗兵", cost=100, category="Infantry", faction="Allies", prerequisites=["tent"]))
# register(UnitInfo(id="MECH", name_cn="机械师", cost=250, category="Infantry", faction="Allies", prerequisites=["fix", "tent"]))
# register(UnitInfo(id="SPY", name_cn="间谍", cost=250, category="Infantry", faction="Allies", prerequisites=["dome", "tent"]))
# register(UnitInfo(id="THIEF", name_cn="小偷", cost=250, category="Infantry", faction="Allies", prerequisites=["tech", "tent"])) 

# Soviet 苏军兵营是barr。
register(UnitInfo(id="E1", name_cn="苏军步枪兵", cost=50, category="Infantry", faction="Soviet", prerequisites=["barr"]))
# register(UnitInfo(id="E2", name_cn="掷弹兵", cost=80, category="Infantry", faction="Soviet", prerequisites=["barr"]))
register(UnitInfo(id="E3", name_cn="苏军火箭兵", cost=150, category="Infantry", faction="Soviet", prerequisites=["barr"]))
# register(UnitInfo(id="E4", name_cn="喷火兵", cost=150, category="Infantry", faction="Soviet", prerequisites=["ftur", "barr"]))
register(UnitInfo(id="E6", name_cn="苏军工程师", cost=200, category="Infantry", faction="Soviet", prerequisites=["barr"]))
# register(UnitInfo(id="DOG", name_cn="军犬", cost=100, category="Infantry", faction="Soviet", prerequisites=["kenn", "barr"]))
# register(UnitInfo(id="SHOK", name_cn="磁暴步兵", cost=175, category="Infantry", faction="Soviet", prerequisites=["stek", "tsla", "barr"]))

# ==========================================
# AIRCRAFT
# ==========================================

register(UnitInfo(id="YAK", name_cn="雅克战机", cost=675, category="Aircraft", faction="Soviet", prerequisites=["afld"]))
register(UnitInfo(id="MIG", name_cn="米格战机", cost=1000, category="Aircraft", faction="Soviet", prerequisites=["afld"]))
# register(UnitInfo(id="HIND", name_cn="雌鹿直升机", cost=600, category="Aircraft", faction="Soviet", prerequisites=["afld"]))
register(UnitInfo(id="HELI", name_cn="长弓武装直升机", cost=1000, category="Aircraft", faction="Allies", prerequisites=["hpad", "atek"])) # 盟军飞机数据有问题
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