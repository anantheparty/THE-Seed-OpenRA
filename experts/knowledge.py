"""Structured OpenRA RA knowledge used directly by Experts and Planners."""

from __future__ import annotations

from typing import Any, Protocol

from openra_api.production_names import production_name_matches


class ProductionCapabilityAPI(Protocol):
    def can_produce(self, unit_type: str) -> bool:
        ...


_KNOWLEDGE_ROWS: tuple[dict[str, Any], ...] = (
    {
        "names": ("powr", "发电厂"),
        "roles": ("power_recovery",),
        "downstream_unlocks": ("anypower",),
    },
    {
        "names": ("apwr", "大电厂"),
        "roles": ("power_recovery", "tech_gateway"),
        "downstream_unlocks": ("anypower",),
    },
    {
        "names": ("proc", "矿场", "精炼厂", "矿石精炼厂"),
        "roles": ("economy_anchor", "expansion_anchor"),
        "downstream_unlocks": ("harv", "weap", "dome"),
        "economic_effects": ("free_harvester", "refinery_dock", "resource_storage"),
    },
    {
        "names": ("harv", "矿车", "采矿车"),
        "roles": ("resource_collection",),
        "downstream_unlocks": (),
    },
    {
        "names": ("weap", "战车工厂", "坦克厂"),
        "roles": ("vehicle_gateway", "tech_gateway"),
        "downstream_unlocks": ("fix", "vehicle_production", "mobile_scout_transition", "armor_play"),
    },
    {
        "names": ("fix", "维修厂", "修理厂"),
        "roles": ("repair_gateway", "tech_gateway"),
        "downstream_unlocks": ("mcv",),
    },
    {
        "names": ("mcv", "基地车"),
        "roles": ("expansion_anchor",),
        "downstream_unlocks": ("construction_yard",),
    },
    {
        "names": ("dome", "雷达站", "雷达"),
        "roles": ("awareness_gateway", "tech_gateway"),
        "downstream_unlocks": ("apwr", "agun", "afld", "atek", "stek"),
        "awareness_effects": ("radar_minimap", "online_shroud_reveal", "offline_local_reveal"),
    },
    {
        "names": ("barr", "兵营"),
        "roles": ("infantry_gateway",),
        "downstream_unlocks": ("infantry_production",),
    },
    {
        "names": ("tent", "盟军兵营"),
        "roles": ("infantry_gateway",),
        "downstream_unlocks": ("infantry_production",),
    },
)

# --- Soft strategy knowledge ---

# Standard opening build sequences (power → barracks → refinery → war_factory).
# Both factions share the same base order in RA; faction-specific divergence
# happens post-refinery (e.g. Soviets favour flamethrower/heavy tank paths).
# TODO: verify Soviet-specific early deviations against actual game rules.
OPENING_BUILD_ORDER: dict[str, list[dict[str, str]]] = {
    "allied": [
        {"unit_type": "powr", "queue_type": "Building", "reason": "power_first"},
        {"unit_type": "barr", "queue_type": "Building", "reason": "infantry_gateway"},
        {"unit_type": "proc", "queue_type": "Building", "reason": "economy_foundation"},
        {"unit_type": "weap", "queue_type": "Building", "reason": "vehicle_gateway"},
    ],
    "soviet": [
        {"unit_type": "powr", "queue_type": "Building", "reason": "power_first"},
        {"unit_type": "barr", "queue_type": "Building", "reason": "infantry_gateway"},
        {"unit_type": "proc", "queue_type": "Building", "reason": "economy_foundation"},
        {"unit_type": "weap", "queue_type": "Building", "reason": "vehicle_gateway"},
    ],
}

