"""WorldModel v1: unified queries, layered refresh, and event detection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
import logging
import math
import time
from typing import Any, Optional, Protocol

from benchmark import timed
from logging_system import get_logger
from models import (
    ActorCategory,
    ActorOwner,
    Constraint,
    Event,
    EventType,
    Mobility,
    NormalizedActor,
)
from openra_api.game_api import GameAPI
from openra_api.intel.names import normalize_unit_name
from openra_api.intel.rules import DEFAULT_UNIT_CATEGORY_RULES, DEFAULT_UNIT_VALUE_WEIGHTS
from openra_api.models import Actor, FrozenActor, Location, MapQueryResult, PlayerBaseInfo, TargetsQueryParam
from openra_api.production_names import production_name_matches, production_name_entry, production_name_unit_id
from openra_state.data.dataset import (
    dataset_actor_category_for,
    dataset_cost_for,
    demo_base_counter_field_for,
    demo_base_progression,
    demo_capability_buildability_snapshot,
    filter_demo_capability_ready_items,
    demo_capability_units_for_queue,
    demo_display_name_for,
    demo_faction_hint_for_unit_types,
    demo_capability_queue_types,
    demo_prerequisites_for,
    demo_queue_type_for,
)
from runtime_views import (
    CapabilityStatusSnapshot,
    build_battlefield_snapshot,
    build_runtime_state_snapshot,
)
from task_triage import build_runtime_unit_pipeline_preview
from unit_registry import UnitRegistry, get_default_registry


QUEUE_TYPES = ("Building", "Defense", "Infantry", "Vehicle", "Aircraft")
QUEUE_PRODUCER_UNIT_IDS: dict[str, tuple[str, ...]] = {
    "Building": ("fact", "const"),
    "Defense": ("fact", "const"),
    "Infantry": ("barr", "tent"),
    "Vehicle": ("weap",),
    "Aircraft": ("afld",),
}

DEFENSIVE_BUILDING_NAMES = {"防空炮", "哨戒炮", "sam", "agun", "gun", "hbox", "pbox", "tsla", "ftur"}
FAST_NAMES = {"dog", "吉普车", "jeep", "bike", "矿车"}
SLOW_NAMES = {"猛犸坦克", "mamm", "v2", "v2rl"}
BASE_ATTACK_MIN_DAMAGE_PCT = 5
BASE_ATTACK_NEARBY_ENEMY_RADIUS = 200
REFRESH_FAILURE_LOG_COOLDOWN_S = 2.0
SLOW_REFRESH_LOG_COOLDOWN_S = 10.0
CONNECTION_FAILURE_RETRY_BACKOFF_S = 2.0

logger = logging.getLogger(__name__)
slog = get_logger("world_model")


class WorldModelSource(Protocol):
    """Fetches raw game state for the WorldModel."""

    def fetch_self_actors(self) -> list[Actor]:
        ...

    def fetch_enemy_actors(self) -> list[Actor]:
        ...

    def fetch_frozen_enemies(self) -> list[FrozenActor]:
        ...

    def fetch_economy(self) -> Optional[PlayerBaseInfo]:
        ...

    def fetch_map(self, fields: list[str] | None = None) -> Optional[MapQueryResult]:
        ...

    def fetch_production_queues(self) -> dict[str, dict[str, Any]]:
        ...


@dataclass(slots=True)
class RefreshPolicy:
    actors_s: float = 0.1
    economy_s: float = 0.5
    map_s: float = 5.0


@dataclass(slots=True)
class WorldState:
    actors: dict[int, NormalizedActor] = field(default_factory=dict)
    self_ids: set[int] = field(default_factory=set)
    enemy_ids: set[int] = field(default_factory=set)
    frozen_enemies: list[dict[str, Any]] = field(default_factory=list)  # last-seen enemy positions in fog
    economy: dict[str, Any] = field(default_factory=dict)
    map_info: dict[str, Any] = field(default_factory=dict)
    production_queues: dict[str, dict[str, Any]] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    stale: bool = False


class GameAPIWorldSource:
    """Real source adapter backed by the existing OpenRA GameAPI."""

    def __init__(self, api: GameAPI) -> None:
        self.api = api

    def fetch_self_actors(self) -> list[Actor]:
        return self.api.query_actor(TargetsQueryParam(faction="自己"))

    def fetch_enemy_actors(self) -> list[Actor]:
        return self.api.query_actor(TargetsQueryParam(faction="敌人"))

    def fetch_frozen_enemies(self) -> list[FrozenActor]:
        try:
            _, frozen = self.api.query_actorwithfrozen(TargetsQueryParam(faction="敌人"))
            return frozen or []
        except Exception:
            return []

    def fetch_economy(self) -> Optional[PlayerBaseInfo]:
        return self.api.player_base_info_query()

    def fetch_map(self, fields: list[str] | None = None) -> Optional[MapQueryResult]:
        return self.api.map_query(fields=fields)

    def fetch_production_queues(self) -> dict[str, dict[str, Any]]:
        queues: dict[str, dict[str, Any]] = {}
        for queue_type in QUEUE_TYPES:
            raw = self.api.query_production_queue(queue_type)
            queues[queue_type] = {
                "queue_type": raw.get("queue_type", queue_type),
                "items": [
                    {
                        "name": item.get("name"),
                        "display_name": item.get("chineseName"),
                        "progress": item.get("progress_percent"),
                        "status": item.get("status"),
                        "paused": item.get("paused"),
                        "owner_actor_id": item.get("owner_actor_id"),
                        "remaining_time": item.get("remaining_time"),
                        "total_time": item.get("total_time"),
                        "done": item.get("done"),
                    }
                    for item in raw.get("queue_items", [])
                ],
                "has_ready_item": raw.get("has_ready_item", False),
            }
        return queues


class WorldModel:
    """Shared world state plus Information-Expert style analysis."""

    def __init__(
        self,
        source: WorldModelSource,
        *,
        refresh_policy: Optional[RefreshPolicy] = None,
        event_history_limit: int = 200,
        stale_failure_threshold: int = 3,
        unit_registry: Optional[UnitRegistry] = None,
    ) -> None:
        self.source = source
        self.refresh_policy = refresh_policy or RefreshPolicy()
        self.event_history_limit = event_history_limit
        self.stale_failure_threshold = stale_failure_threshold
        self.unit_registry = unit_registry or get_default_registry()

        self.state = WorldState(timestamp=0.0)
        self.active_tasks: dict[str, Any] = {}
        self.active_jobs: dict[str, Any] = {}
        self.resource_bindings: dict[str, str] = {}
        self.constraints: dict[str, Constraint] = {}
        # Per-task job stats (includes terminal jobs): {task_id: {"failed_count": int, "expert_attempts": {type: count}}}
        self._job_stats_by_task: dict[str, dict[str, Any]] = {}
        self._unfulfilled_requests: list[dict[str, Any]] = []
        self._capability_state = CapabilityStatusSnapshot()
        self._unit_reservations: list[dict[str, Any]] = []

        self._info_experts: list[Any] = []

        self._last_actor_refresh = 0.0
        self._last_economy_refresh = 0.0
        self._last_map_refresh = 0.0
        self._map_static_fetched = False
        self._pending_events: list[Event] = []
        self._event_history: list[Event] = []
        self._last_refresh_layers: list[str] = []
        self._frontline_weak_active = False
        self._economy_surplus_active = False
        self._low_power_active = False
        self._consecutive_refresh_failures = 0
        self._total_refresh_failures = 0
        self._last_refresh_error: Optional[str] = None
        self._last_refresh_disconnected = False
        self._layer_retry_after: dict[str, float] = {"actors": 0.0, "economy": 0.0, "map": 0.0}
        self._refresh_failure_log_state: dict[str, dict[str, Any]] = {}
        self._slow_refresh_log_state: dict[str, Any] = {"last_log_at": 0.0, "suppressed_count": 0}

    @timed("world_refresh")
    def refresh(self, *, now: Optional[float] = None, force: bool = False) -> list[Event]:
        timestamp = now if now is not None else time.time()
        layers = self._due_layers(timestamp, force=force)
        if not layers and self.state.timestamp:
            self._pending_events = []
            self._last_refresh_layers = []
            return []
        slog.debug("WorldModel refresh started", event="world_refresh_started", force=force, layers=layers, timestamp=timestamp)

        previous = WorldState(
            actors=dict(self.state.actors),
            self_ids=set(self.state.self_ids),
            enemy_ids=set(self.state.enemy_ids),
            economy=dict(self.state.economy),
            map_info=dict(self.state.map_info),
            production_queues={key: dict(value) for key, value in self.state.production_queues.items()},
            timestamp=self.state.timestamp,
            stale=self.state.stale,
        )

        stale = False
        refresh_errors: list[str] = []
        layer_timings: dict[str, float] = {}
        connection_failure_active = False
        if "actors" in layers:
            t0 = time.time()
            try:
                self_actors = self.source.fetch_self_actors()
                enemy_actors = self.source.fetch_enemy_actors()
                normalized = self._normalize_actors(self_actors, enemy_actors, timestamp)
                self.state.actors = normalized["actors"]
                self.state.self_ids = normalized["self_ids"]
                self.state.enemy_ids = normalized["enemy_ids"]
                # Fetch frozen enemies (last-seen positions in fog-of-war)
                try:
                    frozen_raw = self.source.fetch_frozen_enemies()
                    visible_positions = {
                        (a.position[0], a.position[1])
                        for a in self.state.actors.values()
                        if a.owner == ActorOwner.ENEMY and a.position
                    }
                    self.state.frozen_enemies = [
                        {
                            "type": getattr(f, "type", None),
                            "faction": getattr(f, "faction", None),
                            "position": [f.position.x, f.position.y] if f.position else None,
                        }
                        for f in frozen_raw
                        if f.position and (f.position.x, f.position.y) not in visible_positions
                    ]
                except Exception:
                    pass  # frozen fetch is best-effort
                self._last_actor_refresh = timestamp
                self._layer_retry_after["actors"] = 0.0
                self._clear_refresh_failure_log_state("actors")
            except Exception as exc:
                stale = True
                refresh_errors.append(f"actors:{exc}")
                self._mark_layer_retry_backoff("actors", timestamp)
                self._log_refresh_failure("actors", exc, timestamp)
                connection_failure_active = self._is_connection_failure(exc)
            layer_timings["actors"] = (time.time() - t0) * 1000

        if "economy" in layers:
            t0 = time.time()
            if connection_failure_active:
                stale = True
                self._mark_layer_retry_backoff("economy", timestamp)
            else:
                try:
                    economy = self._normalize_economy(self.source.fetch_economy(), timestamp)
                    queues = self._normalize_queues(self.source.fetch_production_queues(), timestamp)
                    self.state.economy = economy
                    self.state.production_queues = queues
                    self._last_economy_refresh = timestamp
                    self._layer_retry_after["economy"] = 0.0
                    self._clear_refresh_failure_log_state("economy")
                except Exception as exc:
                    stale = True
                    refresh_errors.append(f"economy:{exc}")
                    self._mark_layer_retry_backoff("economy", timestamp)
                    self._log_refresh_failure("economy", exc, timestamp)
                    connection_failure_active = self._is_connection_failure(exc)
            layer_timings["economy"] = (time.time() - t0) * 1000

        if "map" in layers:
            t0 = time.time()
            if connection_failure_active:
                stale = True
                self._mark_layer_retry_backoff("map", timestamp)
            else:
                try:
                    # First fetch: full data (for static caching). Subsequent: lightweight.
                    if self._map_static_fetched:
                        map_fields = ["IsExplored_packed", "MapWidth", "MapHeight"]
                    else:
                        map_fields = None  # full fetch
                    map_result = self.source.fetch_map(fields=map_fields)
                    self.state.map_info = self._normalize_map(map_result, timestamp)
                    self._map_static_fetched = True
                    self._last_map_refresh = timestamp
                    self._layer_retry_after["map"] = 0.0
                    self._clear_refresh_failure_log_state("map")
                except Exception as exc:
                    stale = True
                    refresh_errors.append(f"map:{exc}")
                    self._mark_layer_retry_backoff("map", timestamp)
                    self._log_refresh_failure("map", exc, timestamp)
                    connection_failure_active = self._is_connection_failure(exc)
            layer_timings["map"] = (time.time() - t0) * 1000

        # Log slow refreshes for diagnostics (T-R5-5).
        total_ms = sum(layer_timings.values())
        if total_ms > 100:
            self._log_slow_refresh(total_ms, layer_timings, len(self.state.actors), timestamp)

        if stale:
            self._consecutive_refresh_failures += 1
            self._total_refresh_failures += 1
            self._last_refresh_error = "; ".join(refresh_errors) if refresh_errors else "unknown refresh failure"
            self._last_refresh_disconnected = connection_failure_active
        else:
            self._consecutive_refresh_failures = 0
            self._last_refresh_error = None
            self._last_refresh_disconnected = False

        self.state.timestamp = timestamp
        self.state.stale = stale
        events = self._detect_events(previous, self.state, timestamp)
        self._pending_events = list(events)
        self._event_history.extend(events)
        if len(self._event_history) > self.event_history_limit:
            self._event_history = self._event_history[-self.event_history_limit :]
        self._last_refresh_layers = layers
        slog.debug(
            "WorldModel refresh completed",
            event="world_refresh_completed",
            layers=layers,
            stale=stale,
            disconnected=self._last_refresh_disconnected,
            event_count=len(events),
            timestamp=timestamp,
            consecutive_failures=self._consecutive_refresh_failures,
            failure_threshold=self.stale_failure_threshold,
        )
        return list(events)

    def detect_events(self, *, clear: bool = True) -> list[Event]:
        events = list(self._pending_events)
        if clear:
            self._pending_events = []
        return events

    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any:
        params = params or {}
        if query_type == "world_summary":
            return self.world_summary()
        if query_type in {"actors", "my_actors", "enemy_actors", "find_actors"}:
            owner = params.get("owner")
            if query_type == "my_actors":
                owner = ActorOwner.SELF.value
            elif query_type == "enemy_actors":
                owner = ActorOwner.ENEMY.value
            actors = self.find_actors(
                owner=owner,
                category=params.get("category"),
                idle_only=params.get("idle_only", False),
                actor_ids=params.get("actor_ids"),
                unbound_only=params.get("unbound_only", False),
                can_attack=params.get("can_attack"),
                can_harvest=params.get("can_harvest"),
                name=params.get("name") or params.get("type"),
                near=params.get("near"),
                max_distance=params.get("max_distance"),
            )
            return {"actors": [self._actor_to_dict(actor) for actor in actors], "timestamp": self.state.timestamp}
        if query_type == "actor_by_id":
            actor_id = params["actor_id"]
            actor = self.state.actors.get(actor_id)
            return {"actor": self._actor_to_dict(actor) if actor else None, "timestamp": self.state.timestamp}
        if query_type == "economy":
            return dict(self.state.economy)
        if query_type == "map":
            return {k: v for k, v in self.state.map_info.items() if k != "is_explored"}
        if query_type == "map_raw":
            return dict(self.state.map_info)
        if query_type == "production_queues":
            return {name: dict(queue) for name, queue in self.state.production_queues.items()}
        if query_type == "resource_bindings":
            return {"resource_bindings": dict(self.resource_bindings), "timestamp": self.state.timestamp}
        if query_type == "constraints":
            return {
                "constraints": [self._constraint_to_dict(item) for item in self.constraints.values()],
                "timestamp": self.state.timestamp,
            }
        if query_type == "runtime_state":
            return self.runtime_state()
        if query_type == "battlefield_snapshot":
            return self.battlefield_snapshot()
        if query_type == "capability_status":
            return self._capability_state.to_dict()
        if query_type == "events":
            limit = params.get("limit")
            events = self._event_history[-limit:] if limit else self._event_history
            return {"events": [self._event_to_dict(event) for event in events], "timestamp": self.state.timestamp}
        raise ValueError(f"Unsupported query_type: {query_type}")

    def find_actors(
        self,
        *,
        owner: Optional[str] = None,
        category: Optional[str] = None,
        idle_only: bool = False,
        actor_ids: Optional[Sequence[int]] = None,
        unbound_only: bool = False,
        can_attack: Optional[bool] = None,
        can_harvest: Optional[bool] = None,
        name: Optional[str] = None,
        near: Optional[tuple[int, int]] = None,
        max_distance: Optional[float] = None,
        mobility: Optional[str] = None,
    ) -> list[NormalizedActor]:
        requested_ids = set(actor_ids or [])
        matched: list[NormalizedActor] = []
        for actor in self.state.actors.values():
            if owner and actor.owner.value != owner:
                continue
            if category and actor.category.value != category:
                continue
            if idle_only and not actor.is_idle:
                continue
            if requested_ids and actor.actor_id not in requested_ids:
                continue
            if unbound_only and f"actor:{actor.actor_id}" in self.resource_bindings:
                continue
            if can_attack is not None and actor.can_attack != can_attack:
                continue
            if can_harvest is not None and actor.can_harvest != can_harvest:
                continue
            if name and not production_name_matches(name, actor.name, actor.display_name):
                continue
            if near is not None and max_distance is not None:
                if self._distance(actor.position, near) > max_distance:
                    continue
            if mobility is not None and actor.mobility.value != mobility:
                continue
            matched.append(actor)
        matched.sort(key=lambda item: item.actor_id)
        return matched

    def world_summary(self) -> dict[str, Any]:
        queue_block_state = self._queue_block_state()
        structure_power_state = self._self_structure_power_state()
        self_combat = sum(
            actor.combat_value
            for actor in self.state.actors.values()
            if actor.owner == ActorOwner.SELF and actor.can_attack
        )
        enemy_combat = sum(
            actor.combat_value
            for actor in self.state.actors.values()
            if actor.owner == ActorOwner.ENEMY and actor.can_attack
        )
        summary = {
            "economy": {
                **self.state.economy,
                "queue_blocked": bool(queue_block_state.get("blocked")),
                "queue_blocked_reason": queue_block_state.get("reason", ""),
                "queue_blocked_reasons": list(queue_block_state.get("reasons", [])),
                "queue_blocked_queue_types": list(queue_block_state.get("queue_types", [])),
                "queue_blocked_items": [dict(item) for item in list(queue_block_state.get("items", []))],
                "disabled_structure_count": structure_power_state["disabled_structure_count"],
                "powered_down_structure_count": structure_power_state["powered_down_structure_count"],
                "low_power_disabled_structure_count": structure_power_state["low_power_disabled_structure_count"],
                "power_outage_structure_count": structure_power_state["power_outage_structure_count"],
                "disabled_structures": list(structure_power_state["disabled_structures"]),
            },
            "military": {
                "self_units": len(self.state.self_ids),
                "enemy_units": len(self.state.enemy_ids),
                "self_combat_value": round(self_combat, 2),
                "enemy_combat_value": round(enemy_combat, 2),
                "idle_self_units": len(
                    [
                        actor
                        for actor in self.state.actors.values()
                        if actor.owner == ActorOwner.SELF and actor.is_idle
                    ]
                ),
                "bound_resources": len(self.resource_bindings),
            },
            "map": {k: v for k, v in self.state.map_info.items() if k != "is_explored"},
            "known_enemy": {
                "units_spotted": len(self.state.enemy_ids),
                "structures": len(
                    [
                        actor
                        for actor in self.state.actors.values()
                        if actor.owner == ActorOwner.ENEMY and actor.category == ActorCategory.BUILDING
                    ]
                ),
                "bases": len(
                    [
                        actor
                        for actor in self.state.actors.values()
                        if actor.owner == ActorOwner.ENEMY and actor.category in {ActorCategory.BUILDING, ActorCategory.MCV}
                    ]
                ),
                "combat_value": round(enemy_combat, 2),
                "frozen_count": len(self.state.frozen_enemies),
                "frozen_positions": [
                    {"type": f["type"], "position": f["position"]}
                    for f in self.state.frozen_enemies
                    if f.get("position")
                ],
            },
            "timestamp": self.state.timestamp,
            "stale": self.state.stale,
            "disconnected": self._last_refresh_disconnected,
            "last_refresh_error": self._last_refresh_error,
            "consecutive_refresh_failures": self._consecutive_refresh_failures,
            "total_refresh_failures": self._total_refresh_failures,
            "failure_threshold": self.stale_failure_threshold,
        }
        return summary

    def runtime_state(self) -> dict[str, Any]:
        return build_runtime_state_snapshot(
            active_tasks=dict(self.active_tasks),
            active_jobs=dict(self.active_jobs),
            resource_bindings=dict(self.resource_bindings),
            constraints=[self._constraint_to_dict(item) for item in self.constraints.values()],
            unfulfilled_requests=list(self._unfulfilled_requests),
            capability_status=self._capability_state,
            unit_reservations=list(self._unit_reservations),
            timestamp=self.state.timestamp,
        ).to_dict()

    def battlefield_snapshot(self) -> dict[str, Any]:
        summary = self.world_summary()
        economy = summary.get("economy", {})
        military = summary.get("military", {})
        game_map = summary.get("map", {})
        known_enemy = summary.get("known_enemy", {})
        capability = self._capability_state
        runtime_facts = self.compute_runtime_facts("__battlefield__", include_buildable=False)
        info_experts = dict(runtime_facts.get("info_experts") or {})

        self_units = int(military.get("self_units", 0) or 0)
        enemy_units = int(military.get("enemy_units", 0) or 0)
        self_score = float(military.get("self_combat_value", 0) or 0)
        enemy_score = float(military.get("enemy_combat_value", 0) or 0)
        low_power = bool(economy.get("low_power"))
        queue_blocked = bool(economy.get("queue_blocked"))
        queue_blocked_reason = str(economy.get("queue_blocked_reason", "") or "")
        queue_blocked_queue_types = [str(item) for item in list(economy.get("queue_blocked_queue_types", []) or []) if item]
        queue_blocked_items = [dict(item) for item in list(economy.get("queue_blocked_items", []) or []) if isinstance(item, dict)]
        disabled_structure_count = int(economy.get("disabled_structure_count", 0) or 0)
        powered_down_structure_count = int(economy.get("powered_down_structure_count", 0) or 0)
        low_power_disabled_structure_count = int(economy.get("low_power_disabled_structure_count", 0) or 0)
        power_outage_structure_count = int(economy.get("power_outage_structure_count", 0) or 0)
        disabled_structures = [str(item) for item in list(economy.get("disabled_structures", []) or []) if item]
        pending_requests = capability.pending_request_count
        bootstrapping_request_count = capability.bootstrapping_request_count
        reservation_count = len(self._unit_reservations)
        explored_pct = game_map.get("explored_pct")
        enemy_bases = int(known_enemy.get("bases", 0) or 0)
        enemy_spotted = int(known_enemy.get("units_spotted", 0) or 0)
        frozen_count = int(known_enemy.get("frozen_count", 0) or 0)
        threat_level = str(info_experts.get("threat_level") or "unknown")
        threat_direction = str(info_experts.get("threat_direction") or "unknown")
        base_under_attack = bool(info_experts.get("base_under_attack"))
        has_production = bool(info_experts.get("has_production"))
        base_health_summary = str(info_experts.get("base_health_summary") or "")
        self_combat_actor_ids = {
            int(actor.actor_id)
            for actor in self.state.actors.values()
            if actor.owner == ActorOwner.SELF and actor.can_attack
        }
        committed_actor_ids = {
            int(actor_id)
            for runtime_task in self.active_tasks.values()
            for actor_id in list(runtime_task.get("active_actor_ids", []) or [])
            if actor_id is not None
        }
        committed_combat_actor_ids = self_combat_actor_ids & committed_actor_ids
        self_combat_units = len(self_combat_actor_ids)
        committed_combat_units = len(committed_combat_actor_ids)
        free_combat_units = max(0, self_combat_units - committed_combat_units)

        if enemy_score >= max(self_score * 1.2, self_score + 1):
            disposition = "under_pressure"
        elif self_score > 0 and self_score >= max(enemy_score * 1.2, enemy_score + 1):
            disposition = "advantage"
        elif low_power or queue_blocked:
            disposition = "stalled"
        else:
            disposition = "stable"

        if disposition == "under_pressure":
            focus = "defense"
        elif low_power or queue_blocked or pending_requests:
            focus = "economy"
        elif enemy_bases or enemy_spotted or frozen_count:
            focus = "recon"
        else:
            focus = "general"

        if low_power:
            recommended_posture = "stabilize_power"
        elif queue_blocked:
            recommended_posture = "unblock_queue"
        elif pending_requests or reservation_count:
            recommended_posture = "satisfy_requests"
        elif base_under_attack or disposition == "under_pressure":
            recommended_posture = "defend_base"
        elif not enemy_bases and not enemy_spotted and frozen_count <= 0:
            recommended_posture = "expand_recon"
        elif disposition == "advantage":
            recommended_posture = "press_advantage"
        else:
            recommended_posture = "maintain_posture"

        unit_pipeline_preview = build_runtime_unit_pipeline_preview(
            {
                "unfulfilled_requests": self._unfulfilled_requests,
                "unit_reservations": self._unit_reservations,
            }
        )
        summary_text = f"我方{self_units} / 敌方{enemy_units}，战斗值{self_score:.0f}/{enemy_score:.0f}"
        if explored_pct is not None:
            summary_text += f"，探索{float(explored_pct) * 100:.1f}%"
        if low_power:
            summary_text += "，低电"
        if queue_blocked:
            summary_text += "，队列阻塞"
            if queue_blocked_items:
                preview = "、".join(
                    str(item.get("display_name") or item.get("unit_type") or "?")
                    for item in queue_blocked_items[:2]
                )
                summary_text += f"({preview})"
        if disabled_structure_count:
            summary_text += f"，离线建筑{disabled_structure_count}"
        if pending_requests:
            summary_text += f"，待处理请求{pending_requests}"
        if reservation_count:
            summary_text += f"，预留{reservation_count}"
        if unit_pipeline_preview:
            summary_text += f"，在途{unit_pipeline_preview}"
        if self_combat_units:
            summary_text += f"，可自由调度战斗单位{free_combat_units}/{self_combat_units}"

        snapshot = build_battlefield_snapshot(
            summary=summary_text,
            disposition=disposition,
            focus=focus,
            self_units=self_units,
            enemy_units=enemy_units,
            self_combat_value=self_score,
            enemy_combat_value=enemy_score,
            idle_self_units=int(military.get("idle_self_units", 0) or 0),
            self_combat_units=self_combat_units,
            committed_combat_units=committed_combat_units,
            free_combat_units=free_combat_units,
            low_power=low_power,
            queue_blocked=queue_blocked,
            queue_blocked_reason=queue_blocked_reason,
            queue_blocked_queue_types=queue_blocked_queue_types,
            queue_blocked_items=queue_blocked_items,
            disabled_structure_count=disabled_structure_count,
            powered_down_structure_count=powered_down_structure_count,
            low_power_disabled_structure_count=low_power_disabled_structure_count,
            power_outage_structure_count=power_outage_structure_count,
            disabled_structures=disabled_structures,
            recommended_posture=recommended_posture,
            threat_level=threat_level,
            threat_direction=threat_direction,
            base_under_attack=base_under_attack,
            base_health_summary=base_health_summary,
            has_production=has_production,
            explored_pct=explored_pct,
            enemy_bases=enemy_bases,
            enemy_spotted=enemy_spotted,
            frozen_enemy_count=frozen_count,
            pending_request_count=pending_requests,
            bootstrapping_request_count=bootstrapping_request_count,
            reservation_count=reservation_count,
            unit_pipeline_preview=unit_pipeline_preview,
            stale=self.state.stale,
            capability_status=capability.to_dict(),
        ).to_dict()
        snapshot["timestamp"] = self.state.timestamp
        return snapshot

    def set_runtime_state(
        self,
        *,
        active_tasks: Optional[dict[str, Any]] = None,
        active_jobs: Optional[dict[str, Any]] = None,
        resource_bindings: Optional[dict[str, str]] = None,
        constraints: Optional[Sequence[Constraint]] = None,
        job_stats_by_task: Optional[dict[str, Any]] = None,
        unfulfilled_requests: Optional[list[dict[str, Any]]] = None,
        capability_status: Optional[dict[str, Any]] = None,
        unit_reservations: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        if active_tasks is not None:
            self.active_tasks = dict(active_tasks)
        if active_jobs is not None:
            self.active_jobs = dict(active_jobs)
        if resource_bindings is not None:
            self.resource_bindings = dict(resource_bindings)
        if constraints is not None:
            self.constraints = {item.constraint_id: item for item in constraints}
        if job_stats_by_task is not None:
            self._job_stats_by_task = dict(job_stats_by_task)
        if unfulfilled_requests is not None:
            self._unfulfilled_requests = list(unfulfilled_requests)
        if capability_status is not None:
            self._capability_state = CapabilityStatusSnapshot.from_mapping(capability_status)
        if unit_reservations is not None:
            self._unit_reservations = list(unit_reservations)

    def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True) -> dict[str, Any]:
        """Structured, decision-oriented runtime facts for LLM context injection.

        Returns precise boolean/int fields so the LLM doesn't need to infer
        state from coarse world_summary prose.
        """
        counts = self._count_self_actors()
        structure_power_state = self._self_structure_power_state()
        economy = self.state.economy
        total_credits = economy.get("total_credits", 0)

        has_construction_yard = counts["has_construction_yard"]
        power_plant_count = counts["power_plant_count"]
        barracks_count = counts["barracks_count"]
        refinery_count = counts["refinery_count"]
        war_factory_count = counts["war_factory_count"]
        radar_count = counts["radar_count"]
        tech_center_count = counts["tech_center_count"]
        repair_facility_count = counts["repair_facility_count"]
        airfield_count = counts["airfield_count"]
        mcv_count = counts["mcv_count"]
        mcv_idle = counts["mcv_idle"]
        harvester_count = counts["harvester_count"]
        combat_unit_count = counts["combat_unit_count"]

        # Tech level: 0=no base, 1=yard only, 2=has production, 3=has tech
        if not has_construction_yard:
            tech_level = 0
        elif not (barracks_count > 0 or war_factory_count > 0):
            tech_level = 1
        elif not (radar_count > 0):
            tech_level = 2
        else:
            tech_level = 3

        # Jobs for this task (from active_jobs sync, which excludes terminal jobs).
        this_task_jobs = [
            {
                "job_id": job_id,
                "expert_type": info.get("expert_type", ""),
                "status": info.get("status", ""),
                "phase": "",  # Phase not tracked in WorldModel sync; available in agent signals.
            }
            for job_id, info in self.active_jobs.items()
            if info.get("task_id") == task_id
        ]

        # Historical job stats for this task (populated via set_runtime_state).
        task_stats = self._job_stats_by_task.get(task_id, {})
        failed_job_count = task_stats.get("failed_count", 0)
        expert_attempts: dict[str, int] = task_stats.get("expert_attempts", {})
        same_expert_retry_count = max(expert_attempts.values()) - 1 if expert_attempts else 0

        facts: dict[str, Any] = {
            "faction": counts.get("player_faction"),
            "world_sync_stale": self.state.stale,
            "world_sync_consecutive_failures": self._consecutive_refresh_failures,
            "world_sync_failure_threshold": self.stale_failure_threshold,
            "world_sync_total_failures": self._total_refresh_failures,
            "world_sync_last_error": self._last_refresh_error,
            "low_power": bool(economy.get("low_power")),
            "disabled_structure_count": structure_power_state["disabled_structure_count"],
            "powered_down_structure_count": structure_power_state["powered_down_structure_count"],
            "low_power_disabled_structure_count": structure_power_state["low_power_disabled_structure_count"],
            "power_outage_structure_count": structure_power_state["power_outage_structure_count"],
            "disabled_structures": list(structure_power_state["disabled_structures"]),
            "has_construction_yard": has_construction_yard,
            "power_plant_count": power_plant_count,
            "barracks_count": barracks_count,
            "refinery_count": refinery_count,
            "war_factory_count": war_factory_count,
            "radar_count": radar_count,
            "tech_center_count": tech_center_count,
            "repair_facility_count": repair_facility_count,
            "airfield_count": airfield_count,
            "tech_level": tech_level,
            "mcv_count": mcv_count,
            "mcv_idle": mcv_idle,
            "harvester_count": harvester_count,
            "active_task_count": len(self.active_tasks),
            "active_actor_ids": list(self.active_tasks.get(task_id, {}).get("active_actor_ids", [])),
            "active_group_size": int(self.active_tasks.get(task_id, {}).get("active_group_size", 0) or 0),
            "this_task_jobs": this_task_jobs,
            "failed_job_count": failed_job_count,
            "same_expert_retry_count": max(same_expert_retry_count, 0),
        }
        queue_block_state = self._queue_block_state()
        facts["queue_blocked"] = bool(queue_block_state.get("blocked"))
        facts["queue_blocked_reason"] = str(queue_block_state.get("reason", "") or "")
        facts["queue_blocked_queue_types"] = list(queue_block_state.get("queue_types", []))
        facts["queue_blocked_items"] = [dict(item) for item in list(queue_block_state.get("items", []))]
        faction = str(facts.get("faction") or "").strip().lower()
        unsupported_capability_roster = bool(faction and faction != "soviet")
        if unsupported_capability_roster:
            facts["capability_truth_blocker"] = "faction_roster_unsupported"

        if include_buildable:
            power_plant_cost = dataset_cost_for("powr") or 0
            barracks_cost = dataset_cost_for("barr") or 0
            refinery_cost = dataset_cost_for("proc") or 0
            buildability = demo_capability_buildability_snapshot(
                has_construction_yard=has_construction_yard,
                mcv_count=mcv_count,
                power_plant_count=power_plant_count,
                refinery_count=refinery_count,
                barracks_count=barracks_count,
                war_factory_count=war_factory_count,
                radar_count=radar_count,
                repair_facility_count=repair_facility_count,
                tech_center_count=tech_center_count,
                airfield_count=airfield_count,
            )
            buildable = self._filter_buildable_by_active_prerequisites(buildability.get("buildable"))
            base_progression = demo_base_progression(
                has_construction_yard=has_construction_yard,
                mcv_count=mcv_count,
                power_plant_count=power_plant_count,
                refinery_count=refinery_count,
                barracks_count=barracks_count,
                war_factory_count=war_factory_count,
                buildable=buildable,
            )
            facts.update({
                "can_afford_power_plant": total_credits >= power_plant_cost,
                "can_afford_barracks": total_credits >= barracks_cost,
                "can_afford_refinery": total_credits >= refinery_cost,
                "base_progression": dict(base_progression or {}),
            })
            # Capability-facing buildability: only expose when the caller is a
            # dedicated capability planner. Ordinary task contexts should not
            # infer they can build prerequisites themselves from this field.
            facts["buildable"] = buildable
            readiness_snapshot = self._demo_capability_issue_now_snapshot()
            facts["buildable_now"] = readiness_snapshot["buildable_now"]
            facts["buildable_blocked"] = readiness_snapshot["buildable_blocked"]
            has_buildable_capability_action = any(
                bool(buildable.get(queue_type))
                for queue_type in demo_capability_queue_types()
            )
            facts["feasibility"] = {
                "deploy_mcv": mcv_count > 0,
                "scout_map": combat_unit_count > 0,
                # Keep feasibility aligned with the dataset-driven buildable truth
                # instead of a parallel coarse heuristic.
                "produce_units": has_buildable_capability_action,
                "attack": combat_unit_count > 0,
                "move_units": (combat_unit_count + mcv_count + harvester_count) > 0,
            }
            if unsupported_capability_roster:
                facts["buildable"] = {}
                facts["buildable_now"] = {}
                facts["buildable_blocked"] = {}
                facts["base_progression"] = {}
                facts["feasibility"]["produce_units"] = False

        # Unfulfilled unit requests (from Kernel via set_runtime_state)
        facts["unfulfilled_requests"] = list(self._unfulfilled_requests)
        facts["unit_reservations"] = list(self._unit_reservations)
        facts["capability_status"] = self._capability_state.to_dict()
        if self._capability_state.task_id == task_id:
            facts["task_phase"] = self._capability_state.phase
            facts["capability_blocker"] = self._capability_state.blocker
            facts["blocking_request_count"] = self._capability_state.blocking_request_count

        # Production queues — transform game state format to renderer-friendly format
        # Game state: {queue_type: {"queue_type": str, "items": [{"name":..,"progress":..}]}}
        # Renderer expects: {queue_type: [{"unit_type": str, "count": int, "source": str}]}
        prod_queues: dict[str, list[dict[str, Any]]] = {}
        ready_queue_items: list[dict[str, Any]] = []
        for qname, qdata in self.state.production_queues.items():
            items_raw = qdata.get("items", []) if isinstance(qdata, dict) else []
            items_out: list[dict[str, Any]] = []
            for item in items_raw:
                if not isinstance(item, dict):
                    continue
                status = item.get("status", "")
                # Only include items that are actively building or queued
                if bool(item.get("done")) or status in ("done", "completed"):
                    ready_queue_items.append({
                        "queue_type": qname,
                        "unit_type": item.get("name", "?"),
                        "display_name": item.get("display_name", "") or item.get("name", "?"),
                        "owner_actor_id": item.get("owner_actor_id"),
                    })
                    continue
                items_out.append({
                    "unit_type": item.get("name", "?"),
                    "count": 1,
                    "source": "",
                    "progress": item.get("progress"),
                })
            prod_queues[qname] = items_out
        facts["production_queues"] = prod_queues
        facts["ready_queue_items"] = ready_queue_items

        # Enemy intel summary for LLM context
        enemy_buildings: list[dict[str, Any]] = []
        enemy_infantry = 0
        enemy_vehicles = 0
        enemy_other = 0
        for a in self.state.actors.values():
            if a.owner != ActorOwner.ENEMY or not a.is_alive:
                continue
            if a.category == ActorCategory.BUILDING:
                pos = a.position
                enemy_buildings.append({"name": a.display_name or a.name, "position": pos})
            elif a.category == ActorCategory.INFANTRY:
                enemy_infantry += 1
            elif a.category == ActorCategory.VEHICLE:
                enemy_vehicles += 1
            else:
                enemy_other += 1
        # Frozen enemies (last-seen positions in fog-of-war, not currently visible)
        frozen_buildings: list[dict[str, Any]] = []
        for f in self.state.frozen_enemies:
            if f.get("position"):
                frozen_buildings.append({"name": f.get("type", "?"), "position": f["position"], "frozen": True})
        facts["enemy_intel"] = {
            "buildings": enemy_buildings,
            "infantry_count": enemy_infantry,
            "vehicle_count": enemy_vehicles,
            "other_count": enemy_other,
            "total": len(enemy_buildings) + enemy_infantry + enemy_vehicles + enemy_other,
            "frozen": frozen_buildings,
            "frozen_count": len(frozen_buildings),
        }

        # Merge Information Expert analyses under info_experts key.
        if self._info_experts:
            enemy_actors = [
                {
                    "category": a.category.value if hasattr(a.category, "value") else str(a.category),
                    "position": a.position,
                }
                for a in self.state.actors.values()
                if a.owner == ActorOwner.ENEMY and a.is_alive
            ]
            recent_events = [
                {"type": e.type.value if hasattr(e.type, "value") else str(e.type)}
                for e in self._event_history[-20:]
            ]
            info_expert_data: dict[str, Any] = {}
            for expert in self._info_experts:
                try:
                    info_expert_data.update(
                        expert.analyze(facts, enemy_actors=enemy_actors, recent_events=recent_events)
                    )
                except Exception:
                    pass  # never let an info expert crash the runtime facts call
            facts["info_experts"] = info_expert_data

        return facts

    def _demo_capability_issue_now_snapshot(self) -> dict[str, dict[str, Any]]:
        """Split demo roster truth into safe-now vs prereq-only-but-blocked views."""
        buildable_now: dict[str, list[str]] = {}
        buildable_blocked: dict[str, list[dict[str, Any]]] = {}
        for queue_type in demo_capability_queue_types():
            for unit_type in demo_capability_units_for_queue(queue_type):
                readiness = self.production_readiness_for(unit_type, queue_type=queue_type)
                if readiness.get("can_issue_now"):
                    buildable_now.setdefault(queue_type, []).append(unit_type)
                    continue
                if not readiness.get("prereq_satisfied"):
                    continue
                buildable_blocked.setdefault(queue_type, []).append({
                    "unit_type": unit_type,
                    "queue_type": queue_type,
                    "reason": str(readiness.get("reason", "") or ""),
                    "queue_blocked_reason": str(readiness.get("queue_blocked_reason", "") or ""),
                    "queue_blocked_items": filter_demo_capability_ready_items(
                        [
                            dict(item)
                            for item in list(readiness.get("queue_blocked_items", []))
                            if isinstance(item, dict)
                        ]
                    ),
                    "disabled_producers": list(readiness.get("disabled_producers", []) or []),
                    "disabled_prerequisites": list(readiness.get("disabled_prerequisites", []) or []),
                })
        return {
            "buildable_now": buildable_now,
            "buildable_blocked": buildable_blocked,
        }

    def _count_self_actors(self) -> dict[str, Any]:
        """Count self actors by category/building type. Shared by runtime_facts and buildable."""
        has_construction_yard = False
        power_plant_count = 0
        barracks_count = 0
        refinery_count = 0
        war_factory_count = 0
        radar_count = 0
        tech_center_count = 0
        repair_facility_count = 0
        airfield_count = 0
        mcv_count = 0
        mcv_idle = False
        harvester_count = 0
        combat_unit_count = 0
        faction_unit_types: list[str] = []
        for actor in self.state.actors.values():
            if actor.owner != ActorOwner.SELF or not actor.is_alive:
                continue
            unit_id = production_name_unit_id(actor.name) or production_name_unit_id(actor.display_name)
            if unit_id:
                faction_unit_types.append(unit_id)
            if actor.category == ActorCategory.MCV:
                mcv_count += 1
                if actor.is_idle:
                    mcv_idle = True
            elif actor.category == ActorCategory.HARVESTER:
                harvester_count += 1
            elif actor.category in (ActorCategory.INFANTRY, ActorCategory.VEHICLE):
                combat_unit_count += 1
            elif actor.category == ActorCategory.BUILDING:
                counter_field = demo_base_counter_field_for(unit_id)
                if counter_field == "has_construction_yard":
                    has_construction_yard = True
                elif counter_field == "power_plant_count":
                    power_plant_count += 1
                elif counter_field == "barracks_count":
                    barracks_count += 1
                elif counter_field == "refinery_count":
                    refinery_count += 1
                elif counter_field == "war_factory_count":
                    war_factory_count += 1
                elif counter_field == "radar_count":
                    radar_count += 1
                elif counter_field == "tech_center_count":
                    tech_center_count += 1
                elif counter_field == "repair_facility_count":
                    repair_facility_count += 1
                elif counter_field == "airfield_count":
                    airfield_count += 1
        return {
            "has_construction_yard": has_construction_yard,
            "power_plant_count": power_plant_count,
            "barracks_count": barracks_count,
            "refinery_count": refinery_count,
            "war_factory_count": war_factory_count,
            "radar_count": radar_count,
            "tech_center_count": tech_center_count,
            "repair_facility_count": repair_facility_count,
            "airfield_count": airfield_count,
            "mcv_count": mcv_count,
            "mcv_idle": mcv_idle,
            "harvester_count": harvester_count,
            "combat_unit_count": combat_unit_count,
            "player_faction": demo_faction_hint_for_unit_types(faction_unit_types),
        }

    def _self_structure_power_state(self) -> dict[str, Any]:
        disabled_structure_count = 0
        powered_down_structure_count = 0
        low_power_disabled_structure_count = 0
        power_outage_structure_count = 0
        disabled_structures: list[str] = []
        for actor in self.state.actors.values():
            if actor.owner != ActorOwner.SELF or not actor.is_alive or actor.category != ActorCategory.BUILDING:
                continue
            if actor.is_disabled:
                disabled_structure_count += 1
            if actor.is_powered_down:
                powered_down_structure_count += 1
            if actor.has_low_power and actor.is_disabled:
                low_power_disabled_structure_count += 1
            if actor.has_power_outage:
                power_outage_structure_count += 1
            if actor.is_disabled:
                label = actor.display_name or actor.name
                reason = actor.disabled_reason or (
                    "powerdown"
                    if actor.is_powered_down
                    else "lowpower"
                    if actor.has_low_power
                    else "power-outage"
                    if actor.has_power_outage
                    else "disabled"
                )
                disabled_structures.append(f"{label}({reason})")
        return {
            "disabled_structure_count": disabled_structure_count,
            "powered_down_structure_count": powered_down_structure_count,
            "low_power_disabled_structure_count": low_power_disabled_structure_count,
            "power_outage_structure_count": power_outage_structure_count,
            "disabled_structures": disabled_structures,
        }

    def runtime_facts_buildable(self) -> dict[str, list[str]]:
        """Return current buildable units per queue (lightweight, no task_id needed)."""
        c = self._count_self_actors()
        snapshot = demo_capability_buildability_snapshot(
            has_construction_yard=c["has_construction_yard"],
            mcv_count=c["mcv_count"],
            power_plant_count=c["power_plant_count"],
            refinery_count=c["refinery_count"],
            barracks_count=c["barracks_count"],
            war_factory_count=c["war_factory_count"],
            radar_count=c["radar_count"],
            repair_facility_count=c["repair_facility_count"],
            tech_center_count=c["tech_center_count"],
            airfield_count=c["airfield_count"],
        )
        return self._filter_buildable_by_active_prerequisites(snapshot.get("buildable"))

    def _queue_block_state(self, queue_types: Sequence[str] | None = None) -> dict[str, Any]:
        """Return structured queue-blocking truth for the selected queues."""
        selected = set(str(q) for q in (queue_types or self.state.production_queues.keys()) if q)
        reasons: list[str] = []
        blocked_queues: list[str] = []
        blocked_items: list[dict[str, Any]] = []
        for qname, queue in self.state.production_queues.items():
            if selected and qname not in selected:
                continue
            has_ready_item = bool(queue.get("has_ready_item"))
            ready_items = [
                self._queue_block_item_payload(qname, item)
                for item in list(queue.get("items", []) or [])
                if isinstance(item, dict) and (
                    bool(item.get("done")) or str(item.get("status", "")).lower() in {"done", "completed"}
                )
            ]
            has_paused = any(
                bool(item.get("paused"))
                for item in queue.get("items", [])
                if isinstance(item, dict)
            )
            if not (has_ready_item or has_paused):
                continue
            blocked_queues.append(str(qname))
            if has_ready_item and "ready_not_placed" not in reasons:
                reasons.append("ready_not_placed")
                blocked_items.extend(item for item in ready_items if item)
            if has_paused and "paused" not in reasons:
                reasons.append("paused")
        if not blocked_queues:
            return {"blocked": False, "reason": "", "reasons": [], "queue_types": [], "items": []}
        if len(reasons) == 1:
            reason = reasons[0]
        else:
            reason = "mixed"
        return {
            "blocked": True,
            "reason": reason,
            "reasons": reasons,
            "queue_types": blocked_queues,
            "items": blocked_items,
        }

    def _queue_block_item_payload(self, queue_type: str, item: dict[str, Any]) -> dict[str, Any]:
        unit_type = str(item.get("name") or "").lower()
        display_name = str(item.get("display_name") or "")
        if not display_name and unit_type:
            display_name = demo_display_name_for(unit_type)
        if not display_name:
            display_name = unit_type or str(queue_type or "?")
        return {
            "queue_type": str(queue_type or ""),
            "unit_type": unit_type,
            "display_name": display_name,
            "owner_actor_id": item.get("owner_actor_id"),
        }

    def _disabled_actor_label(self, actor: Any) -> str:
        label = actor.display_name or actor.name
        reason = actor.disabled_reason or (
            "powerdown"
            if actor.is_powered_down
            else "lowpower"
            if actor.has_low_power
            else "power-outage"
            if actor.has_power_outage
            else "disabled"
        )
        return f"{label}({reason})"

    def _prerequisite_provider_state(self, prerequisite_ids: Sequence[str]) -> dict[str, Any]:
        selected = [str(item).lower() for item in prerequisite_ids if item]
        if not selected:
            return {
                "owned_prerequisites": [],
                "available_prerequisites": [],
                "disabled_prerequisites": [],
            }

        selected_set = set(selected)
        owned: set[str] = set()
        available: set[str] = set()
        disabled_by_prereq: dict[str, list[str]] = {}
        for actor in self.state.actors.values():
            if actor.owner != ActorOwner.SELF or not actor.is_alive or actor.category != ActorCategory.BUILDING:
                continue
            actor_unit_id = production_name_unit_id(actor.name) or production_name_unit_id(actor.display_name)
            canonical = str(actor_unit_id or "").lower()
            if canonical not in selected_set:
                continue
            owned.add(canonical)
            if actor.is_disabled:
                disabled_by_prereq.setdefault(canonical, []).append(self._disabled_actor_label(actor))
            else:
                available.add(canonical)

        disabled_prerequisites: list[str] = []
        for prerequisite_id in selected:
            if prerequisite_id not in owned or prerequisite_id in available:
                continue
            labels = [str(item) for item in list(disabled_by_prereq.get(prerequisite_id, [])) if item]
            if labels:
                disabled_prerequisites.extend(labels)
                continue
            disabled_prerequisites.append(demo_display_name_for(prerequisite_id))

        return {
            "owned_prerequisites": sorted(owned),
            "available_prerequisites": sorted(available),
            "disabled_prerequisites": disabled_prerequisites,
        }

    def _filter_buildable_by_active_prerequisites(self, buildable: Mapping[str, Sequence[str]] | None) -> dict[str, list[str]]:
        filtered: dict[str, list[str]] = {}
        for queue_type, units in dict(buildable or {}).items():
            queue_units: list[str] = []
            for unit_type in list(units or []):
                canonical = str(unit_type or "").lower()
                if not canonical:
                    continue
                prerequisites = demo_prerequisites_for(canonical)
                prerequisite_state = self._prerequisite_provider_state(prerequisites)
                available_prerequisites = {
                    str(item).lower()
                    for item in list(prerequisite_state.get("available_prerequisites", []))
                    if item
                }
                if all(prerequisite in available_prerequisites for prerequisite in prerequisites):
                    queue_units.append(canonical)
            if queue_units:
                filtered[str(queue_type)] = queue_units
        return filtered

    def _queue_producer_state(self, queue_type: str) -> dict[str, Any]:
        producer_ids = {
            str(item).lower()
            for item in QUEUE_PRODUCER_UNIT_IDS.get(str(queue_type or ""), ())
            if item
        }
        if not producer_ids:
            return {
                "queue_type": str(queue_type or ""),
                "producer_ids": [],
                "producer_count": 0,
                "active_producer_count": 0,
                "disabled_producer_count": 0,
                "low_power_disabled_count": 0,
                "powered_down_count": 0,
                "power_outage_count": 0,
                "disabled_producers": [],
            }

        producer_count = 0
        active_producer_count = 0
        disabled_producer_count = 0
        low_power_disabled_count = 0
        powered_down_count = 0
        power_outage_count = 0
        disabled_producers: list[str] = []
        for actor in self.state.actors.values():
            if actor.owner != ActorOwner.SELF or not actor.is_alive or actor.category != ActorCategory.BUILDING:
                continue
            actor_unit_id = production_name_unit_id(actor.name) or production_name_unit_id(actor.display_name)
            if str(actor_unit_id or "").lower() not in producer_ids:
                continue
            producer_count += 1
            if actor.is_disabled:
                disabled_producer_count += 1
                if actor.has_low_power:
                    low_power_disabled_count += 1
                if actor.is_powered_down:
                    powered_down_count += 1
                if actor.has_power_outage:
                    power_outage_count += 1
                disabled_producers.append(self._disabled_actor_label(actor))
            else:
                active_producer_count += 1
        return {
            "queue_type": str(queue_type or ""),
            "producer_ids": sorted(producer_ids),
            "producer_count": producer_count,
            "active_producer_count": active_producer_count,
            "disabled_producer_count": disabled_producer_count,
            "low_power_disabled_count": low_power_disabled_count,
            "powered_down_count": powered_down_count,
            "power_outage_count": power_outage_count,
            "disabled_producers": disabled_producers,
        }

    def production_readiness_for(self, unit_type: str, *, queue_type: str | None = None) -> dict[str, Any]:
        """Return whether a demo production order is safe to issue right now.

        This is intentionally stricter than prerequisite-only buildability. It
        folds in stale world state, MCV deployment requirements, queue jams, low
        power, and affordability so Kernel fast-paths do not over-infer
        producibility from raw prereq truth.
        """
        canonical = str(unit_type or "").lower()
        resolved_queue = str(queue_type or demo_queue_type_for(canonical) or "")
        counts = self._count_self_actors()
        snapshot = demo_capability_buildability_snapshot(
            has_construction_yard=counts["has_construction_yard"],
            mcv_count=counts["mcv_count"],
            power_plant_count=counts["power_plant_count"],
            refinery_count=counts["refinery_count"],
            barracks_count=counts["barracks_count"],
            war_factory_count=counts["war_factory_count"],
            radar_count=counts["radar_count"],
            repair_facility_count=counts["repair_facility_count"],
            tech_center_count=counts["tech_center_count"],
            airfield_count=counts["airfield_count"],
        )
        buildable = self._filter_buildable_by_active_prerequisites(snapshot.get("buildable"))
        base_progression = demo_base_progression(
            has_construction_yard=counts["has_construction_yard"],
            mcv_count=counts["mcv_count"],
            power_plant_count=counts["power_plant_count"],
            refinery_count=counts["refinery_count"],
            barracks_count=counts["barracks_count"],
            war_factory_count=counts["war_factory_count"],
            buildable=buildable,
        )
        economy = dict(self.state.economy)
        queue_block_state = self._queue_block_state([resolved_queue] if resolved_queue else None)
        producer_state = self._queue_producer_state(resolved_queue)
        prerequisites = demo_prerequisites_for(canonical)
        prerequisite_state = self._prerequisite_provider_state(prerequisites)
        owned_prerequisites = {
            str(item).lower()
            for item in list(prerequisite_state.get("owned_prerequisites", []))
            if item
        }
        available_prerequisites = {
            str(item).lower()
            for item in list(prerequisite_state.get("available_prerequisites", []))
            if item
        }
        disabled_prerequisites = [
            str(item)
            for item in list(prerequisite_state.get("disabled_prerequisites", []))
            if item
        ]
        queue_blocked = bool(queue_block_state.get("blocked"))
        low_power = bool(economy.get("low_power"))
        deploy_required = (
            not counts["has_construction_yard"]
            and int(counts["mcv_count"] or 0) > 0
            and str(base_progression.get("phase") or "") == "deploy_mcv"
        )
        all_producers_disabled = (
            int(producer_state.get("producer_count", 0) or 0) > 0
            and int(producer_state.get("active_producer_count", 0) or 0) == 0
        )
        prereq_satisfied = all(prerequisite in owned_prerequisites for prerequisite in prerequisites)
        active_prereq_satisfied = all(prerequisite in available_prerequisites for prerequisite in prerequisites)
        cost = dataset_cost_for(canonical) or 0
        affordable = cost <= 0 or int(economy.get("total_credits", 0) or 0) >= cost

        reason = ""
        can_issue_now = prereq_satisfied
        if self.state.stale:
            reason = "world_sync_stale"
            can_issue_now = False
        elif deploy_required:
            reason = "deploy_required"
            can_issue_now = False
        elif not prereq_satisfied:
            reason = "missing_prerequisite"
            can_issue_now = False
        elif queue_blocked:
            reason = "queue_blocked"
            can_issue_now = False
        elif all_producers_disabled:
            if low_power and int(producer_state.get("low_power_disabled_count", 0) or 0) > 0:
                reason = "low_power"
            else:
                reason = "producer_disabled"
            can_issue_now = False
        elif prereq_satisfied and not active_prereq_satisfied and disabled_prerequisites:
            reason = "disabled_prerequisite"
            can_issue_now = False
        elif low_power and canonical not in {"powr", "apwr"}:
            reason = "low_power"
            can_issue_now = False
        elif not affordable:
            reason = "insufficient_funds"
            can_issue_now = False

        return {
            "unit_type": canonical,
            "queue_type": resolved_queue,
            "prereq_satisfied": prereq_satisfied,
            "active_prereq_satisfied": active_prereq_satisfied,
            "can_issue_now": can_issue_now,
            "reason": reason,
            "world_sync_stale": self.state.stale,
            "world_sync_last_error": self._last_refresh_error or "",
            "world_sync_consecutive_failures": self._consecutive_refresh_failures,
            "world_sync_failure_threshold": self.stale_failure_threshold,
            "deploy_required": deploy_required,
            "low_power": low_power,
            "queue_blocked": queue_blocked,
            "queue_blocked_reason": str(queue_block_state.get("reason", "") or ""),
            "queue_blocked_queue_types": list(queue_block_state.get("queue_types", [])),
            "queue_blocked_items": [dict(item) for item in list(queue_block_state.get("items", [])) if isinstance(item, dict)],
            "producer_count": int(producer_state.get("producer_count", 0) or 0),
            "active_producer_count": int(producer_state.get("active_producer_count", 0) or 0),
            "disabled_producer_count": int(producer_state.get("disabled_producer_count", 0) or 0),
            "disabled_producers": list(producer_state.get("disabled_producers", [])),
            "disabled_prerequisites": disabled_prerequisites,
            "affordable": affordable,
            "cost": cost,
        }

    def register_info_expert(self, expert: Any) -> None:
        """Register an Information Expert whose analyze() output is merged into runtime_facts."""
        self._info_experts.append(expert)

    def bind_resource(self, resource_id: str, job_id: str) -> None:
        self.resource_bindings[resource_id] = job_id

    def unbind_resource(self, resource_id: str) -> None:
        self.resource_bindings.pop(resource_id, None)

    def set_constraint(self, constraint: Constraint) -> None:
        self.constraints[constraint.constraint_id] = constraint

    def remove_constraint(self, constraint_id: str) -> None:
        self.constraints.pop(constraint_id, None)

    def last_refresh_layers(self) -> list[str]:
        return list(self._last_refresh_layers)

    def recent_events(self, limit: int = 20) -> list[Event]:
        return list(self._event_history[-limit:])

    def refresh_health(self) -> dict[str, Any]:
        return {
            "stale": self.state.stale,
            "disconnected": self._last_refresh_disconnected,
            "consecutive_failures": self._consecutive_refresh_failures,
            "total_failures": self._total_refresh_failures,
            "last_error": self._last_refresh_error,
            "failure_threshold": self.stale_failure_threshold,
            "timestamp": self.state.timestamp,
        }

    def reset_snapshot(self, *, clear_history: bool = True) -> None:
        self.state = WorldState(timestamp=0.0)
        self._last_actor_refresh = 0.0
        self._last_economy_refresh = 0.0
        self._last_map_refresh = 0.0
        self._layer_retry_after = {"actors": 0.0, "economy": 0.0, "map": 0.0}
        self._map_static_fetched = False
        self._pending_events = []
        self._last_refresh_layers = []
        self._frontline_weak_active = False
        self._economy_surplus_active = False
        self._low_power_active = False
        self._consecutive_refresh_failures = 0
        self._total_refresh_failures = 0
        self._last_refresh_error = None
        self._last_refresh_disconnected = False
        self._refresh_failure_log_state = {}
        self._slow_refresh_log_state = {"last_log_at": 0.0, "suppressed_count": 0}
        if clear_history:
            self._event_history = []

    def _log_refresh_failure(self, layer: str, exc: Exception, timestamp: float) -> None:
        error = str(exc)
        state = self._refresh_failure_log_state.get(layer)
        if state and state["error"] == error and timestamp - state["last_log_at"] < REFRESH_FAILURE_LOG_COOLDOWN_S:
            state["suppressed_count"] += 1
            return

        suppressed_count = 0
        if state and state["error"] == error:
            suppressed_count = int(state.get("suppressed_count", 0))

        detail = self._extract_exception_detail(exc)
        summary = f"WorldModel {layer} refresh failed: {error}"
        if detail:
            summary = f"{summary} | detail: {detail}"
        if suppressed_count:
            summary = f"{summary} ({suppressed_count} repeat(s) suppressed)"

        logger.warning(summary)
        slog.warn(
            f"WorldModel {layer} refresh failed",
            event="world_refresh_failed",
            layer=layer,
            error=error,
            error_detail=detail,
            disconnected=self._is_connection_failure(exc),
            error_meta=self._extract_exception_meta(exc),
            failure_threshold=self.stale_failure_threshold,
            suppressed_count=suppressed_count,
        )
        self._refresh_failure_log_state[layer] = {
            "error": error,
            "last_log_at": timestamp,
            "suppressed_count": 0,
        }

    def _clear_refresh_failure_log_state(self, layer: str) -> None:
        self._refresh_failure_log_state.pop(layer, None)

    def _log_slow_refresh(
        self,
        total_ms: float,
        layer_timings: Mapping[str, float],
        actor_count: int,
        timestamp: float,
    ) -> None:
        state = self._slow_refresh_log_state
        if timestamp - float(state.get("last_log_at", 0.0) or 0.0) < SLOW_REFRESH_LOG_COOLDOWN_S:
            state["suppressed_count"] = int(state.get("suppressed_count", 0) or 0) + 1
            return

        suppressed_count = int(state.get("suppressed_count", 0) or 0)
        summary = (
            f"Slow world refresh: total={round(total_ms, 1)}ms "
            f"layers={{{', '.join(f'{key}={round(value, 1)}' for key, value in layer_timings.items())}}}"
        )
        if suppressed_count:
            summary = f"{summary} ({suppressed_count} repeat(s) suppressed)"

        logger.warning(summary)
        slog.warn(
            "Slow world refresh",
            event="world_refresh_slow",
            total_ms=round(total_ms, 1),
            layer_ms={key: round(value, 1) for key, value in layer_timings.items()},
            actor_count=actor_count,
            suppressed_count=suppressed_count,
        )
        self._slow_refresh_log_state = {
            "last_log_at": timestamp,
            "suppressed_count": 0,
        }

    def _extract_exception_detail(self, exc: Exception) -> Optional[str]:
        details = getattr(exc, "details", None)
        if isinstance(details, Mapping):
            for key in ("message", "inner", "type"):
                value = details.get(key)
                if value:
                    return str(value)
        return None

    def _extract_exception_meta(self, exc: Exception) -> dict[str, Any]:
        details = getattr(exc, "details", None)
        if not isinstance(details, Mapping):
            return {}
        payload: dict[str, Any] = {}
        for key in ("type", "message", "inner", "data"):
            value = details.get(key)
            if value is not None:
                payload[key] = value
        return payload

    def _due_layers(self, now: float, force: bool) -> list[str]:
        layers: list[str] = []
        if force or (
            now >= self._layer_retry_after["actors"]
            and (not self.state.actors or now - self._last_actor_refresh >= self.refresh_policy.actors_s)
        ):
            layers.append("actors")
        if force or (
            now >= self._layer_retry_after["economy"]
            and (not self.state.economy or now - self._last_economy_refresh >= self.refresh_policy.economy_s)
        ):
            layers.append("economy")
        if force or (
            now >= self._layer_retry_after["map"]
            and (not self.state.map_info or now - self._last_map_refresh >= self.refresh_policy.map_s)
        ):
            layers.append("map")
        return layers

    def _mark_layer_retry_backoff(self, layer: str, timestamp: float) -> None:
        self._layer_retry_after[layer] = max(
            self._layer_retry_after.get(layer, 0.0),
            timestamp + CONNECTION_FAILURE_RETRY_BACKOFF_S,
        )

    @staticmethod
    def _is_connection_failure(exc: Exception) -> bool:
        code = str(getattr(exc, "code", "") or "").upper()
        if code in {"CONNECTION_ERROR", "REQUEST_ID_MISMATCH"}:
            return True
        if isinstance(exc, (ConnectionError, OSError)):
            return True
        message = str(exc).lower()
        if any(token in message for token in ("disconnected", "connection refused", "连接服务器失败", "连接失败")):
            return True
        return False

    def _normalize_actors(
        self,
        self_actors: Sequence[Actor],
        enemy_actors: Sequence[Actor],
        timestamp: float,
    ) -> dict[str, Any]:
        actors: dict[int, NormalizedActor] = {}
        self_ids: set[int] = set()
        enemy_ids: set[int] = set()
        for raw in self_actors:
            actor = self._normalize_actor(raw, ActorOwner.SELF, timestamp)
            actors[actor.actor_id] = actor
            self_ids.add(actor.actor_id)
        for raw in enemy_actors:
            actor = self._normalize_actor(raw, ActorOwner.ENEMY, timestamp)
            actors[actor.actor_id] = actor
            enemy_ids.add(actor.actor_id)
        return {"actors": actors, "self_ids": self_ids, "enemy_ids": enemy_ids}

    def _normalize_actor(self, raw: Actor, default_owner: ActorOwner, timestamp: float) -> NormalizedActor:
        raw_name = getattr(raw, "type", None) or "unknown"
        name = normalize_unit_name(raw_name)
        owner = self._actor_owner(getattr(raw, "faction", None), default_owner)
        category = self._actor_category(name)
        hp = int(getattr(raw, "hppercent", 100) or 0)
        position = self._location_to_tuple(getattr(raw, "position", None))
        mobility = self._mobility(name, category)
        can_harvest = category == ActorCategory.HARVESTER
        can_attack = self._can_attack(name, category)
        return NormalizedActor(
            actor_id=int(getattr(raw, "actor_id")),
            name=name,
            display_name=str(raw_name),
            owner=owner,
            category=category,
            position=position,
            hp=hp,
            hp_max=100,
            is_alive=hp > 0,
            is_idle=self._is_idle(getattr(raw, "activity", None), getattr(raw, "order", None)),
            mobility=mobility,
            combat_value=self._combat_value(name, category),
            can_attack=can_attack,
            can_harvest=can_harvest,
            weapon_range=self._weapon_range(name, category, can_attack),
            is_disabled=bool(getattr(raw, "is_disabled", False)),
            is_powered_down=bool(getattr(raw, "is_powered_down", False)),
            has_low_power=bool(getattr(raw, "has_low_power", False)),
            has_power_outage=bool(getattr(raw, "has_power_outage", False)),
            disabled_reason=str(getattr(raw, "disabled_reason", "") or ""),
            timestamp=timestamp,
        )

    def _normalize_economy(self, base_info: Optional[PlayerBaseInfo], timestamp: float) -> dict[str, Any]:
        if base_info is None:
            return {"cash": 0, "resources": 0, "total_credits": 0, "timestamp": timestamp}
        cash = int(getattr(base_info, "Cash", 0) or 0)
        resources = int(getattr(base_info, "Resources", 0) or 0)
        power = int(getattr(base_info, "Power", 0) or 0)
        drained = int(getattr(base_info, "PowerDrained", 0) or 0)
        provided = int(getattr(base_info, "PowerProvided", 0) or 0)
        return {
            "cash": cash,
            "resources": resources,
            "total_credits": cash + resources,
            "power": power,
            "power_drained": drained,
            "power_provided": provided,
            "low_power": provided > 0 and drained > provided,
            "timestamp": timestamp,
        }

    def _normalize_map(self, map_info: Optional[MapQueryResult], timestamp: float) -> dict[str, Any]:
        if map_info is None:
            return {"width": 0, "height": 0, "explored_pct": 0.0, "visible_pct": 0.0, "timestamp": timestamp}
        # Use C#-computed explored_pct if available, otherwise compute from grid.
        server_explored_pct = getattr(map_info, "explored_pct", None)
        if server_explored_pct is not None:
            explored_pct = float(server_explored_pct)
        else:
            explored_pct = self._grid_ratio(getattr(map_info, "IsExplored", []))
        visible_pct = self._grid_ratio(getattr(map_info, "IsVisible", []))
        resources = getattr(map_info, "Resources", []) or []
        remaining_resources = sum(sum(row) for row in resources) if resources else 0
        # Store IsExplored grid for query("map_raw") consumers (e.g. ReconJob).
        is_explored = getattr(map_info, "IsExplored", None)
        result = {
            "width": int(getattr(map_info, "MapWidth", 0) or 0),
            "height": int(getattr(map_info, "MapHeight", 0) or 0),
            "visible_pct": round(visible_pct, 4),
            "explored_pct": round(explored_pct, 4),
            "remaining_resources": remaining_resources,
            "timestamp": timestamp,
        }
        if is_explored and is_explored != [[]]:
            result["is_explored"] = is_explored
        return result

    def _normalize_queues(self, queues: Mapping[str, dict[str, Any]], timestamp: float) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for queue_name, queue in queues.items():
            normalized[queue_name] = {
                "queue_type": queue.get("queue_type", queue_name),
                "items": [dict(item) for item in queue.get("items", [])],
                "has_ready_item": bool(queue.get("has_ready_item", False)),
                "timestamp": timestamp,
            }
        return normalized

    def _detect_events(self, previous: WorldState, current: WorldState, timestamp: float) -> list[Event]:
        if previous.timestamp <= 0:
            return []
        if self._is_probable_match_reset(previous, current):
            return [
                Event(
                    type=EventType.GAME_RESET,
                    data={
                        "previous_self_units": len(previous.self_ids),
                        "current_self_units": len(current.self_ids),
                    },
                    timestamp=timestamp,
                )
            ]
        events: list[Event] = []
        events.extend(self._detect_actor_events(previous, current, timestamp))
        events.extend(self._detect_queue_events(previous, current, timestamp))
        events.extend(self._detect_summary_events(current, timestamp))
        events.sort(key=lambda item: item.timestamp)
        return events

    def _is_probable_match_reset(self, previous: WorldState, current: WorldState) -> bool:
        if not previous.self_ids or not current.self_ids:
            return False
        if previous.self_ids & current.self_ids:
            return False

        current_self = [current.actors[actor_id] for actor_id in current.self_ids if actor_id in current.actors]
        previous_self = [previous.actors[actor_id] for actor_id in previous.self_ids if actor_id in previous.actors]
        current_buildings = [actor for actor in current_self if actor.category == ActorCategory.BUILDING]
        current_mcvs = [actor for actor in current_self if actor.category == ActorCategory.MCV]
        previous_had_base = any(actor.category in {ActorCategory.BUILDING, ActorCategory.MCV} for actor in previous_self)

        if not previous_had_base:
            return False
        if current.enemy_ids:
            return False
        if current_buildings:
            return False
        if len(current_mcvs) != 1:
            return False
        if len(current.self_ids) > 3:
            return False
        if len(previous.self_ids) <= len(current.self_ids):
            return False
        return True

    def _detect_actor_events(self, previous: WorldState, current: WorldState, timestamp: float) -> list[Event]:
        events: list[Event] = []
        base_attacked_actor_ids: set[int] = set()

        previous_ids = set(previous.actors)
        current_ids = set(current.actors)
        new_self_buildings = [
            current.actors[actor_id]
            for actor_id in sorted(current_ids - previous_ids)
            if current.actors[actor_id].owner == ActorOwner.SELF
            and current.actors[actor_id].category == ActorCategory.BUILDING
        ]

        for actor_id in sorted(previous_ids - current_ids):
            actor = previous.actors[actor_id]
            if self._is_probable_self_deploy(actor, new_self_buildings):
                continue
            event_type = EventType.UNIT_DIED
            if actor.owner == ActorOwner.SELF and actor.category in {ActorCategory.BUILDING, ActorCategory.MCV}:
                event_type = EventType.STRUCTURE_LOST
                base_attacked_actor_ids.add(actor_id)
            events.append(
                Event(
                    type=event_type,
                    actor_id=actor.actor_id,
                    position=actor.position,
                    data={
                        "owner": actor.owner.value,
                        "name": actor.name,
                        "display_name": actor.display_name,
                        "category": actor.category.value,
                    },
                    timestamp=timestamp,
                )
            )

        for actor_id in sorted(current.enemy_ids - previous.enemy_ids):
            actor = current.actors[actor_id]
            events.append(
                Event(
                    type=EventType.ENEMY_DISCOVERED,
                    actor_id=actor.actor_id,
                    position=actor.position,
                    data={"name": actor.name, "category": actor.category.value},
                    timestamp=timestamp,
                )
            )

        for actor_id in sorted(previous_ids & current_ids):
            old_actor = previous.actors[actor_id]
            new_actor = current.actors[actor_id]
            if new_actor.hp < old_actor.hp:
                damage = old_actor.hp - new_actor.hp
                events.append(
                    Event(
                        type=EventType.UNIT_DAMAGED,
                        actor_id=actor_id,
                        position=new_actor.position,
                        data={
                            "owner": new_actor.owner.value,
                            "name": new_actor.name,
                            "hp_before": old_actor.hp,
                            "hp_after": new_actor.hp,
                            "damage": damage,
                        },
                        timestamp=timestamp,
                    )
                )
                if self._is_probable_base_attack(old_actor, new_actor, current, damage):
                    base_attacked_actor_ids.add(actor_id)

        previous_enemy_buildings = [
            previous.actors[actor_id]
            for actor_id in previous.enemy_ids
            if previous.actors[actor_id].category in {ActorCategory.BUILDING, ActorCategory.MCV}
        ]
        for actor_id in sorted(current.enemy_ids - previous.enemy_ids):
            actor = current.actors[actor_id]
            if actor.category not in {ActorCategory.BUILDING, ActorCategory.MCV} or not previous_enemy_buildings:
                continue
            centroid = self._centroid([item.position for item in previous_enemy_buildings])
            if centroid and self._distance(actor.position, centroid) >= 300:
                events.append(
                    Event(
                        type=EventType.ENEMY_EXPANSION,
                        actor_id=actor.actor_id,
                        position=actor.position,
                        data={"name": actor.name, "distance_from_known_base": round(self._distance(actor.position, centroid), 2)},
                        timestamp=timestamp,
                    )
                )

        if base_attacked_actor_ids:
            sorted_actor_ids = sorted(base_attacked_actor_ids)
            first_actor = current.actors.get(sorted_actor_ids[0]) or previous.actors.get(sorted_actor_ids[0])
            events.append(
                Event(
                    type=EventType.BASE_UNDER_ATTACK,
                    actor_id=first_actor.actor_id if first_actor else None,
                    position=first_actor.position if first_actor else None,
                    data={"actor_ids": sorted_actor_ids},
                    timestamp=timestamp,
                )
            )

        return events

    def _is_probable_self_deploy(
        self,
        actor: NormalizedActor,
        new_self_buildings: Sequence[NormalizedActor],
    ) -> bool:
        if actor.owner != ActorOwner.SELF or actor.category != ActorCategory.MCV:
            return False
        for building in new_self_buildings:
            if self._distance(actor.position, building.position) <= 48:
                return True
        return False

    def _detect_queue_events(self, previous: WorldState, current: WorldState, timestamp: float) -> list[Event]:
        events: list[Event] = []
        previous_done = self._queue_done_state(previous.production_queues)
        current_done = self._queue_done_state(current.production_queues)
        for signature, item in current_done.items():
            if not item.get("done"):
                continue
            if previous_done.get(signature, {}).get("done"):
                continue
            events.append(
                Event(
                    type=EventType.PRODUCTION_COMPLETE,
                    data={
                        "queue_type": item.get("queue_type"),
                        "name": item.get("name"),
                        "display_name": item.get("display_name"),
                        "owner_actor_id": item.get("owner_actor_id"),
                    },
                    timestamp=timestamp,
                )
            )
        return events

    def _detect_summary_events(self, current: WorldState, timestamp: float) -> list[Event]:
        events: list[Event] = []
        self_combat = sum(
            actor.combat_value for actor in current.actors.values() if actor.owner == ActorOwner.SELF and actor.can_attack
        )
        enemy_combat = sum(
            actor.combat_value for actor in current.actors.values() if actor.owner == ActorOwner.ENEMY and actor.can_attack
        )
        frontline_weak = enemy_combat >= max(300.0, self_combat * 1.5)
        if frontline_weak and not self._frontline_weak_active:
            events.append(
                Event(
                    type=EventType.FRONTLINE_WEAK,
                    data={"self_combat_value": round(self_combat, 2), "enemy_combat_value": round(enemy_combat, 2)},
                    timestamp=timestamp,
                )
            )
        self._frontline_weak_active = frontline_weak

        total_credits = float(current.economy.get("total_credits", 0))
        active_queue_items = sum(
            1
            for queue in current.production_queues.values()
            for item in queue.get("items", [])
            if not item.get("done")
        )
        economy_surplus = total_credits >= 4000 and active_queue_items == 0
        if economy_surplus and not self._economy_surplus_active:
            events.append(
                Event(
                    type=EventType.ECONOMY_SURPLUS,
                    data={"total_credits": total_credits},
                    timestamp=timestamp,
                )
            )
        self._economy_surplus_active = economy_surplus

        # Low power detection: fires once on transition to power deficit
        provided = int(current.economy.get("power_provided", 0))
        drained = int(current.economy.get("power_drained", 0))
        low_power = provided > 0 and drained > provided
        if low_power and not self._low_power_active:
            events.append(
                Event(
                    type=EventType.LOW_POWER,
                    data={"power_provided": provided, "power_drained": drained, "deficit": drained - provided},
                    timestamp=timestamp,
                )
            )
        self._low_power_active = low_power
        return events

    def _is_probable_base_attack(
        self,
        old_actor: NormalizedActor,
        new_actor: NormalizedActor,
        current: WorldState,
        damage: int,
    ) -> bool:
        if new_actor.owner != ActorOwner.SELF:
            return False
        if new_actor.category not in {ActorCategory.BUILDING, ActorCategory.MCV}:
            return False
        if old_actor.hp <= 0 or old_actor.hp_max <= 0:
            return False

        damage_pct = (damage / old_actor.hp_max) * 100
        if damage_pct <= BASE_ATTACK_MIN_DAMAGE_PCT:
            return False

        return self._has_nearby_enemy_combat_units(new_actor.position, current)

    def _has_nearby_enemy_combat_units(self, position: tuple[int, int], current: WorldState) -> bool:
        for actor_id in current.enemy_ids:
            actor = current.actors[actor_id]
            if not actor.can_attack:
                continue
            if self._distance(position, actor.position) <= BASE_ATTACK_NEARBY_ENEMY_RADIUS:
                return True
        return False

    def _queue_done_state(self, queues: Mapping[str, dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
        state: dict[tuple[Any, ...], dict[str, Any]] = {}
        for queue_name, queue in queues.items():
            for index, item in enumerate(queue.get("items", [])):
                signature = (
                    queue_name,
                    index,
                    item.get("name"),
                    item.get("owner_actor_id"),
                )
                state[signature] = {
                    "queue_type": queue.get("queue_type", queue_name),
                    "name": item.get("name"),
                    "display_name": item.get("display_name"),
                    "owner_actor_id": item.get("owner_actor_id"),
                    "done": bool(item.get("done")),
                }
        return state

    def _actor_owner(self, faction: Optional[str], default_owner: ActorOwner) -> ActorOwner:
        if faction is None:
            return default_owner
        normalized = str(faction).lower()
        if normalized in {"self", "ally", "自己"}:
            return ActorOwner.SELF
        if normalized in {"enemy", "敌人"}:
            return ActorOwner.ENEMY
        if normalized in {"neutral", "中立"}:
            return ActorOwner.NEUTRAL
        return default_owner

    def _actor_category(self, name: str) -> ActorCategory:
        lowered = name.lower()
        dataset_category = dataset_actor_category_for(name)
        if dataset_category == "mcv":
            return ActorCategory.MCV
        if dataset_category == "harvester":
            return ActorCategory.HARVESTER
        if dataset_category == "building":
            return ActorCategory.BUILDING
        if dataset_category == "infantry":
            return ActorCategory.INFANTRY
        if dataset_category == "vehicle":
            return ActorCategory.VEHICLE
        entry = production_name_entry(name)
        if entry is not None:
            unit_id = entry.unit_id.lower()
            category = entry.category.lower()
            if unit_id == "mcv" or lowered == "mcv" or lowered.endswith("mcv"):
                return ActorCategory.MCV
            if unit_id == "harv" or lowered == "harv":
                return ActorCategory.HARVESTER
            if category in {"building", "defense"}:
                return ActorCategory.BUILDING
            if category == "infantry":
                return ActorCategory.INFANTRY
            if category in {"vehicle", "aircraft", "ship"}:
                return ActorCategory.VEHICLE
        if name == "基地车" or lowered == "mcv" or lowered.endswith("mcv"):
            return ActorCategory.MCV
        if name == "矿车" or lowered == "harv":
            return ActorCategory.HARVESTER
        category = DEFAULT_UNIT_CATEGORY_RULES.get(name)
        if category in {"vehicle", "air"}:
            return ActorCategory.VEHICLE
        if category in {"infantry", "support"}:
            return ActorCategory.INFANTRY
        if category in {"building", "defense"}:
            return ActorCategory.BUILDING
        if category == "harvester":
            return ActorCategory.HARVESTER
        if category == "mcv":
            return ActorCategory.MCV
        if lowered.endswith("tnk") or lowered in {"ftrk"}:
            return ActorCategory.VEHICLE
        if lowered.startswith("e") and lowered[1:].isdigit():
            return ActorCategory.INFANTRY
        if lowered in {"powr", "proc", "weap", "barr", "afld", "stek", "fix", "dome"}:
            return ActorCategory.BUILDING
        if lowered in {"harv", "矿车"}:
            return ActorCategory.HARVESTER
        return ActorCategory.VEHICLE

    def _mobility(self, name: str, category: ActorCategory) -> Mobility:
        lowered = name.lower()
        if category == ActorCategory.BUILDING:
            return Mobility.STATIC
        if category == ActorCategory.MCV:
            return Mobility.SLOW
        if lowered in FAST_NAMES:
            return Mobility.FAST
        if lowered in SLOW_NAMES or category == ActorCategory.HARVESTER:
            return Mobility.SLOW
        return Mobility.MEDIUM

    def _combat_value(self, name: str, category: ActorCategory) -> float:
        unit_id = production_name_unit_id(name) or name.lower()
        if category == ActorCategory.BUILDING and unit_id not in {"sam", "agun", "gun", "hbox", "pbox", "tsla", "ftur"}:
            return float(DEFAULT_UNIT_VALUE_WEIGHTS.get(name, 80))
        if category == ActorCategory.HARVESTER:
            return float(DEFAULT_UNIT_VALUE_WEIGHTS.get(name, 50))
        return float(DEFAULT_UNIT_VALUE_WEIGHTS.get(name, 100))

    def _can_attack(self, name: str, category: ActorCategory) -> bool:
        unit_id = production_name_unit_id(name) or name.lower()
        if category == ActorCategory.HARVESTER:
            return False
        if category == ActorCategory.MCV:
            return False
        if category == ActorCategory.BUILDING:
            if unit_id in {"sam", "agun", "gun", "hbox", "pbox", "tsla", "ftur"}:
                return True
            return name in DEFENSIVE_BUILDING_NAMES
        return True

    def _weapon_range(self, name: str, category: ActorCategory, can_attack: bool) -> int:
        if not can_attack:
            return 0
        if category == ActorCategory.BUILDING:
            return 8
        if name.lower() in {"v2", "v2rl"}:
            return 10
        if category == ActorCategory.INFANTRY:
            return 4
        return 6

    def _is_idle(self, activity: Optional[str], order: Optional[str]) -> bool:
        activity_text = str(activity or "").lower()
        order_text = str(order or "").lower()
        if not activity_text and not order_text:
            return True
        busy_markers = ("move", "attack", "harvest", "repair", "build", "produce", "deploy")
        return not any(marker in activity_text or marker in order_text for marker in busy_markers)

    def _location_to_tuple(self, location: Any) -> tuple[int, int]:
        if isinstance(location, Location):
            return (int(location.x), int(location.y))
        if isinstance(location, (tuple, list)) and len(location) == 2:
            return (int(location[0]), int(location[1]))
        return (0, 0)

    def _grid_ratio(self, grid: Sequence[Sequence[bool]]) -> float:
        total = 0
        visible = 0
        for row in grid:
            total += len(row)
            visible += sum(1 for cell in row if cell)
        return (visible / total) if total else 0.0

    def _centroid(self, positions: Sequence[tuple[int, int]]) -> Optional[tuple[int, int]]:
        if not positions:
            return None
        avg_x = round(sum(position[0] for position in positions) / len(positions))
        avg_y = round(sum(position[1] for position in positions) / len(positions))
        return (avg_x, avg_y)

    def _distance(self, left: tuple[int, int], right: tuple[int, int]) -> float:
        return math.dist((float(left[0]), float(left[1])), (float(right[0]), float(right[1])))

    def _actor_to_dict(self, actor: Optional[NormalizedActor]) -> Optional[dict[str, Any]]:
        if actor is None:
            return None
        return {
            "actor_id": actor.actor_id,
            "name": actor.name,
            "display_name": actor.display_name,
            "owner": actor.owner.value,
            "category": actor.category.value,
            "position": list(actor.position),
            "hp": actor.hp,
            "hp_max": actor.hp_max,
            "is_alive": actor.is_alive,
            "is_idle": actor.is_idle,
            "mobility": actor.mobility.value,
            "combat_value": actor.combat_value,
            "can_attack": actor.can_attack,
            "can_harvest": actor.can_harvest,
            "weapon_range": actor.weapon_range,
            "is_disabled": actor.is_disabled,
            "is_powered_down": actor.is_powered_down,
            "has_low_power": actor.has_low_power,
            "has_power_outage": actor.has_power_outage,
            "disabled_reason": actor.disabled_reason,
            "timestamp": actor.timestamp,
        }

    def _constraint_to_dict(self, constraint: Constraint) -> dict[str, Any]:
        payload = asdict(constraint)
        payload["enforcement"] = constraint.enforcement.value
        return payload

    def _event_to_dict(self, event: Event) -> dict[str, Any]:
        payload = {
            "type": event.type.value,
            "timestamp": event.timestamp,
            "data": dict(event.data),
        }
        if event.actor_id is not None:
            payload["actor_id"] = event.actor_id
        if event.position is not None:
            payload["position"] = list(event.position)
        return payload
