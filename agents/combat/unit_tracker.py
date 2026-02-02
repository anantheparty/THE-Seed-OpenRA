import threading
import time
import logging
from typing import Dict, List, Optional, Callable

from agents.combat.infra.game_client import GameClient
from agents.combat.infra.combat_data import get_combat_info, UnitCategory
from agents.combat.structs import CombatUnit

logger = logging.getLogger(__name__)

class UnitTracker:
    """
    Background worker that polls GameAPI for unit updates.
    - Adds new units to the tracking system.
    - Updates HP/Position of existing units.
    - Detects and removes dead units.
    """
    
    def __init__(self, game_client: GameClient):
        self.game_client = game_client
        self.running = False
        self.thread = None
        
        # Central store of all valid combat units: {unit_id: CombatUnit}
        self.units: Dict[int, CombatUnit] = {}
        self.lock = threading.RLock()
        
        # Callbacks for lifecycle events
        self.on_unit_added: Optional[Callable[[CombatUnit], None]] = None
        self.on_unit_removed: Optional[Callable[[int], None]] = None
        
        self.poll_interval = 0.5 # 0.5s interval for high-frequency combat tracking

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True, name="UnitTrackerThread")
        self.thread.start()
        logger.info("UnitTracker started.")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        logger.info("UnitTracker stopped.")

    def _poll_loop(self):
        while self.running:
            try:
                self._update_units()
            except Exception as e:
                logger.error(f"Error in UnitTracker poll loop: {e}", exc_info=True)
            
            time.sleep(self.poll_interval)

    def _update_units(self):
        # 1. Fetch raw data for Player's units
        try:
            # We use "己方" for My Faction because GameClient is set to "zh" and expects Chinese filters
            # Exclude frozen units for own forces (they are ghosts/glitches for own units and distract targeting)
            response = self.game_client.query_actors(faction_filter="己方", include_frozen=False)
            raw_actors = response.get("actors", [])
            
        except Exception as e:
            logger.warning(f"Failed to query actors: {e}")
            return

        current_ids = set()
        
        with self.lock:
            for actor_data in raw_actors:
                # Filter Logic
                # 1. Must be my unit (Double check faction field if returned)
                # Note: GameAPI ensures we only get requested faction, but check safely
                if actor_data.get("faction") != "己方":
                     # Fallback check for "Player" if API behavior varies
                    if actor_data.get("faction") != "Player":
                        continue
                
                # 2. Must be a valid combat unit (Score > 0)
                # 3. Must not be a structure/defense (User requirement: "no defense towers")
                u_type = actor_data.get("type", "")
                category, score = get_combat_info(u_type)
                
                if score <= 0:
                    continue
                if category == UnitCategory.DEFENSE:
                    continue
                if category == UnitCategory.OTHER: # Redundant check but safe
                    continue
                    
                # Valid unit found
                u_id = actor_data.get("id")
                current_ids.add(u_id)
                
                # Calculate HP Ratio
                hp = actor_data.get("hp", 0)
                max_hp = actor_data.get("maxHp", 1) # Avoid div by zero
                hp_ratio = hp / max_hp if max_hp > 0 else 0.0
                
                position = actor_data.get("position", {"x": 0, "y": 0})
                
                if u_id in self.units:
                    # Update existing
                    unit = self.units[u_id]
                    unit.hp_ratio = hp_ratio
                    unit.position = position
                else:
                    # New unit
                    new_unit = CombatUnit(
                        id=u_id,
                        type=u_type,
                        hp_ratio=hp_ratio,
                        position=position,
                        category=category,
                        score=score
                    )
                    self.units[u_id] = new_unit
                    if self.on_unit_added:
                        try:
                            self.on_unit_added(new_unit)
                        except Exception as e:
                            logger.error(f"Error in on_unit_added callback: {e}")

            # Detect removed/dead units
            # Any ID in self.units that is NOT in current_ids means it's gone
            existing_ids = list(self.units.keys())
            for old_id in existing_ids:
                if old_id not in current_ids:
                    # Unit is dead or lost
                    del self.units[old_id]
                    if self.on_unit_removed:
                        try:
                            self.on_unit_removed(old_id)
                        except Exception as e:
                            logger.error(f"Error in on_unit_removed callback: {e}")

    def get_unit(self, u_id: int) -> Optional[CombatUnit]:
        with self.lock:
            return self.units.get(u_id)
            
    def get_all_units(self) -> List[CombatUnit]:
        with self.lock:
            return list(self.units.values())
