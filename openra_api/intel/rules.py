from __future__ import annotations

from typing import Dict, Set

# 服务器/模组里可能出现的同义名称 -> 统一规范名（用于统计、价值、tech 阶段判断）
DEFAULT_NAME_ALIASES: Dict[str, str] = {
    # 单位
    "采矿车": "矿车",
    "矿车": "矿车",
    "基地车": "基地车",
    "MCV": "mcv",
    "Mcv": "mcv",
    # 建筑（常见中文别名）
    "雷达站": "雷达",
    "雷达": "雷达",
    "发电厂": "电厂",
    "电厂": "电厂",
    "核电站": "核电",
    "核电": "核电",
    "战车工厂": "车间",
    "车间": "车间",
    # 一些模组里叫“建造厂/指挥中心”，这里统一为“建造厂”（不参与 tier，但用于 building 分类）
    "建造厂": "建造厂",
}

# 默认单位类别与价值配置，可在 IntelAnalyzer 初始化时注入以支持不同阵营/平衡
DEFAULT_UNIT_CATEGORY_RULES: Dict[str, str] = {
    "矿车": "harvester",
    "矿场": "building",
    "兵营": "building",
    "车间": "building",
    "雷达": "building",
    "科技中心": "building",
    "机场": "building",
    "维修中心": "building",
    "基地车": "mcv",
    "mcv": "mcv",
    "防空车": "vehicle",
    "防空炮": "defense",
    "哨戒炮": "defense",
    "步兵": "infantry",
    "火箭兵": "infantry",
    "狗": "infantry",
    "装甲车": "vehicle",
    "重坦": "vehicle",
    "v2": "vehicle",
    "猛犸坦克": "vehicle",
    "飞机": "air",
    "直升机": "air",
    "工程师": "support",
}

DEFAULT_UNIT_VALUE_WEIGHTS: Dict[str, float] = {
    "矿车": 50,
    "矿场": 120,
    "车间": 200,
    "雷达": 160,
    "科技中心": 240,
    "机场": 180,
    "兵营": 90,
    "防空车": 120,
    "装甲车": 100,
    "重坦": 160,
    "v2": 180,
    "猛犸坦克": 240,
    "工程师": 80,
    "mcv": 260,
    "基地车": 260,
    "飞机": 200,
    "直升机": 200,
}

DEFAULT_HIGH_VALUE_TARGETS: Set[str] = {
    "矿车",
    "矿场",
    "车间",
    "雷达",
    "科技中心",
    "机场",
    "mcv",
    "基地车",
}


