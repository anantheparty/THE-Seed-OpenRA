from typing import Dict, List, Optional
from dataclasses import dataclass, field
import logging
import traceback

from openra_api.game_api import GameAPI
from openra_api.models import TargetsQueryParam

try:
    from .data.dataset import DATASET
    from .utils import UnitType, get_unit_info, normalize_unit_id, get_my_faction, Faction
except ImportError:
    from data.dataset import DATASET
    from utils import UnitType, get_unit_info, normalize_unit_id, get_my_faction, Faction

logger = logging.getLogger(__name__)

@dataclass
class QueueItem:
    name: str
    remaining_time: float
    total_time: float
    status: str  # "completed", "paused", "in_progress", "waiting"
    
    @property
    def is_active(self) -> bool:
        return self.status in ("in_progress", "waiting")

@dataclass
class ProductionQueue:
    type: str
    items: List[QueueItem] = field(default_factory=list)
    has_ready_item: bool = False

    @property
    def is_busy(self) -> bool:
        # Check if any item is active
        return any(item.is_active for item in self.items) or self.has_ready_item

class EconomyState:
    def __init__(self):
        self.faction = get_my_faction()
        self.cash: int = 0
        self.resources: int = 0
        self.power_provided: int = 0
        self.power_drained: int = 0
        
        # Inventory
        self.my_structures: Dict[str, int] = {} # ID -> count
        self.my_units: Dict[str, int] = {}      # ID -> count
        
        # Queues
        self.queues: Dict[str, ProductionQueue] = {
            "Building": ProductionQueue("Building"),
            "Defense": ProductionQueue("Defense"),
            "Infantry": ProductionQueue("Infantry"),
            "Vehicle": ProductionQueue("Vehicle"),
            "Aircraft": ProductionQueue("Aircraft"),
            # Naval disabled per memory
        }

    def _resp_summary(self, resp) -> str:
        if resp is None:
            return "resp=None"
        if not isinstance(resp, dict):
            return f"resp_type={type(resp).__name__}"
        keys = list(resp.keys())
        status = resp.get("status")
        data = resp.get("data")
        data_type = type(data).__name__
        data_keys = list(data.keys()) if isinstance(data, dict) else None
        return f"resp_keys={keys} status={status} data_type={data_type} data_keys={data_keys}"

    def _actor_value(self, actor, field: str):
        if isinstance(actor, dict):
            return actor.get(field)
        return getattr(actor, field, None)

    def _actor_keys(self, actor):
        if isinstance(actor, dict):
            return list(actor.keys())
        if hasattr(actor, "__dict__"):
            return list(actor.__dict__.keys())
        return []

    @property
    def total_money(self) -> int:
        return self.cash + self.resources

    @property
    def power_surplus(self) -> int:
        return self.power_provided - self.power_drained

    def update(self, api: GameAPI):
        """Fetch all necessary data from GameAPI"""
        self._update_base_info(api)
        self._update_queues(api)
        self._update_inventory(api)

    def _update_base_info(self, api: GameAPI):
        try:
            # player_baseinfo_query
            # Returns: {"Cash": 3000, "Resources": 120, "Power": 25, ...}
            # Note: The API command is player_baseinfo_query, params {}
            resp = api._send_request("player_baseinfo_query", {})
            if not resp or "data" not in resp:
                logger.debug(f"BaseInfo missing data. {self._resp_summary(resp)}")
                return
            data = resp.get("data", {})
            if not isinstance(data, dict) or not data:
                logger.debug(f"BaseInfo invalid data. {self._resp_summary(resp)} data_value={data}")
                return
            self.cash = data.get("Cash", 0)
            self.resources = data.get("Resources", 0)
            self.power_provided = data.get("PowerProvided", 0)
            self.power_drained = data.get("PowerDrained", 0)
            logger.debug(f"BaseInfo updated cash={self.cash} resources={self.resources} power_provided={self.power_provided} power_drained={self.power_drained}")
        except Exception as e:
            logger.error(f"Failed to update base info: {e}\n{traceback.format_exc()}")

    def _update_queues(self, api: GameAPI):
        for q_type in self.queues.keys():
            try:
                resp = api._send_request("query_production_queue", {"queueType": q_type})
                if not resp or "data" not in resp:
                    logger.debug(f"Queue {q_type} missing data. {self._resp_summary(resp)}")
                    continue
                data = resp.get("data", {})
                if not isinstance(data, dict) or not data:
                    logger.debug(f"Queue {q_type} invalid data. {self._resp_summary(resp)} data_value={data}")
                    continue
                items_data = data.get("queue_items", [])
                if items_data is None:
                    logger.debug(f"Queue {q_type} queue_items=None. {self._resp_summary(resp)}")
                    items_data = []
                if not isinstance(items_data, list):
                    logger.debug(f"Queue {q_type} queue_items_type={type(items_data).__name__}. {self._resp_summary(resp)}")
                    items_data = []
                queue_items = []
                if items_data:
                    for idx, item in enumerate(items_data):
                        if not isinstance(item, dict):
                            logger.debug(f"Queue {q_type} item_type={type(item).__name__} index={idx}")
                            continue
                        # Map Chinese Name back to ID if possible, or use name from API
                        # The API returns "name" (config name) and "chineseName"
                        # We prefer config name which usually matches ID or internal name
                        
                        q_item = QueueItem(
                            name=item.get("name"),
                            remaining_time=item.get("remaining_time", 0),
                            total_time=item.get("total_time", 0),
                            status=item.get("status", "unknown")
                        )
                        queue_items.append(q_item)
                
                self.queues[q_type].items = queue_items
                self.queues[q_type].has_ready_item = data.get("has_ready_item", False)
                logger.debug(f"Queue {q_type} items={len(queue_items)} has_ready_item={self.queues[q_type].has_ready_item}")
                
            except Exception as e:
                logger.error(f"Failed to update queue {q_type}: {e}\n{traceback.format_exc()}")

    def _update_inventory(self, api: GameAPI):
        try:
            # query_actor to get all my units/structures
            # We filter by faction="Player"
            actors = api.query_actor(TargetsQueryParam(faction="己方"))
            if actors is None:
                logger.debug("Inventory actors=None from query_actor")
                return
            if not isinstance(actors, list):
                logger.debug(f"Inventory actors_type={type(actors).__name__} from query_actor")
                return
            
            self.my_structures.clear()
            self.my_units.clear()
            actor_types_sample = []
            
            for idx, actor in enumerate(actors):
                if not isinstance(actor, dict) and not hasattr(actor, "__dict__"):
                    logger.debug(f"Inventory actor_type={type(actor).__name__} index={idx}")
                    continue
                # Filter out dead ones? Memory says "is_dead" field exists.
                if self._actor_value(actor, "is_dead"):
                    continue
                
                # Identify type
                # The API returns "type" (e.g. "3tnk", "fact")
                u_type = self._actor_value(actor, "type")
                if not u_type:
                    logger.debug(f"Inventory actor missing type index={idx} actor_keys={self._actor_keys(actor)}")
                    continue
                
                u_type = normalize_unit_id(u_type, self.faction)
                if not u_type:
                    continue
                if len(actor_types_sample) < 5:
                    actor_types_sample.append(u_type)
                
                # Try to map CN name back to ID if it's CN (Unlikely for 'type' field but possible)
                # But typically 'type' is ID-like (e.g. 3tnk, fact).
                # We use DATASET to check category
                info = get_unit_info(u_type)
                
                category = info.category if info else "Unknown"
                
                if category == "Building":
                    self.my_structures[u_type] = self.my_structures.get(u_type, 0) + 1
                else:
                    # Assume everything else is a unit (Vehicle, Infantry, Aircraft, Ship)
                    self.my_units[u_type] = self.my_units.get(u_type, 0) + 1
            logger.debug(f"Inventory updated structures={len(self.my_structures)} units={len(self.my_units)} actors={len(actors)} sample_types={actor_types_sample} unit_keys={list(self.my_units.keys())}")
                    
        except Exception as e:
            logger.error(f"Failed to update inventory: {e}\n{traceback.format_exc()}")

    def get_structure_count(self, structure_id: str) -> int:
        return self.my_structures.get(structure_id.upper(), 0)

    def get_unit_count(self, unit_id: str) -> int:
        return self.my_units.get(unit_id.upper(), 0)
