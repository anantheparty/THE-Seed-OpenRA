from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum

@dataclass
class CombatUnit:
    id: int
    type: str
    hp_ratio: float  # 0.0 to 1.0
    position: Dict[str, int] # {"x": 1, "y": 2}
    category: str
    score: float
    
    # Track which squad this unit belongs to (None if unassigned)
    squad_id: Optional[str] = None

@dataclass
class Squad:
    id: str
    name: str
    units: Dict[int, CombatUnit] = field(default_factory=dict)
    
    # Configuration for auto-balancing
    target_weight: float = 1.0  # Default weight for distribution
    
    @property
    def total_score(self) -> float:
        return sum(u.score for u in self.units.values())
        
    @property
    def unit_count(self) -> int:
        return len(self.units)
        
    @property
    def combat_power_density(self) -> float:
        """Score per unit weight"""
        return self.total_score / self.target_weight if self.target_weight > 0 else 0
        
    @property
    def unit_count_density(self) -> float:
        """Count per unit weight"""
        return self.unit_count / self.target_weight if self.target_weight > 0 else 0

    def get_center_coordinates(self) -> Optional[Dict[str, int]]:
        """
        Calculate the center of the squad, excluding outliers.
        Returns None if squad is empty.
        """
        if not self.units:
            return None
            
        units_list = list(self.units.values())
        if len(units_list) <= 2:
            # Simple average for small squads
            avg_x = sum(u.position["x"] for u in units_list) / len(units_list)
            avg_y = sum(u.position["y"] for u in units_list) / len(units_list)
            return {"x": int(avg_x), "y": int(avg_y)}

        # 1. Calculate Median Center (robust to outliers)
        xs = sorted(u.position["x"] for u in units_list)
        ys = sorted(u.position["y"] for u in units_list)
        mid = len(units_list) // 2
        med_x = xs[mid]
        med_y = ys[mid]
        
        # 2. Filter outliers (Core Density Clustering)
        # Inspired by brigade_runner.py
        # Use Manhattan distance for tighter, diamond-shaped clustering.
        # Threshold: 15 cells (Focus on the main fighting body).
        THRESHOLD_MANHATTAN = 15
        
        valid_units = []
        for u in units_list:
            dist = abs(u.position["x"] - med_x) + abs(u.position["y"] - med_y)
            if dist <= THRESHOLD_MANHATTAN:
                valid_units.append(u)
                
        # Fallback: if clustering is too aggressive, revert to median
        if not valid_units:
             return {"x": int(med_x), "y": int(med_y)}
            
        # 3. Calculate Mean of valid units (The Core)
        avg_x = sum(u.position["x"] for u in valid_units) / len(valid_units)
        avg_y = sum(u.position["y"] for u in valid_units) / len(valid_units)
        
        return {"x": int(avg_x), "y": int(avg_y)}

class SquadType(Enum):
    UNASSIGNED = "UNASSIGNED"
    PLAYER = "PLAYER"     # Manual control, hidden from auto-assign
    COMPANY = "COMPANY"   # Auto-managed company
