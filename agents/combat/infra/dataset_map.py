from typing import Dict, Optional

# Copy from agents/economy/data/dataset.py to ensure standalone independence
CN_NAME_MAP = {
    "POWR": "发电厂",
    "APWR": "核电站",
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

# Invert map for O(1) reverse lookup: "步兵" -> "e1"
# Priority: If multiple keys map to same value (e.g. BARR/TENT -> 兵营), 
# we need to ensure we pick the canonical one if possible, or just one of them.
# The code usually works with standard IDs like 'e1', '2tnk'.
# We normalize keys to lowercase for internal usage.

CN_TO_CODE_MAP: Dict[str, str] = {}
for code, name in CN_NAME_MAP.items():
    CN_TO_CODE_MAP[name] = code.lower()
    
# Manual overrides/fixes if needed (e.g. synonyms)
CN_TO_CODE_MAP["矿柱"] = "mine" 
CN_TO_CODE_MAP["油井"] = "oil_derrick"

def map_cn_to_code(cn_name: str) -> str:
    """
    Map Chinese unit name from GameAPI to English Code (e.g. '步兵' -> 'e1').
    Returns original name (lowercased) if not found.
    """
    if not cn_name:
        return "unknown"
    return CN_TO_CODE_MAP.get(cn_name, cn_name.lower())
