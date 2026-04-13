from typing import Dict, Any, List, Tuple
import math

from openra_api.models import ActorCategory
from openra_state.data.combat_data import get_unit_combat_info
from openra_state.intel.clustering import SpatialClustering


class DisadvantageAssessor:
    """
    An independent Information Expert module designed to assess whether the player
    is in a tactical or strategic disadvantage compared to the enemy.
    
    It outputs three specific disadvantage warnings:
    1. Global Disadvantage: Evaluated by comparing the total combat score of all friendly 
       vs enemy units using openra_state.data.combat_data.
    2. Local Disadvantage: Evaluated by clustering friendly units into squads using DBSCAN, 
       then comparing the combat score of the squad against nearby enemies.
    3. Economy Disadvantage: Evaluated by checking if safe resource zones are depleted 
       compared to the number of friendly harvesters/refineries.
    """

    # Global Combat Thresholds
    _GLOBAL_CRITICAL_RATIO = 3.0  # Enemy total score >= 3.0x friendly total score
    _GLOBAL_CRITICAL_DIFF = 20.0  # Enemy total score - friendly total score >= 20.0

    # Local Squad Thresholds
    _SQUAD_DBSCAN_EPS = 15.0      # Radius for clustering friendly squads
    _SQUAD_DBSCAN_MIN = 2         # Minimum units to form a squad
    _SQUAD_THREAT_RADIUS = 25.0   # Radius around squad center to search for enemies
    _LOCAL_CRITICAL_RATIO = 2.5   # Enemy local score >= 2.5x squad score
    _LOCAL_CRITICAL_DIFF = 15.0   # Enemy local score - squad score >= 15.0

    # Economy Thresholds
    _MIN_RESOURCE_PER_HARVESTER = 5.0 # If safe resource value < this * harvesters, it's a shortage

    def __init__(self):
        self.name = "DisadvantageAssessor"

    def analyze(self, world_state: Any, runtime_facts: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze the current world state to generate disadvantage warnings.
        """
        disadvantage_level = "none"
        warnings: List[str] = []
        is_disadvantaged = False

        friendly_combat_units = self._get_combat_units(world_state, owner="self")
        enemy_combat_units = self._get_combat_units(world_state, owner="enemy")

        # 1. Global Disadvantage Evaluation (Based on Combat Score)
        global_warn, global_msg = self._evaluate_global_disadvantage(friendly_combat_units, enemy_combat_units)
        if global_warn:
            is_disadvantaged = True
            disadvantage_level = "critical"
            warnings.append(global_msg)

        # 2. Local Squad Disadvantage Evaluation (DBSCAN Clustering)
        local_warn, local_msgs = self._evaluate_local_disadvantage(friendly_combat_units, enemy_combat_units)
        if local_warn:
            is_disadvantaged = True
            # Elevate to high if not already critical
            if disadvantage_level == "none":
                disadvantage_level = "high"
            warnings.extend(local_msgs)

        # 3. Economy Shortage Evaluation (Using ZoneManager)
        zone_manager = runtime_facts.get("zone_manager")
        econ_warn, econ_msg = self._evaluate_economy_disadvantage(world_state, zone_manager)
        if econ_warn:
            is_disadvantaged = True
            if disadvantage_level == "none":
                disadvantage_level = "high"
            warnings.append(econ_msg)

        return {
            "is_disadvantaged": is_disadvantaged,
            "disadvantage_level": disadvantage_level,
            "warnings": warnings,
        }

    def _get_combat_units(self, world_state: Any, owner: str) -> List[Any]:
        """
        Filter actors to extract only mobile combat units.
        Explicitly excludes Buildings, Defenses, Harvesters, MCVs, e6, husk, and mpspawn.
        """
        combat_units = []
        NON_COMBAT_TYPES = {"e6", "husk", "mpspawn"}
        
        for actor in world_state.actors.values():
            if actor.owner != owner:
                continue
            if getattr(actor, 'type', '').lower() in NON_COMBAT_TYPES:
                continue
            if actor.category in (ActorCategory.INFANTRY, ActorCategory.VEHICLE):
                if getattr(actor, 'can_attack', True):
                    combat_units.append(actor)
        return combat_units

    def _calculate_combat_score(self, units: List[Any]) -> float:
        """Calculate the total combat score for a list of units based on openra_state data."""
        total_score = 0.0
        for u in units:
            unit_type = getattr(u, 'type', '')
            _, score = get_unit_combat_info(unit_type)
            total_score += score
        return total_score

    def _evaluate_global_disadvantage(self, friendly_units: List[Any], enemy_units: List[Any]) -> Tuple[bool, str]:
        friendly_score = self._calculate_combat_score(friendly_units)
        enemy_score = self._calculate_combat_score(enemy_units)

        ratio = enemy_score / max(1.0, friendly_score)
        diff = enemy_score - friendly_score

        if ratio >= self._GLOBAL_CRITICAL_RATIO and diff >= self._GLOBAL_CRITICAL_DIFF:
            msg = (f"[GLOBAL INFERIORITY] Enemy global combat score ({enemy_score:.1f}) "
                   f"severely outweighs ours ({friendly_score:.1f}). Ratio: {ratio:.1f}x.")
            return True, msg
        return False, ""

    def _evaluate_local_disadvantage(self, friendly_units: List[Any], enemy_units: List[Any]) -> Tuple[bool, List[str]]:
        if not friendly_units or not enemy_units:
            return False, []

        warnings = []
        # Cluster friendly units into squads
        squads = SpatialClustering.cluster_units_dbscan(
            friendly_units, eps=self._SQUAD_DBSCAN_EPS, min_samples=self._SQUAD_DBSCAN_MIN
        )

        for i, squad in enumerate(squads):
            squad_score = self._calculate_combat_score(squad)
            
            # Calculate center of mass for the squad
            cx = sum(u.position.x for u in squad) / len(squad)
            cy = sum(u.position.y for u in squad) / len(squad)

            # Find enemies near this squad
            nearby_enemies = []
            for eu in enemy_units:
                dist = math.hypot(eu.position.x - cx, eu.position.y - cy)
                if dist <= self._SQUAD_THREAT_RADIUS:
                    nearby_enemies.append(eu)
            
            if not nearby_enemies:
                continue

            enemy_local_score = self._calculate_combat_score(nearby_enemies)
            ratio = enemy_local_score / max(1.0, squad_score)
            diff = enemy_local_score - squad_score

            if ratio >= self._LOCAL_CRITICAL_RATIO and diff >= self._LOCAL_CRITICAL_DIFF:
                warnings.append(
                    f"[LOCAL INFERIORITY] Squad #{i+1} at ({int(cx)}, {int(cy)}) is outmatched! "
                    f"Squad score: {squad_score:.1f}, Nearby enemy score: {enemy_local_score:.1f}."
                )
        
        return len(warnings) > 0, warnings

    def _evaluate_economy_disadvantage(self, world_state: Any, zone_manager: Any) -> Tuple[bool, str]:
        if not zone_manager:
            return False, ""

        harvester_count = 0
        for actor in world_state.actors.values():
            if actor.owner == "self" and actor.category == ActorCategory.HARVESTER:
                harvester_count += 1
                
        if harvester_count == 0:
            return False, "" # Cannot evaluate resource shortage if we have no harvesters to begin with

        my_safe_resource_value = 0.0
        for zone in zone_manager.zones.values():
            if zone.resource_value <= 0:
                continue
            
            # A zone is considered safe/ours if we have structures there, or if enemies are absent
            has_enemy = bool(zone.enemy_structures) or zone.enemy_strength > 0
            has_me = bool(zone.my_structures) or zone.my_strength > 0
            
            if zone.owner_faction == "MY" or (has_me and not has_enemy):
                my_safe_resource_value += zone.resource_value

        required_value = harvester_count * self._MIN_RESOURCE_PER_HARVESTER
        if my_safe_resource_value < required_value:
            msg = (f"[ECONOMY SHORTAGE] Safe resource zones are depleted! "
                   f"Safe value: {my_safe_resource_value:.1f}, "
                   f"Required for {harvester_count} harvesters: {required_value:.1f}.")
            return True, msg

        return False, ""
