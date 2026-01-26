import logging
import time
from typing import List, Dict

from .api.game_api import GameAPI
from .api.models import TargetsQueryParam
from agents.global_blackboard import GlobalBlackboard
from agents.economy.state import EconomyState
from agents.economy.engine import EconomyEngine, Action, ActionType
from agents.economy.utils import get_unit_cn_name, UnitType, normalize_unit_id, get_unit_info

logger = logging.getLogger(__name__)

class EconomyAgent:
    """
    Economy Specialist Agent.
    Operates without FSM, using a direct Observe-Think-Act loop.
    """
    def __init__(self, name: str, global_bb: GlobalBlackboard, game_api: GameAPI):
        self.name = name
        self.global_bb = global_bb
        self.game_api = game_api
        
        self.state = EconomyState()
        self.engine = EconomyEngine()
        
        # Production Cooldown tracking
        # Key: Unit Category or Queue Type? 
        # Since we want to limit per queue.
        # Let's map Unit ID to its Queue Type to track last production time per Queue.
        # Actually simplest is to track last production time per ActionType + Unit Category/Queue.
        # But we don't know Queue Type easily here without looking up UnitInfo.
        # Let's track by Queue Type.
        self.last_production_time: Dict[str, float] = {}
        self.PRODUCTION_COOLDOWN = 3.0 # Seconds to wait before trusting queue state again
        
        # Register to Global Blackboard
        self.global_bb.registered_agents[self.name] = "ACTIVE"
        logger.info(f"EconomyAgent [{self.name}] initialized.")

    def tick(self):
        try:
            logger.debug("Tick observe start")
            # 1. Observe
            self.state.update(self.game_api)
            
            logger.debug("Tick think start")
            # 2. Think
            actions = self.engine.decide(self.state)
            if not actions:
                logger.debug("Tick think produced no actions")
            else:
                logger.debug(f"Tick think produced actions={len(actions)}")
            
            logger.debug("Tick act start")
            # 3. Act
            self._execute_actions(actions)
            logger.debug("Tick act done")
            
        except Exception as e:
            logger.error(f"Error in EconomyAgent tick: {e}", exc_info=True)

    def _execute_actions(self, actions: List[Action]):
        if not actions:
            logger.debug("Execute actions skipped (empty)")
            return
            
        current_time = time.time()
        
        for action in actions:
            try:
                # Check cooldown if it's a build action
                if action.type in (ActionType.BUILD_STRUCTURE, ActionType.BUILD_UNIT):
                    info = get_unit_info(action.target_id)
                    if info:
                        queue_type = info.category # "Building", "Vehicle", "Infantry", "Aircraft"
                        real_queue = queue_type
                        
                        if queue_type == "Building":
                            # Explicitly identify Defense structures
                            if action.target_id in [UnitType.TeslaCoil, UnitType.FlameTower, UnitType.SAMSite, 
                                                  UnitType.Pillbox, UnitType.Turret, UnitType.AA_Gun]:
                                real_queue = "Defense"
                        
                        last_time = self.last_production_time.get(real_queue, 0)
                        if current_time - last_time < self.PRODUCTION_COOLDOWN:
                            logger.info(f"Skipping action {action.target_id} due to cooldown for queue {real_queue}")
                            continue
                            
                        self.last_production_time[real_queue] = current_time

                logger.debug(f"Executing action type={action.type} target_id={action.target_id} count={action.count}")
                if action.type == ActionType.BUILD_STRUCTURE:
                    self._handle_build(action, is_structure=True)
                elif action.type == ActionType.BUILD_UNIT:
                    self._handle_build(action, is_structure=False)
                elif action.type == ActionType.DEPLOY_MCV:
                    self._handle_deploy_mcv()
            except Exception as e:
                logger.error(f"Failed to execute action {action}: {e}")

    def _handle_build(self, action: Action, is_structure: bool):
        # Convert ID to Chinese Name
        unit_cn = get_unit_cn_name(action.target_id)
        if not unit_cn:
            logger.error(f"Unknown unit ID: {action.target_id}")
            return

        logger.info(f"Executing Build: {unit_cn} (Count: {action.count})")
        
        # Call start_production
        # autoPlaceBuilding=True for structures (as requested)
        # For units, it's ignored or False? User said "d:\THE-Seed-OpenRA\socket-apis.md#L545-545 全部使用自动放置"
        # Does this apply to units? "建筑单位完成后自动放置". It only applies to buildings.
        # But sending True for units shouldn't hurt, or API ignores it.
        
        auto_place = True
        
        # API expects list of units
        # produce(self, unit_type: str, quantity: int, auto_place_building: bool = False)
        # Note: game_api.produce takes single unit_type.
        
        self.game_api.produce(
            unit_type=unit_cn, 
            quantity=action.count, 
            auto_place_building=auto_place
        )

    def _handle_deploy_mcv(self):
        # Find MCV
        # We can use state.my_units to check if we HAVE one, but to get the object we need query.
        # But state doesn't store actor objects.
        
        try:
            # TargetsQueryParam(owner=None) might query all. 
            # We filter for MCV in the returned list.
            my_actors = self.game_api.query_actor(TargetsQueryParam(faction="己方")) 
            
            mcv_actors = []
            mcv_actors = [
                actor for actor in my_actors
                if normalize_unit_id(actor.type, self.state.faction) == UnitType.MCV
            ]
            
            if mcv_actors:
                logger.info(f"Deploying MCV (Count: {len(mcv_actors)})")
                self.game_api.deploy_units(mcv_actors)
            else:
                logger.warning("Action DEPLOY_MCV requested but no MCV found.")
                
        except Exception as e:
            logger.error(f"Failed to deploy MCV: {e}")
