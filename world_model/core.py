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
from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo, TargetsQueryParam


QUEUE_TYPES = ("Building", "Defense", "Infantry", "Vehicle", "Aircraft")
DEFENSIVE_BUILDING_NAMES = {"防空炮", "哨戒炮", "sam", "agun", "gun", "hbox", "pbox", "tsla", "ftur"}
FAST_NAMES = {"dog", "吉普车", "jeep", "bike", "矿车"}
SLOW_NAMES = {"猛犸坦克", "mamm", "v2", "v2rl"}
BASE_ATTACK_MIN_DAMAGE_PCT = 5
BASE_ATTACK_NEARBY_ENEMY_RADIUS = 200
VEHICLE_CODES = {"2tnk", "1tnk", "3tnk", "4tnk", "harv", "jeep", "arty", "apc", "mamm", "ttnk", "v2rl"}
INFANTRY_CODES = {"e1", "e2", "e3", "e4", "dog", "engi", "medi"}
BUILDING_CODES = {
    "powr",
    "apwr",
    "proc",
    "weap",
    "tent",
    "barr",
    "afld",
    "atek",
    "stek",
    "fix",
    "silo",
    "hbox",
    "pbox",
    "gun",
    "sam",
    "agun",
    "kenn",
}

logger = logging.getLogger(__name__)
slog = get_logger("world_model")


class WorldModelSource(Protocol):
    """Fetches raw game state for the WorldModel."""

    def fetch_self_actors(self) -> list[Actor]:
        ...

    def fetch_enemy_actors(self) -> list[Actor]:
        ...

    def fetch_economy(self) -> Optional[PlayerBaseInfo]:
        ...

    def fetch_map(self) -> Optional[MapQueryResult]:
        ...

    def fetch_production_queues(self) -> dict[str, dict[str, Any]]:
        ...


@dataclass(slots=True)
class RefreshPolicy:
    actors_s: float = 0.1
    economy_s: float = 0.5
    map_s: float = 1.0


