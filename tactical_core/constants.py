# -*- coding: utf-8 -*-
from enum import Enum

class UnitCategory(Enum):
    ARTY = "ARTY"       # V2RL: 超视距、脆皮、面伤（火炮类）
    MBT = "MBT"         # 3TNK/4TNK: 高血量、中程、中坚力量（主战坦克）
    AFV = "AFV"         # FTRK: 高机动、反轻甲（防空车、轻坦）
    INF_MEAT = "INF_MEAT" # E1: 低价值、高火力吸收（步兵炮灰）
    INF_AT = "INF_AT"     # E3: 低血量、高装甲伤害（反坦克步兵）
    DEFENSE = "DEFENSE"   # 防御性建筑（未来备用）
    OTHER = "OTHER"       # 其他战斗单位（默认归类）

# 单位分类定义（包含英文代码和中文）
# 结构: (Category, StandardCode, [Aliases])
_UNIT_DEFINITIONS = [
    # ARTY: 火炮类
    (UnitCategory.ARTY, "v2rl", ["v2rl", "V2火箭发射车"]),
    (UnitCategory.ARTY, "arty", ["arty", "榴弹炮"]),
    
    # MBT: 主战坦克
    (UnitCategory.MBT, "3tnk", ["3tnk", "重型坦克"]),
    (UnitCategory.MBT, "4tnk", ["4tnk", "超重型坦克"]),
    (UnitCategory.MBT, "2tnk", ["2tnk", "中型坦克"]),
    (UnitCategory.MBT, "ttnk", ["ttnk", "特斯拉坦克"]),
    (UnitCategory.MBT, "ctnk", ["ctnk", "超时空坦克"]),
    
    # AFV: 防空/轻型载具
    (UnitCategory.AFV, "ftrk", ["ftrk", "防空车"]),
    (UnitCategory.AFV, "1tnk", ["1tnk", "轻坦克"]),
    (UnitCategory.AFV, "jeep", ["jeep", "吉普车"]),
    (UnitCategory.AFV, "apc",  ["apc", "装甲运输车"]),
    
    # INF_MEAT: 步兵炮灰
    (UnitCategory.INF_MEAT, "e1", ["e1", "步兵"]),
    (UnitCategory.INF_MEAT, "e2", ["e2", "掷弹兵"]),
    
    # INF_AT: 反坦克步兵
    (UnitCategory.INF_AT, "e3", ["e3", "火箭兵"]),
    (UnitCategory.INF_AT, "e4", ["e4", "喷火兵"]),
    (UnitCategory.INF_AT, "shok", ["shok", "磁暴步兵"]),

    # DEFENSE: 防御性单位（未来备用）
    (UnitCategory.DEFENSE, "pbox", ["pbox", "碉堡"]),
    (UnitCategory.DEFENSE, "hbox", ["hbox", "伪装碉堡"]),
    (UnitCategory.DEFENSE, "gun", ["gun", "炮塔"]),
    (UnitCategory.DEFENSE, "tsla", ["tsla", "磁暴线圈"]),
    (UnitCategory.DEFENSE, "ftur", ["ftur", "火焰塔"]),
]

# 自动生成映射表
UNIT_CATEGORY_MAP = {}
STANDARD_NAME_MAP = {}

for category, std_code, aliases in _UNIT_DEFINITIONS:
    # 注册标准代码
    UNIT_CATEGORY_MAP[std_code] = category
    STANDARD_NAME_MAP[std_code] = std_code
    
    # 注册别名
    for alias in aliases:
        lower_alias = alias.lower()
        UNIT_CATEGORY_MAP[lower_alias] = category
        STANDARD_NAME_MAP[lower_alias] = std_code

# 必须从状态机中剔除的非战斗实体（黑名单）
IGNORED_UNIT_CODES = {
    "mpspawn",  # 出生点逻辑实体
    "camera",  # 摄像机控制点(?)
    "husk",  # 残骸
}
