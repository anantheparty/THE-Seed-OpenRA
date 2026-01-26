import logging
from typing import List, Optional, Tuple, Dict
from dataclasses import dataclass
from enum import Enum

from agents.economy.state import EconomyState, QueueItem
from agents.economy.utils import UnitType, get_unit_category, get_my_faction, Faction, UnitInfo, get_unit_info

logger = logging.getLogger(__name__)

class ActionType(Enum):
    BUILD_STRUCTURE = "BUILD_STRUCTURE"
    BUILD_UNIT = "BUILD_UNIT"
    DEPLOY_MCV = "DEPLOY_MCV"

@dataclass
class Action:
    type: ActionType
    target_id: str
    count: int = 1

class EconomyEngine:
    def __init__(self):
        self.faction = get_my_faction()
        self.target_ratios = {
            "ARTY": 1.0,
            "MBT": 2.0,
            "AFV": 0.5,
            "INF_MEAT": 2.0,
            "INF_AT": 1.0
        }
        self.build_order_index = 0
        
        # Build Order Sequence (Dynamic based on faction)
        self.build_sequence = self._get_build_sequence()

    def _get_build_sequence(self) -> List[Tuple[str, int]]:
        """
        Return list of (StructureID, DesiredCount).
        """
        # Common start
        seq = [
            (UnitType.ConstructionYard, 1),
            (UnitType.PowerPlant, 1),
        ]
        
        if self.faction == Faction.Soviet:
            seq.extend([
                (UnitType.Barracks_Soviet, 1),
                (UnitType.OreRefinery, 1),
                (UnitType.WarFactory, 1),
                (UnitType.Radar_Soviet, 1),
                (UnitType.OreRefinery, 2), # Total 2
                (UnitType.Airfield, 1), # Added Airfield
                (UnitType.ServiceDepot, 1),
                (UnitType.OreRefinery, 5), # Up to 5
                (UnitType.TechCenter_Soviet, 1),
            ])
        else:
            # Allies
            seq.extend([
                (UnitType.Barracks_Allies, 1),
                (UnitType.OreRefinery, 1),
                (UnitType.WarFactory, 1),
                (UnitType.Radar_Soviet, 1), # Allies use same DOME ID
                (UnitType.OreRefinery, 2),
                (UnitType.Helipad, 1), # Added Helipad
                (UnitType.ServiceDepot, 1),
                (UnitType.OreRefinery, 5),
                (UnitType.TechCenter_Allies, 1),
            ])
            
        return seq

    def decide(self, state: EconomyState) -> List[Action]:
        actions = []
        
        # 0. Check MCV Deployment
        # If no ConstructionYard AND NO OTHER BUILDINGS and have MCV unit -> Deploy
        # User requirement: "当玩家没有任何建筑的时候，才自动展开基地"
        if len(state.my_structures) == 0:
            if state.get_unit_count(UnitType.MCV) > 0:
                logger.info("No structures and MCV found. Deploying MCV.")
                return [Action(ActionType.DEPLOY_MCV, "MCV")]
        
        # Check if we have Construction Yard to build structures
        has_construction_yard = state.get_structure_count(UnitType.ConstructionYard) > 0
        
        # 1. Power Check (Highest Priority)
        # If power < 0, build Power Plant immediately
        # Only if we have Construction Yard
        if has_construction_yard and state.power_surplus < 0:
            # Check if we are already building power
            if not self._is_building(state, UnitType.PowerPlant) and \
               not self._is_building(state, UnitType.AdvPowerPlant):
                
                pwr_id = UnitType.AdvPowerPlant if self._check_prereqs(state, UnitType.AdvPowerPlant) else UnitType.PowerPlant
                
                logger.warning(f"Low Power ({state.power_surplus}). Emergency Build: {pwr_id}")
                return [Action(ActionType.BUILD_STRUCTURE, pwr_id)]

        # 2. Build Order Execution (Includes Rebuilding)
        # The build sequence includes essential buildings (Factory, Service Depot, Tech Center).
        # _get_next_structure automatically handles rebuilding if current_count < desired_count.
        # Only proceed if Building Queue is free AND we have Construction Yard
        building_busy = state.queues["Building"].is_busy
        if has_construction_yard and not building_busy:
            next_struct = self._get_next_structure(state)
            if next_struct:
                # Pre-check power for this structure
                info = get_unit_info(next_struct)
                power_drain = -info.power if info and info.power < 0 else 0
                
                # If building this will cause power outage, build power instead
                # (Unless it IS a power plant, which has positive power)
                if info and info.power < 0 and (state.power_surplus - power_drain < 0):
                     logger.info(f"Building {next_struct} would cause low power. Building Power Plant first.")
                     pwr_id = UnitType.AdvPowerPlant if self._check_prereqs(state, UnitType.AdvPowerPlant) else UnitType.PowerPlant
                     return [Action(ActionType.BUILD_STRUCTURE, pwr_id)]
                
                logger.info(f"Build Order: {next_struct}")
                actions.append(Action(ActionType.BUILD_STRUCTURE, next_struct))
                # If we are building an essential structure, we return early to prioritize it
                # and avoid spending money elsewhere (unless we are super rich, but safety first).
                return actions

        # 3. Excess Money Handling (Dynamic Expansion)
        # Thresholds:
        # > 5000: Build extra War Factories (up to 5)
        # > 10000: Build Aircraft
        # > 15000: Build Defenses
        
        THRESHOLD_WF = 5000
        THRESHOLD_AIRCRAFT = 10000
        THRESHOLD_DEFENSE = 15000
        
        if state.total_money > THRESHOLD_WF:
            # 3.1 Extra War Factories
            # Requires Construction Yard
            if has_construction_yard and not building_busy:
                wf_id = UnitType.WarFactory
                current_wf = state.get_structure_count(wf_id)
                if current_wf < 5:
                    if not self._is_building(state, wf_id):
                        logger.info(f"Excess Money ({state.total_money} > {THRESHOLD_WF}). Expanding War Factories ({current_wf} -> 5).")
                        info_wf = get_unit_info(wf_id)
                        power_drain_wf = -info_wf.power if info_wf and info_wf.power < 0 else 0
                        if state.power_surplus - power_drain_wf >= 0:
                            actions.append(Action(ActionType.BUILD_STRUCTURE, wf_id))
        
        if state.total_money > THRESHOLD_AIRCRAFT:
            # 3.2 Aircraft (Aircraft Queue)
            # Independent of Construction Yard (usually)
            logger.info(f"Excess Money ({state.total_money} > {THRESHOLD_AIRCRAFT}). Building Aircraft.")
            self._handle_excess_aircraft(state, actions)

        if state.total_money > THRESHOLD_DEFENSE:
            # 3.3 Defenses (Defense Queue)
            # Defense structures require Construction Yard? Yes, usually built from ConYard queue.
            # But wait, in RA1, Defenses are in "Defense" tab but built by ConYard.
            # So they need ConYard.
            if has_construction_yard:
                logger.info(f"Excess Money ({state.total_money} > {THRESHOLD_DEFENSE}). Building Defenses.")
                self._handle_excess_defense(state, actions)

        # 4. Unit Production (Heuristic)
        # Check Queues
        # Infantry
        self._check_unit_queue(state, "Infantry", actions)
        # Vehicle
        self._check_unit_queue(state, "Vehicle", actions)
        # Aircraft (Normal production if defined in ratios, but currently Aircraft handled in excess mainly? 
        # Actually standard heuristic might also produce them if in ratios.
        # But our ratios map doesn't have "AIRCRAFT" key explicitly in target_ratios keys (ARTY, MBT, AFV, INF...).
        # Let's check target_ratios keys: ARTY, MBT, AFV, INF_MEAT, INF_AT. No AIRCRAFT.
        # So Aircraft are ONLY produced in Excess Money mode.
        
        return actions

    def _handle_excess_defense(self, state: EconomyState, actions: List[Action]):
        queue = state.queues.get("Defense")
        # Strict one-by-one check
        if not queue or len(queue.items) > 0 or queue.has_ready_item:
            return
            
        # Pick a random or round-robin defense
        # Simple logic: Balance between Anti-Ground and Anti-Air
        # Soviet: Tesla (G), Flame (G/I), SAM (A)
        # Allies: Turret (G), Pillbox (I), AA Gun (A)
        
        # We can just check what we have less of.
        def get_count(uid): return state.get_structure_count(uid)
        
        if self.faction == Faction.Soviet:
            defs = [UnitType.TeslaCoil, UnitType.SAMSite, UnitType.FlameTower]
        else:
            defs = [UnitType.Turret, UnitType.AA_Gun, UnitType.Pillbox]
            
        # Find the one with lowest count
        best_def = min(defs, key=get_count)
        
        # Check power
        info = get_unit_info(best_def)
        if info:
             power_drain = -info.power if info.power < 0 else 0
             if state.power_surplus - power_drain < 0:
                 # Not enough power. Try to build power plant.
                 # Power plants use "Building" queue.
                 b_queue = state.queues.get("Building")
                 if b_queue and not b_queue.is_busy:
                      pwr_id = UnitType.AdvPowerPlant if self._check_prereqs(state, UnitType.AdvPowerPlant) else UnitType.PowerPlant
                      logger.info(f"Low power for defense {best_def}. Queuing {pwr_id} instead.")
                      actions.append(Action(ActionType.BUILD_STRUCTURE, pwr_id))
                 return # Not enough power for defense yet
        
        # Check prerequisites
        if self._check_prereqs(state, best_def):
             actions.append(Action(ActionType.BUILD_STRUCTURE, best_def))

    def _handle_excess_aircraft(self, state: EconomyState, actions: List[Action]):
        queue = state.queues.get("Aircraft")
        # Strict one-by-one check
        if not queue or len(queue.items) > 0 or queue.has_ready_item:
            return
        
        # Limit removed per user request for endless production
        # total_aircraft = sum(state.get_unit_count(u) for u in [UnitType.Yak, UnitType.Mig, UnitType.Heli, UnitType.BlackHawk])
        # if total_aircraft >= 10:
        #    return

        unit_to_build = self._pick_unit_for_category("Aircraft", state)
            
        if unit_to_build and self._check_prereqs(state, unit_to_build):
            actions.append(Action(ActionType.BUILD_UNIT, unit_to_build))


    def _is_building(self, state: EconomyState, unit_name: str) -> bool:
        # Check all queues for this name
        # Note: QueueItem.name might be internal name.
        # We assume unit_name (ID) matches or maps to it.
        # Ideally we check all queues.
        for q in state.queues.values():
            for item in q.items:
                if item.is_active:
                    # Fuzzy match or exact match?
                    # API returns config name. dataset ID is typically upper case config name.
                    if item.name and item.name.upper() == unit_name.upper():
                        return True
        return False

    def _get_next_structure(self, state: EconomyState) -> Optional[str]:
        # Iterate through build sequence
        for struct_id, desired_count in self.build_sequence:
            current_count = state.get_structure_count(struct_id)
            # We also count active queue items as "in progress" -> effectively "count" for decision
            # to avoid queuing duplicates if we just issued one
            # Wait, `get_structure_count` is from `query_actor`.
            # If I just started building, it won't be in `query_actor` yet.
            # So I must check queue.
            
            in_queue = 0
            if self._is_building(state, struct_id):
                in_queue = 1
            
            if current_count + in_queue < desired_count:
                return struct_id
        return None

    def _check_unit_queue(self, state: EconomyState, queue_type: str, actions: List[Action]):
        queue = state.queues.get(queue_type)
        if not queue:
            return
            
        # Limit queue depth
        # User requirement: "all build queues are not allowed to stack (build one by one)"
        # So we strict check if queue is busy or has any items.
        if len(queue.items) > 0 or queue.has_ready_item:
            return

        # Money Check: Don't spend last penny on units if we need structures
        # Heuristic: Keep buffer of 2000 if not rich
        # But user said "游戏中有后期... 不停地建造单位".
        # Let's assume if money > 500 we can build.
        if state.total_money < 500:
            return

        # Select Unit Logic
        # Calculate current counts and ratios
        current_counts = self._count_units_by_category(state)
        
        # Define allowed categories based on queue type
        allowed_categories = []
        if queue_type == "Infantry":
            allowed_categories = ["INF_MEAT", "INF_AT"]
        elif queue_type == "Vehicle":
            allowed_categories = ["MBT", "AFV", "ARTY"]
        else:
            # Other queues (e.g. Aircraft if passed here, though Aircraft handled separately)
            return

        # Calculate scores
        best_category = None
        max_score = -1.0
        
        for cat, target_ratio in self.target_ratios.items():
            if cat not in allowed_categories:
                continue
                
            count = current_counts.get(cat, 0)
            # Normalize count by total combat units? 
            # Or just use count / ratio -> lowest value means "most behind".
            # "1:2" means for every 1 ARTY, I want 2 MBT.
            # So IdealMBT = 2 * IdealARTY.
            # Score = TargetRatio / (CurrentCount + epsilon).
            # The one with HIGHEST score is the one we need most.
            
            score = target_ratio / (count + 0.1)
            if score > max_score:
                max_score = score
                best_category = cat
        
        if best_category:
            unit_to_build = self._pick_unit_for_category(best_category, state)
            if unit_to_build:
                # Check prerequisites
                if self._check_prereqs(state, unit_to_build):
                    actions.append(Action(ActionType.BUILD_UNIT, unit_to_build))

    def _count_units_by_category(self, state: EconomyState) -> Dict[str, int]:
        counts = {k: 0 for k in self.target_ratios.keys()}
        for u_id, count in state.my_units.items():
            cat = get_unit_category(u_id)
            if cat in counts:
                counts[cat] += count
        return counts

    def _pick_unit_for_category(self, category: str, state: Optional[EconomyState] = None) -> Optional[str]:
        # Pick best unit for category based on faction with Round-Robin support
        # We use a simple counter or just pick the one with lower count to balance them.
        # Balancing by count is robust.
        
        candidates = []
        
        if self.faction == Faction.Soviet:
            if category == "MBT":
                # 3TNK, 4TNK
                candidates = [UnitType.HeavyTank]
                if state and self._check_prereqs(state, UnitType.MammothTank):
                    candidates.append(UnitType.MammothTank)
            elif category == "ARTY":
                candidates = [UnitType.V2Rocket]
            elif category == "AFV":
                candidates = [UnitType.FlakTrack]
                # APC is also AFV for Soviet? In RA1, APC is Soviet/Allies?
                # Dataset says APC faction="Soviet".
                # But user said "AFV includes ftrk, apc".
                candidates.append(UnitType.APC)
            elif category == "Aircraft":
                candidates = []
                # Check prereqs for each to ensure we only pick what we can build
                if state:
                    if self._check_prereqs(state, UnitType.Yak):
                        candidates.append(UnitType.Yak)
                    if self._check_prereqs(state, UnitType.Mig):
                        candidates.append(UnitType.Mig)
                else:
                    # Fallback without state (unlikely)
                    candidates = [UnitType.Yak, UnitType.Mig]

            elif category == "INF_MEAT":
                candidates = [UnitType.RifleInfantry_Soviet]
            elif category == "INF_AT":
                candidates = [UnitType.RocketInfantry_Soviet]
            elif category == "Defense": # Special category logic handled in _handle_excess_defense usually, but if unified...
                # Engine handles Defense separately in _handle_excess_defense.
                pass
            
        else:
            # Allies
            if category == "MBT":
                # 2TNK, CTNK
                candidates = []
                if state:
                    # Allies MBT (2TNK) requires Service Depot.
                    # If we can't build it, we shouldn't return it as "the" candidate, 
                    # otherwise the queue jams waiting for it.
                    if self._check_prereqs(state, UnitType.MediumTank):
                        candidates.append(UnitType.MediumTank)
                    if self._check_prereqs(state, UnitType.ChronoTank):
                        candidates.append(UnitType.ChronoTank)
                else:
                    candidates = [UnitType.MediumTank, UnitType.ChronoTank]
                    
            elif category == "ARTY":
                candidates = [UnitType.Artillery]
            elif category == "AFV":
                # 1TNK, JEEP
                candidates = [UnitType.LightTank, UnitType.Ranger]
            elif category == "Aircraft":
                candidates = []
                if state:
                    if self._check_prereqs(state, UnitType.Heli):
                        candidates.append(UnitType.Heli)
                    if self._check_prereqs(state, UnitType.BlackHawk):
                        candidates.append(UnitType.BlackHawk)
                else:
                    candidates = [UnitType.Heli, UnitType.BlackHawk]

            elif category == "INF_MEAT":
                candidates = [UnitType.RifleInfantry_Allies]
            elif category == "INF_AT":
                candidates = [UnitType.RocketInfantry_Allies]

        if not candidates:
            return None
            
        # Balance logic: Pick the one with lowest count
        if state:
            best_unit = min(candidates, key=lambda u: state.get_unit_count(u))
            return best_unit
        
        # Fallback if no state (shouldn't happen with current caller)
        return candidates[0]

    def _check_prereqs(self, state: EconomyState, unit_id: str) -> bool:
        # Check if we have prerequisites
        info = get_unit_info(unit_id)
        if not info:
            return True # Unknown, try anyway
        
        for req in info.prerequisites:
            # req is lower case id usually.
            # e.g. "fact", "weap"
            # State.my_structures keys are Upper Case usually (from query_actor type).
            req_upper = req.upper()
            if state.get_structure_count(req_upper) == 0:
                # Missing prereq
                return False
        return True
