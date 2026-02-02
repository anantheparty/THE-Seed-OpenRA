import logging
import uuid
import threading
from typing import Dict, List, Optional
from agents.combat.structs import Squad, CombatUnit, SquadType
from agents.combat.unit_tracker import UnitTracker

logger = logging.getLogger(__name__)

class SquadManager:
    """
    Manages squad composition and assignment.
    Implements greedy heuristic for auto-assignment.
    """
    
    MAX_COMPANIES = 8

    def __init__(self, unit_tracker: UnitTracker):
        self.tracker = unit_tracker
        self.lock = threading.RLock()
        
        # Squad Storage
        self.unassigned = Squad(id="unassigned", name="Unassigned")
        self.player_squad = Squad(id="player", name="Player Control")
        self.companies: Dict[str, Squad] = {} # id -> Squad
        
        # Initialize default companies (1 and 2)
        self.enable_company("1")
        self.enable_company("2")
        
        # Register callbacks
        self.tracker.on_unit_added = self._handle_new_unit
        self.tracker.on_unit_removed = self._handle_unit_death

    def enable_company(self, cid: str, weight: float = 1.0) -> bool:
        """
        Enable/Create a company by ID (1-8).
        """
        if cid not in [str(i) for i in range(1, self.MAX_COMPANIES + 1)]:
            logger.warning(f"Enable Company failed: ID {cid} must be 1-{self.MAX_COMPANIES}")
            return False
            
        with self.lock:
            if cid in self.companies:
                logger.info(f"Company {cid} already active.")
                return True
                
            new_company = Squad(id=cid, name=f"Company {cid}", target_weight=weight)
            self.companies[cid] = new_company
            logger.info(f"Enabled Company {cid}")
            return True

    # Deprecated: create_company is replaced by enable_company
    # def create_company(self, name: str, weight: float = 1.0) -> Optional[str]:
    #    ...

    def delete_company(self, company_id: str):
        with self.lock:
            if company_id not in self.companies:
                logger.warning(f"Attempted to delete non-existent company {company_id}")
                return
            
            # Return units to unassigned
            company = self.companies[company_id]
            for u_id, unit in list(company.units.items()):
                self._transfer_internal(unit, self.unassigned)
            
            del self.companies[company_id]
            logger.info(f"Deleted Company: {company_id}")

    def update_company_weight(self, company_id: str, weight: float):
        with self.lock:
            if company_id in self.companies:
                self.companies[company_id].target_weight = max(0.1, weight)
                logger.info(f"Updated Company {company_id} weight to {weight}")

    def transfer_unit(self, unit_id: int, target_squad_id: str):
        """API for manual transfer"""
        with self.lock:
            unit = self.tracker.get_unit(unit_id)
            if not unit:
                logger.warning(f"Transfer failed: Unit {unit_id} not found")
                return

            target_squad = self._get_squad_by_id(target_squad_id)
            if not target_squad:
                logger.warning(f"Transfer failed: Target squad {target_squad_id} not found")
                return

            self._transfer_internal(unit, target_squad)

    def _get_squad_by_id(self, squad_id: str) -> Optional[Squad]:
        if squad_id == "unassigned":
            return self.unassigned
        if squad_id == "player":
            return self.player_squad
        return self.companies.get(squad_id)

    def _transfer_internal(self, unit: CombatUnit, target_squad: Squad):
        """Internal transfer logic with squad bookkeeping"""
        # 1. Remove from current squad
        current_squad_id = unit.squad_id
        if current_squad_id:
            current_squad = self._get_squad_by_id(current_squad_id)
            if current_squad and unit.id in current_squad.units:
                del current_squad.units[unit.id]
        
        # 2. Add to new squad
        target_squad.units[unit.id] = unit
        unit.squad_id = target_squad.id
        logger.debug(f"Transferred Unit {unit.id} to {target_squad.name}")

    def _handle_new_unit(self, unit: CombatUnit):
        """
        Callback when a new unit is detected.
        Applies Greedy Heuristic to assign to a company.
        """
        with self.lock:
            # If no companies exist, put in unassigned
            if not self.companies:
                self._transfer_internal(unit, self.unassigned)
                return

            # Greedy Heuristic:
            # Find company with minimum 'Load'
            # Load = (CombatPower / Weight) + (UnitCount / Weight)
            # We can normalize if needed, but simple sum is robust enough for similar magnitudes
            
            best_company = None
            min_load = float('inf')
            
            for company in self.companies.values():
                # Avoid division by zero
                w = company.target_weight if company.target_weight > 0 else 0.1
                
                # Metric: We want to balance Power AND Count.
                # Since Power is usually ~1-20 and Count is 1, Power dominates.
                # Let's normalize or treat them equally?
                # User said: "Equalize Combat Power" AND "Equalize Unit Count".
                # Let's try to minimize the max of (PowerDensity, CountDensity) or sum?
                # Sum of densities is a good proxy for "Total Load".
                
                load = (company.total_score / w) + (company.unit_count / w)
                
                if load < min_load:
                    min_load = load
                    best_company = company
            
            if best_company:
                self._transfer_internal(unit, best_company)
                logger.info(f"Auto-assigned Unit {unit.id} ({unit.type}) to {best_company.name}")
            else:
                self._transfer_internal(unit, self.unassigned)

    def _handle_unit_death(self, unit_id: int):
        with self.lock:
            # Find which squad had it
            # Since we don't store "unit_id -> squad_id" mapping in Manager (it's on Unit),
            # we can iterate or rely on Unit object state if it persists?
            # Actually UnitTracker deleted the Unit object from its dict.
            # But the Unit object in memory might still be referenced by Squad?
            # Wait, UnitTracker.units holds the source of truth.
            # Squad.units holds references to the SAME CombatUnit objects? Yes.
            # So if UnitTracker deletes it, Squad still has it?
            # We need to clean up Squads.
            
            # Since we don't know which squad it was in easily without scanning (unless we kept a side map),
            # Let's scan. It's safe for O(N) where N is total units (usually <500).
            
            found = False
            for squad in [self.unassigned, self.player_squad] + list(self.companies.values()):
                if unit_id in squad.units:
                    del squad.units[unit_id]
                    found = True
                    # We don't break immediately because we want to ensure consistency, 
                    # though unit should be in only one squad.
                    break
            
            if found:
                logger.info(f"Removed dead unit {unit_id} from squads.")
            else:
                logger.debug(f"Dead unit {unit_id} was not in any squad (or already removed).")

    def get_status(self) -> Dict:
        """Return status summary for UI/Logging"""
        with self.lock:
            return {
                "companies": [
                    {
                        "id": c.id, 
                        # "name": c.name, # Hidden to avoid confusion for LLM
                        "count": c.unit_count, 
                        "power": c.total_score,
                        "weight": c.target_weight,
                        "location": c.get_center_coordinates()
                    }
                    for c in self.companies.values()
                ]
                # Hidden unassigned/player to reduce LLM context load
                # "unassigned": self.unassigned.unit_count,
                # "player": self.player_squad.unit_count
            }
