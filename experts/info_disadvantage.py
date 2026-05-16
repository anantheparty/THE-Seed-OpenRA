import math
from typing import Any, Dict, List, Tuple

class DisadvantageAssessor:
    """Information Expert: evaluates tactical disadvantage.
    
    Output fields injected into info_experts:
      disadvantage_global    bool
      disadvantage_local     bool
      disadvantage_warnings  list[str]
    """

    _GLOBAL_CRITICAL_RATIO = 3.0
    _GLOBAL_CRITICAL_DIFF = 20.0

    _LOCAL_THREAT_RADIUS = 25.0
    _LOCAL_CRITICAL_RATIO = 2.5
    _LOCAL_CRITICAL_DIFF = 15.0

    def __init__(self, world_model: Any):
        self.world_model = world_model

    def analyze(
        self,
        runtime_facts: Dict[str, Any],
        *,
        enemy_actors: List[Dict[str, Any]],
        recent_events: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        
        disadvantage_global = False
        disadvantage_local = False
        warnings: List[str] = []

        # 1. Fetch friendly combat units using world_model
        friendly_combat_units = self.world_model.find_actors(
            owner="self",
            can_attack=True,
        )
        # Filter mobile combat units
        friendly_mobile = [
            u for u in friendly_combat_units
            if u.category.value in {"infantry", "vehicle", "aircraft"}
        ]
        friendly_score = sum(u.combat_value for u in friendly_mobile)

        # 2. Evaluate Global Disadvantage
        # Calculate total enemy combat score from enemy_actors provided by WorldModel
        # Note: WorldModel compute_runtime_facts provides enemy_actors as dicts,
        # but we need their combat value. Since WorldModel provides 'enemy_combat_value' 
        # in the battlefield_snapshot but not in runtime_facts, we can fetch all enemy 
        # combat units directly to be accurate.
        enemy_combat_units = self.world_model.find_actors(
            owner="enemy",
            can_attack=True,
        )
        enemy_mobile = [
            u for u in enemy_combat_units
            if u.category.value in {"infantry", "vehicle", "aircraft"}
        ]
        enemy_score = sum(u.combat_value for u in enemy_mobile)

        if friendly_score > 0 or enemy_score > 0:
            ratio = enemy_score / max(1.0, friendly_score)
            diff = enemy_score - friendly_score
            if ratio >= self._GLOBAL_CRITICAL_RATIO and diff >= self._GLOBAL_CRITICAL_DIFF:
                disadvantage_global = True
                warnings.append(
                    f"[GLOBAL INFERIORITY] Enemy mobile combat score ({enemy_score:.1f}) "
                    f"severely outweighs ours ({friendly_score:.1f}). Ratio: {ratio:.1f}x."
                )

        # 3. Evaluate Local Disadvantage
        # A simple grid-based local clustering for friendly units
        if friendly_mobile and enemy_mobile:
            clusters = self._cluster_units(friendly_mobile, eps=15.0)
            for i, cluster in enumerate(clusters):
                squad_score = sum(u.combat_value for u in cluster)
                if squad_score <= 0:
                    continue
                
                cx = sum(u.position[0] for u in cluster) / len(cluster)
                cy = sum(u.position[1] for u in cluster) / len(cluster)

                # Find enemies near this squad center
                nearby_enemy_score = 0.0
                for eu in enemy_mobile:
                    dist = math.hypot(eu.position[0] - cx, eu.position[1] - cy)
                    if dist <= self._LOCAL_THREAT_RADIUS:
                        nearby_enemy_score += eu.combat_value

                ratio = nearby_enemy_score / max(1.0, squad_score)
                diff = nearby_enemy_score - squad_score

                if ratio >= self._LOCAL_CRITICAL_RATIO and diff >= self._LOCAL_CRITICAL_DIFF:
                    disadvantage_local = True
                    warnings.append(
                        f"[LOCAL INFERIORITY] Squad #{i+1} at ({int(cx)}, {int(cy)}) is outmatched! "
                        f"Squad score: {squad_score:.1f}, Nearby enemy score: {nearby_enemy_score:.1f}."
                    )

        return {
            "disadvantage_global": disadvantage_global,
            "disadvantage_local": disadvantage_local,
            "disadvantage_warnings": warnings,
        }

    def _cluster_units(self, units: List[Any], eps: float) -> List[List[Any]]:
        """Simple distance-based clustering for units with a .position tuple."""
        if not units:
            return []
        
        clusters: List[List[Any]] = []
        visited = set()

        for i, u1 in enumerate(units):
            if i in visited:
                continue
            
            # Start a new cluster
            current_cluster = [u1]
            visited.add(i)
            
            # Expand cluster
            queue = [u1]
            while queue:
                current_u = queue.pop(0)
                for j, u2 in enumerate(units):
                    if j in visited:
                        continue
                    dist = math.hypot(current_u.position[0] - u2.position[0], current_u.position[1] - u2.position[1])
                    if dist <= eps:
                        visited.add(j)
                        current_cluster.append(u2)
                        queue.append(u2)
            
            clusters.append(current_cluster)
            
        return clusters
