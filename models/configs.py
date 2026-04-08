"""Expert job configuration schemas — one per Expert type."""

from dataclasses import dataclass
from typing import Optional

from .enums import EngagementMode, MoveMode


@dataclass
class ReconJobConfig:
    search_region: str  # northeast / enemy_half / full_map
    target_type: str  # base / army / expansion
    target_owner: str  # enemy
    retreat_hp_pct: float = 0.3
    avoid_combat: bool = True
    actor_ids: Optional[list[int]] = None  # explicit scout ownership when known
    scout_count: int = 1  # only used when actor_ids is None


@dataclass
class CombatJobConfig:
    target_position: tuple[int, int]
    engagement_mode: EngagementMode
    max_chase_distance: int = 20
    retreat_threshold: float = 0.3
    actor_ids: Optional[list[int]] = None  # explicit combat ownership when known
    unit_count: int = 0  # 0 = all available idle combat units; only used when actor_ids is None


@dataclass
class MovementJobConfig:
    target_position: tuple[int, int]
    move_mode: MoveMode = MoveMode.MOVE
    arrival_radius: int = 5
    actor_ids: Optional[list[int]] = None  # optional, defaults to ResourceNeed
    unit_count: int = 0  # 0 = all available; only used when actor_ids is None


@dataclass
class StopJobConfig:
    actor_ids: Optional[list[int]] = None  # explicit ownership when known
    unit_count: int = 0  # 0 = all available; only used when actor_ids is None


@dataclass
class DeployJobConfig:
    actor_id: int
    target_position: tuple[int, int]
    building_type: Optional[str] = None  # e.g. "ConstructionYard"


@dataclass
class EconomyJobConfig:
    unit_type: str
    count: int
    queue_type: str
    repeat: bool = False


# Union type for all expert configs
ExpertConfig = (
    ReconJobConfig
    | CombatJobConfig
    | MovementJobConfig
    | StopJobConfig
    | DeployJobConfig
    | EconomyJobConfig
)

# Registry: expert_type string -> expected config class
EXPERT_CONFIG_REGISTRY: dict[str, type] = {
    "ReconExpert": ReconJobConfig,
    "CombatExpert": CombatJobConfig,
    "MovementExpert": MovementJobConfig,
    "StopExpert": StopJobConfig,
    "DeployExpert": DeployJobConfig,
    "EconomyExpert": EconomyJobConfig,
}


def validate_job_config(expert_type: str, config: ExpertConfig) -> bool:
    """Validate that config type matches the expert_type."""
    expected = EXPERT_CONFIG_REGISTRY.get(expert_type)
    if expected is None:
        raise ValueError(f"Unknown expert_type: {expert_type!r}")
    if not isinstance(config, expected):
        raise TypeError(
            f"{expert_type} requires {expected.__name__}, got {type(config).__name__}"
        )
    return True