# Tech prerequisites: must have these buildings before building the key.
# TODO: cross-check advanced tech tree against ra/rules/structures.yaml.
_TECH_PREREQUISITES: dict[str, list[dict[str, str]]] = {
    "weap": [
        {"unit_type": "proc", "reason": "economy_required_before_vehicle_gateway"},
    ],
    "dome": [
        {"unit_type": "proc", "reason": "economy_required_before_radar"},
        {"unit_type": "barr", "reason": "infantry_gateway_required_before_radar"},
    ],
    "agun": [
        {"unit_type": "proc", "reason": "economy_required_before_advanced_defense"},
    ],
    "sam": [
        {"unit_type": "proc", "reason": "economy_required_before_sam"},
    ],
    "atek": [
        {"unit_type": "weap", "reason": "vehicle_gateway_required_before_tech_center"},
        {"unit_type": "dome", "reason": "radar_required_before_tech_center"},
    ],
    "stek": [
        {"unit_type": "weap", "reason": "vehicle_gateway_required_before_tech_center"},
        {"unit_type": "dome", "reason": "radar_required_before_tech_center"},
    ],
}

# Counter-unit table: enemy category composition → recommended counter.
# Evaluated in order; first matching rule wins.
# TODO: verify exact counter relationships against RA balance data.
_COUNTER_TABLE: tuple[dict[str, Any], ...] = (
    {
        "enemy_category": "aircraft",
        "threshold_ratio": 0.3,
        "counter_unit": "sam",
        "counter_queue": "Building",
        "reason": "air_threat_counter_sam",
        "display_name": "防空导弹",
    },
    {
        "enemy_category": "infantry",
        "threshold_ratio": 0.6,
        "counter_unit": "e4",   # rocket soldier — TODO: confirm unit ID
        "counter_queue": "Infantry",
        "reason": "infantry_heavy_counter_rocket",
        "display_name": "火箭兵",
    },
    {
        "enemy_category": "vehicle",
        "threshold_ratio": 0.5,
        "counter_unit": "arti",  # artillery — TODO: confirm unit ID
        "counter_queue": "Vehicle",
        "reason": "vehicle_heavy_counter_artillery",
        "display_name": "火炮",
    },
)

# Placement hints keyed by unit_type.
_PLACEMENT_HINTS: dict[str, dict[str, str]] = {
    "proc":  {"near": "ore_field",      "reason": "refinery_near_ore_minimizes_travel"},
    "harv":  {"near": "ore_field",      "reason": "harvester_spawns_near_refinery_dock"},
    "powr":  {"near": "base_center",    "reason": "power_plant_central_for_adjacency"},
    "apwr":  {"near": "base_center",    "reason": "advanced_power_central"},
    "agun":  {"near": "base_perimeter", "reason": "anti_ground_covers_approach"},
    "sam":   {"near": "base_perimeter", "reason": "anti_air_covers_airspace"},
    "pbox":  {"near": "base_perimeter", "reason": "pillbox_covers_infantry_approach"},
    "dome":  {"near": "base_center",    "reason": "radar_protected_centrally"},
    "weap":  {"near": "base_interior",  "reason": "war_factory_protected_from_rush"},
    "barr":  {"near": "base_interior",  "reason": "barracks_protected_from_rush"},
    "tent":  {"near": "base_interior",  "reason": "allied_barracks_protected"},
}


def opening_build_order(faction: str = "allied") -> list[dict[str, str]]:
    """Return the standard opening build sequence for the given faction."""
    key = faction.lower()
    return list(OPENING_BUILD_ORDER.get(key, OPENING_BUILD_ORDER["allied"]))


def tech_prerequisites_for(unit_type: str) -> list[dict[str, str]]:
    """Return required buildings that should exist before constructing unit_type."""
    return list(_TECH_PREREQUISITES.get((unit_type or "").lower(), []))


