from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from ..models import MapQueryResult


@dataclass
class IntelMemory:
    """内部记忆，用于差分估计与 last-seen"""

    last_resources: Optional[float] = None
    last_time: Optional[float] = None
    prev_snapshot_time: Optional[float] = None
    last_snapshot_time: Optional[float] = None
    last_explored_ratio: Optional[float] = None
    enemy_last_seen: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    map_cache: Optional[Tuple[float, MapQueryResult]] = None
    queues_cache: Dict[str, Tuple[float, Dict[str, Any]]] = field(default_factory=dict)
    attributes_cache: Optional[Tuple[float, Dict[str, Any], Tuple[str, ...]]] = None
    scout_stalled: bool = False


