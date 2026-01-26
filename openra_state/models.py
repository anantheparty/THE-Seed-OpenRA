from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class Location:
    x: int
    y: int

    def __add__(self, other):
        return Location(self.x + other.x, self.y + other.y) if isinstance(other, Location) else NotImplemented

    def __floordiv__(self, other):
        return Location(self.x // other, self.y // other) if isinstance(other, int) else NotImplemented

    def to_dict(self):
        return {"x": self.x, "y": self.y}

    def manhattan_distance(self, other):
        return abs(self.x - other.x) + abs(self.y - other.y)

    def euclidean_distance(self, other):
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5

@dataclass
class TargetsQueryParam:
    type: Optional[List[str]] = None
    faction: Optional[str] = None
    group_id: Optional[List[int]] = None
    restrain: Optional[List[dict]] = None
    location: Optional[Location] = None
    direction: Optional[str] = None
    range: Optional[str] = None

    def to_dict(self):
        return {
            "type": self.type,
            "faction": self.faction,
            "groupId": self.group_id,
            "restrain": self.restrain,
            "location": self.location.to_dict() if self.location else None,
            "direction": self.direction,
            "range": self.range,
        }

@dataclass
class Actor:
    actor_id: int
    type: Optional[str] = None
    faction: Optional[str] = None
    position: Optional[Location] = None
    hppercent: Optional[int] = None
    is_frozen: bool = False
    is_dead: bool = False
    activity: Optional[str] = None
    order: Optional[str] = None

    @property
    def id(self) -> int:
        return self.actor_id

    def __hash__(self):
        return hash(self.actor_id)

    def __eq__(self, other):
        if isinstance(other, Actor):
            return self.actor_id == other.actor_id
        return False

    def update_details(
        self,
        type: str,
        faction: str,
        position: Location,
        hppercent: int,
        is_frozen: bool = False,
        is_dead: bool = False,
        activity: Optional[str] = None,
        order: Optional[str] = None,
    ):
        self.type = type
        self.faction = faction
        self.position = position
        self.hppercent = hppercent
        self.is_frozen = is_frozen
        self.is_dead = is_dead
        self.activity = activity
        self.order = order

@dataclass
class FrozenActor:
    type: Optional[str] = None
    faction: Optional[str] = None
    position: Optional[Location] = None

@dataclass
class MapQueryResult:
    MapWidth: int
    MapHeight: int
    Height: List[List[int]]
    IsVisible: List[List[bool]]
    IsExplored: List[List[bool]]
    Terrain: List[List[str]]
    ResourcesType: List[List[str]]
    Resources: List[List[int]]

    def get_value_at_location(self, grid_name: str, location: "Location"):
        grid = getattr(self, grid_name, None)
        if grid is None:
            raise AttributeError(f"网格 '{grid_name}' 不存在。")
        if 0 <= location.x < len(grid) and 0 <= location.y < len(grid[0]):
            return grid[location.x][location.y]
        raise ValueError("位置超出范围。")

@dataclass
class PlayerBaseInfo:
    Cash: int
    Resources: int
    Power: int
    PowerDrained: int
    PowerProvided: int

@dataclass
class ScreenInfoResult:
    ScreenMin: Location
    ScreenMax: Location
    IsMouseOnScreen: bool
    MousePosition: Location

    def to_dict(self) -> Dict:
        return {
            "ScreenMin": self.ScreenMin.to_dict() if isinstance(self.ScreenMin, Location) else self.ScreenMin,
            "ScreenMax": self.ScreenMax.to_dict() if isinstance(self.ScreenMax, Location) else self.ScreenMax,
            "IsMouseOnScreen": self.IsMouseOnScreen,
            "MousePosition": self.MousePosition.to_dict() if isinstance(self.MousePosition, Location) else self.MousePosition,
        }

@dataclass
class ControlPoint:
    name: str
    x: int
    y: int
    hasBuffs: bool
    buffs: List[str]

@dataclass
class ControlPointQueryResult:
    ControlPoints: List[ControlPoint]

@dataclass
class MatchInfoQueryResult:
    SelfScore: int
    EnemyScore: int
    RemainingTime: int