def counter_recommendation(enemy_actors: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Recommend a counter unit based on enemy composition.

    Returns a recommendation dict or None if no clear counter is identified.
    """
    if not enemy_actors:
        return None
    total = len(enemy_actors)
    category_counts: dict[str, int] = {}
    for actor in enemy_actors:
        cat = str(actor.get("category") or "unknown").lower()
        category_counts[cat] = category_counts.get(cat, 0) + 1
    for rule in _COUNTER_TABLE:
        cat = rule["enemy_category"]
        ratio = category_counts.get(cat, 0) / total
        if ratio >= rule["threshold_ratio"]:
            return {
                "unit_type": rule["counter_unit"],
                "queue_type": rule["counter_queue"],
                "display_name": rule["display_name"],
                "reason": rule["reason"],
                "enemy_ratio": round(ratio, 2),
            }
    return None


def placement_hint_for(unit_type: str) -> dict[str, str] | None:
    """Return a placement suggestion for the given building type, or None."""
    return _PLACEMENT_HINTS.get((unit_type or "").lower())


LOW_POWER_DISABLE_CLASSES = {
    "low_power": ["ATEK"],
    "low_power_or_powerdown": ["DOME", "TSLA", "AGUN", "SAM"],
    "power_outage_only": ["POWR", "APWR"],
}


def queue_scope_for(queue_type: str | None) -> str:
    if str(queue_type or "").strip():
        return "player_shared"
    return "none"


def knowledge_for_target(unit_type: str | None, queue_type: str | None) -> dict[str, Any]:
    roles: list[str] = []
    downstream_unlocks: list[str] = []
    economic_effects: list[str] = []
    awareness_effects: list[str] = []
    for row in _KNOWLEDGE_ROWS:
        names = row["names"]
        if any(production_name_matches(unit_type, alias, alias) for alias in names):
            roles.extend(row.get("roles", ()))
            downstream_unlocks.extend(row.get("downstream_unlocks", ()))
            economic_effects.extend(row.get("economic_effects", ()))
            awareness_effects.extend(row.get("awareness_effects", ()))
    return {
        "queue_scope": queue_scope_for(queue_type),
        "roles": list(dict.fromkeys(roles)),
        "downstream_unlocks": list(dict.fromkeys(downstream_unlocks)),
        "economic_effects": list(dict.fromkeys(economic_effects)),
        "awareness_effects": list(dict.fromkeys(awareness_effects)),
    }


def has_role(actor: dict[str, Any], role: str) -> bool:
    knowledge = knowledge_for_target(
        actor.get("name") or actor.get("display_name"),
        "Building" if actor.get("category") == "building" else None,
    )
    return role in knowledge.get("roles", [])


def buildable_power_recovery_options(game_api: ProductionCapabilityAPI) -> list[dict[str, str]]:
    return _buildable_options(
        game_api,
        (
            ("powr", "发电厂"),
            ("apwr", "高级发电厂"),
        ),
    )


def buildable_economy_recovery_options(game_api: ProductionCapabilityAPI) -> list[dict[str, str]]:
    return _buildable_options(
        game_api,
        (
            ("proc", "矿场"),
            ("harv", "采矿车"),
        ),
    )


def low_power_impacts() -> dict[str, Any]:
    return {
        "kind": "power_state",
        "effects": ["queue_slowdown", "structure_disable_possible"],
        "disable_classes": LOW_POWER_DISABLE_CLASSES,
    }


def radar_loss_impact() -> dict[str, Any]:
    return {
        "kind": "awareness_loss",
        "effects": ["minimap_unavailable", "reduced_local_awareness"],
    }


def awareness_recovery_package() -> dict[str, Any]:
    return {
        "kind": "awareness_recovery",
        "options": [{"unit_type": "dome", "display_name": "雷达站"}],
    }


def recon_first_recommendation(*, search_region: str = "enemy_half", target_type: str = "base") -> dict[str, Any]:
    return {
        "kind": "recon_first",
        "expert_type": "ReconExpert",
        "config_hint": {
            "search_region": search_region,
            "target_type": target_type,
            "target_owner": "enemy",
        },
    }


def has_awareness_gateway(actors: list[dict[str, Any]]) -> bool:
    return any(has_role(actor, "awareness_gateway") for actor in actors)


def _buildable_options(
    game_api: ProductionCapabilityAPI,
    candidates: tuple[tuple[str, str], ...],
) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for unit_type, display_name in candidates:
        try:
            if game_api.can_produce(unit_type):
                options.append({"unit_type": unit_type, "display_name": display_name})
        except Exception:
            continue
    return options
