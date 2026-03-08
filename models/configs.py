"""Expert job configuration schemas — one per Expert type."""

from dataclasses import dataclass, field
from typing import Optional

from .enums import EngagementMode, MoveMode


@dataclass
class ReconJobConfig:
    search_region: str  # northeast / enemy_half / full_map
    target_type: str  # base / army / expansion
    target_owner: str  # enemy
    retreat_hp_pct: float = 0.3
    avoid_combat: bool = True


@dataclass
class CombatJobConfig:
    target_position: tuple[int, int]
    engagement_mode: EngagementMode
    max_chase_distance: int = 20
    retreat_threshold: float = 0.3


@dataclass
class MovementJobConfig:
    target_position: tuple[int, int]
    move_mode: MoveMode = MoveMode.MOVE
    arrival_radius: int = 5
    actor_ids: Optional[list[int]] = None  # optional, defaults to ResourceNeed


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
    | DeployJobConfig
    | EconomyJobConfig
)

# Registry: expert_type string -> expected config class
EXPERT_CONFIG_REGISTRY: dict[str, type] = {
    "ReconExpert": ReconJobConfig,
    "CombatExpert": CombatJobConfig,
    "MovementExpert": MovementJobConfig,
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
