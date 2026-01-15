from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class IntelModel:
    """仅承载情报数据的结构体"""

    meta: Dict[str, Any]
    economy: Dict[str, Any]
    tech: Dict[str, Any]
    forces: Dict[str, Any]
    battle: Dict[str, Any]
    opportunities: List[Dict[str, Any]]
    map_control: Dict[str, Any]
    alerts: List[str]
    legacy: Dict[str, Any]
    # 详细：所有可见 actor 正在做什么（activity/order），以及一些聚合统计
    actors_actions: Dict[str, Any] = field(default_factory=dict)


