from typing import Dict, Any, List, Optional
from openra_api.models import ActorCategory

class DisadvantageAssessor:
    """
    An independent Information Expert module designed to assess whether the player
    is in a tactical or strategic disadvantage compared to the enemy.
    
    Unlike standard threat detection (which merely counts visible enemies), this
    assessor evaluates the relative strength by comparing friendly vs enemy combat units.
    """

    # Parameters for evaluating Combat Unit Ratio Disadvantage
    # A disadvantage is declared only if BOTH ratio and absolute difference conditions are met
    # to avoid false positives in early-game scouting (e.g., 2 enemy scouts vs 0 friendly).
    _CRITICAL_RATIO_THRESHOLD = 3.0
    _CRITICAL_DIFF_THRESHOLD = 10
    
    _HIGH_RATIO_THRESHOLD = 1.5
    _HIGH_DIFF_THRESHOLD = 5

    def __init__(self):
        self.name = "DisadvantageAssessor"

    def analyze(self, world_state: Any, runtime_facts: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze the current world state to determine if the player is at a disadvantage.
        
        Args:
            world_state: The current WorldState containing all actors.
            runtime_facts: Derived runtime facts from the world model.
            
        Returns:
            Dict containing the disadvantage signal and detailed reasoning.
        """
        disadvantage_level = "none"
        disadvantage_reasons: List[str] = []
        is_disadvantaged = False

        # 1. Filter pure combat units (excluding buildings, defenses, harvesters, MCVs)
        friendly_combat_units = self._get_combat_units(world_state, owner="self")
        enemy_combat_units = self._get_combat_units(world_state, owner="enemy")

        friendly_count = len(friendly_combat_units)
        enemy_count = len(enemy_combat_units)

        # 2. Evaluate Combat Unit Disadvantage
        # We add 1 to the denominator to prevent division by zero
        combat_ratio = enemy_count / max(1, friendly_count)
        combat_diff = enemy_count - friendly_count

        if combat_ratio >= self._CRITICAL_RATIO_THRESHOLD and combat_diff >= self._CRITICAL_DIFF_THRESHOLD:
            disadvantage_level = "critical"
            is_disadvantaged = True
            disadvantage_reasons.append(
                f"Critical Combat Inferiority: Enemy combat units ({enemy_count}) severely outnumber "
                f"friendly combat units ({friendly_count}). Ratio: {combat_ratio:.1f}x, Diff: +{combat_diff}."
            )
        elif combat_ratio >= self._HIGH_RATIO_THRESHOLD and combat_diff >= self._HIGH_DIFF_THRESHOLD:
            # Only upgrade to high if not already critical
            if disadvantage_level != "critical":
                disadvantage_level = "high"
                is_disadvantaged = True
            disadvantage_reasons.append(
                f"Combat Inferiority: Enemy combat units ({enemy_count}) outnumber "
                f"friendly combat units ({friendly_count}). Ratio: {combat_ratio:.1f}x, Diff: +{combat_diff}."
            )

        # 3. Future Expansion: Economy Disadvantage, Tech Disadvantage, Map Control Disadvantage
        # (Can be added here later based on the same interface)

        return {
            "is_disadvantaged": is_disadvantaged,
            "disadvantage_level": disadvantage_level,
            "disadvantage_reasons": disadvantage_reasons,
            "metrics": {
                "friendly_combat_count": friendly_count,
                "enemy_combat_count": enemy_count,
                "combat_unit_ratio": round(combat_ratio, 2)
            }
        }

    def _get_combat_units(self, world_state: Any, owner: str) -> List[Any]:
        """
        Filter actors to extract only mobile combat units.
        This excludes Buildings (including defenses), Harvesters, and MCVs.
        """
        combat_units = []
        for actor in world_state.actors.values():
            if actor.owner != owner:
                continue
            
            # Use category to accurately filter out non-combatants
            if actor.category in (ActorCategory.INFANTRY, ActorCategory.VEHICLE):
                # Optionally, double check the can_attack flag to filter out unarmed scouts
                if getattr(actor, 'can_attack', True):
                    combat_units.append(actor)
                    
        return combat_units