@dataclass(slots=True)
class WorldState:
    actors: dict[int, NormalizedActor] = field(default_factory=dict)
    self_ids: set[int] = field(default_factory=set)
    enemy_ids: set[int] = field(default_factory=set)
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

    def fetch_economy(self) -> Optional[PlayerBaseInfo]:
        return self.api.player_base_info_query()

    def fetch_map(self) -> Optional[MapQueryResult]:
        return self.api.map_query()

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
    ) -> None:
        self.source = source
        self.refresh_policy = refresh_policy or RefreshPolicy()
        self.event_history_limit = event_history_limit
        self.stale_failure_threshold = stale_failure_threshold

        self.state = WorldState(timestamp=0.0)
        self.active_tasks: dict[str, Any] = {}
        self.active_jobs: dict[str, Any] = {}
        self.resource_bindings: dict[str, str] = {}
        self.constraints: dict[str, Constraint] = {}

        self._last_actor_refresh = 0.0
        self._last_economy_refresh = 0.0
        self._last_map_refresh = 0.0
        self._pending_events: list[Event] = []
        self._event_history: list[Event] = []
        self._last_refresh_layers: list[str] = []
        self._frontline_weak_active = False
        self._economy_surplus_active = False
        self._consecutive_refresh_failures = 0
        self._total_refresh_failures = 0
        self._last_refresh_error: Optional[str] = None

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
        if "actors" in layers:
            try:
                self_actors = self.source.fetch_self_actors()
                enemy_actors = self.source.fetch_enemy_actors()
                normalized = self._normalize_actors(self_actors, enemy_actors, timestamp)
                self.state.actors = normalized["actors"]
                self.state.self_ids = normalized["self_ids"]
                self.state.enemy_ids = normalized["enemy_ids"]
                self._last_actor_refresh = timestamp
            except Exception as exc:
                stale = True
                refresh_errors.append(f"actors:{exc}")
                logger.warning("WorldModel actor refresh failed: %s", exc)
                slog.warn("WorldModel actor refresh failed", event="world_refresh_failed", layer="actors", error=str(exc))

        if "economy" in layers:
            try:
                economy = self._normalize_economy(self.source.fetch_economy(), timestamp)
                queues = self._normalize_queues(self.source.fetch_production_queues(), timestamp)
                self.state.economy = economy
                self.state.production_queues = queues
                self._last_economy_refresh = timestamp
            except Exception as exc:
                stale = True
                refresh_errors.append(f"economy:{exc}")
                logger.warning("WorldModel economy refresh failed: %s", exc)
                slog.warn("WorldModel economy refresh failed", event="world_refresh_failed", layer="economy", error=str(exc))

        if "map" in layers:
            try:
                self.state.map_info = self._normalize_map(self.source.fetch_map(), timestamp)
                self._last_map_refresh = timestamp
            except Exception as exc:
                stale = True
                refresh_errors.append(f"map:{exc}")
                logger.warning("WorldModel map refresh failed: %s", exc)
                slog.warn("WorldModel map refresh failed", event="world_refresh_failed", layer="map", error=str(exc))

        if stale:
            self._consecutive_refresh_failures += 1
            self._total_refresh_failures += 1
            self._last_refresh_error = "; ".join(refresh_errors) if refresh_errors else "unknown refresh failure"
        else:
            self._consecutive_refresh_failures = 0
            self._last_refresh_error = None

        self.state.timestamp = timestamp
        self.state.stale = stale
        events = self._detect_events(previous, self.state, timestamp)
        self._pending_events = list(events)
        self._event_history.extend(events)
        if len(self._event_history) > self.event_history_limit:
            self._event_history = self._event_history[-self.event_history_limit :]
        self._last_refresh_layers = layers
        slog.info(
            "WorldModel refresh completed",
            event="world_refresh_completed",
            layers=layers,
            stale=stale,
            event_count=len(events),
            timestamp=timestamp,
            consecutive_failures=self._consecutive_refresh_failures,
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
                name=params.get("name"),
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
            if name and actor.name != name and actor.display_name != name:
                continue
            if near is not None and max_distance is not None:
                if self._distance(actor.position, near) > max_distance:
                    continue
            matched.append(actor)
        matched.sort(key=lambda item: item.actor_id)
        return matched

    def world_summary(self) -> dict[str, Any]:
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
                "queue_blocked": any(
                    queue.get("has_ready_item") or any(item.get("paused") for item in queue.get("items", []))
                    for queue in self.state.production_queues.values()
                ),
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
            "map": dict(self.state.map_info),
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
            },
            "timestamp": self.state.timestamp,
            "stale": self.state.stale,
        }
        return summary

    def runtime_state(self) -> dict[str, Any]:
        return {
            "active_tasks": dict(self.active_tasks),
            "active_jobs": dict(self.active_jobs),
            "resource_bindings": dict(self.resource_bindings),
            "constraints": [self._constraint_to_dict(item) for item in self.constraints.values()],
            "timestamp": self.state.timestamp,
        }

    def set_runtime_state(
        self,
        *,
        active_tasks: Optional[dict[str, Any]] = None,
        active_jobs: Optional[dict[str, Any]] = None,
        resource_bindings: Optional[dict[str, str]] = None,
        constraints: Optional[Sequence[Constraint]] = None,
    ) -> None:
        if active_tasks is not None:
            self.active_tasks = dict(active_tasks)
        if active_jobs is not None:
            self.active_jobs = dict(active_jobs)
        if resource_bindings is not None:
            self.resource_bindings = dict(resource_bindings)
        if constraints is not None:
            self.constraints = {item.constraint_id: item for item in constraints}

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
            "consecutive_failures": self._consecutive_refresh_failures,
            "total_failures": self._total_refresh_failures,
            "last_error": self._last_refresh_error,
            "failure_threshold": self.stale_failure_threshold,
            "timestamp": self.state.timestamp,
        }

    def _due_layers(self, now: float, force: bool) -> list[str]:
        layers: list[str] = []
        if force or not self.state.actors or now - self._last_actor_refresh >= self.refresh_policy.actors_s:
            layers.append("actors")
        if force or not self.state.economy or now - self._last_economy_refresh >= self.refresh_policy.economy_s:
            layers.append("economy")
        if force or not self.state.map_info or now - self._last_map_refresh >= self.refresh_policy.map_s:
            layers.append("map")
        return layers

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
            "low_power": provided > 0 and power <= max(0, provided * 0.1),
            "timestamp": timestamp,
        }

    def _normalize_map(self, map_info: Optional[MapQueryResult], timestamp: float) -> dict[str, Any]:
        if map_info is None:
            return {"width": 0, "height": 0, "explored_pct": 0.0, "visible_pct": 0.0, "timestamp": timestamp}
        visible_pct = self._grid_ratio(getattr(map_info, "IsVisible", []))
        explored_pct = self._grid_ratio(getattr(map_info, "IsExplored", []))
        resources = getattr(map_info, "Resources", []) or []
        remaining_resources = sum(sum(row) for row in resources) if resources else 0
        return {
            "width": int(getattr(map_info, "MapWidth", 0) or 0),
            "height": int(getattr(map_info, "MapHeight", 0) or 0),
            "visible_pct": round(visible_pct, 4),
            "explored_pct": round(explored_pct, 4),
            "remaining_resources": remaining_resources,
            "timestamp": timestamp,
        }

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
        events: list[Event] = []
        events.extend(self._detect_actor_events(previous, current, timestamp))
        events.extend(self._detect_queue_events(previous, current, timestamp))
        events.extend(self._detect_summary_events(current, timestamp))
        events.sort(key=lambda item: item.timestamp)
        return events

    def _detect_actor_events(self, previous: WorldState, current: WorldState, timestamp: float) -> list[Event]:
        events: list[Event] = []
        base_attacked_actor_ids: set[int] = set()

        previous_ids = set(previous.actors)
        current_ids = set(current.actors)

        for actor_id in sorted(previous_ids - current_ids):
            actor = previous.actors[actor_id]
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
        lowered = name.lower()
        if lowered == "mcv" or lowered.endswith("mcv"):
            return ActorCategory.MCV
        if lowered in VEHICLE_CODES or lowered.endswith("tnk"):
            return ActorCategory.VEHICLE
        if lowered in INFANTRY_CODES or lowered.startswith("e"):
            return ActorCategory.INFANTRY
        if lowered in BUILDING_CODES:
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
        if category == ActorCategory.BUILDING and name not in DEFENSIVE_BUILDING_NAMES:
            return float(DEFAULT_UNIT_VALUE_WEIGHTS.get(name, 80))
        if category == ActorCategory.HARVESTER:
            return float(DEFAULT_UNIT_VALUE_WEIGHTS.get(name, 50))
        return float(DEFAULT_UNIT_VALUE_WEIGHTS.get(name, 100))

    def _can_attack(self, name: str, category: ActorCategory) -> bool:
        if category == ActorCategory.HARVESTER:
            return False
        if category == ActorCategory.MCV:
            return False
        if category == ActorCategory.BUILDING:
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
