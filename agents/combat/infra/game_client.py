import socket
import json
import uuid
import logging
from typing import Dict, Any, Optional
from agents.combat.infra.dataset_map import map_cn_to_code

logger = logging.getLogger(__name__)

class GameClient:
    """
    Independent Game Client for Combat Module.
    Does not depend on openra_api.models or other project-specific classes.
    Returns raw dictionaries/lists.
    """
    
    def __init__(self, host: str = "127.0.0.1", port: int = 7445, language: str = "zh"):
        self.server_address = (host, port)
        # Force language="zh" because OpenRA engine returns Chinese unit names regardless of setting
        # We will map them to English codes internally.
        self.language = "zh" 
        self.timeout = 5.0
        self.api_version = "1.0"

    def _generate_request_id(self) -> str:
        return str(uuid.uuid4())

    def _receive_data(self, sock: socket.socket) -> str:
        """Receive data until server closes connection or timeout (Short-lived connection pattern)"""
        chunks = []
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            except socket.timeout:
                if chunks:
                    break
                raise TimeoutError("Socket timed out receiving data")
        return b''.join(chunks).decode('utf-8')

    def _send_request(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send request to OpenRA server.
        Uses a new connection per request (Short-lived).
        """
        request_id = self._generate_request_id()
        payload = {
            "apiVersion": self.api_version,
            "requestId": request_id,
            "command": command,
            "params": params,
            "language": self.language
        }

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.server_address)
                
                sock.sendall(json.dumps(payload).encode('utf-8'))
                response_str = self._receive_data(sock)
                
                if not response_str:
                    raise ConnectionError("Empty response from server")

                response = json.loads(response_str)
                
                if response.get("requestId") != request_id:
                    logger.warning(f"Request ID mismatch: expected {request_id}, got {response.get('requestId')}")
                
                if response.get("status", 0) < 0:
                    error = response.get("error", {})
                    raise RuntimeError(f"Game Server Error [{error.get('code')}]: {error.get('message')}")

                return response.get("data", {})

        except (socket.error, ConnectionRefusedError) as e:
            logger.error(f"Socket connection failed: {e}")
            raise

    def query_map(self) -> Dict[str, Any]:
        """Get map info"""
        return self._send_request("map_query", {})

    def query_actors(self, faction_filter: str = "己方", include_frozen: bool = True) -> Dict[str, Any]:
        """
        Get actors.
        According to API Docs and IntelligenceService Guide:
        - Must explicitly iterate query ["己方", "敌方", "友方", "中立"] to get all units.
        - Default params (no faction) usually returns "己方".
        - Returns 'actors' and 'frozenActors'.
        
        Args:
            faction_filter: "己方", "敌方", "中立", "友方". Default is "己方" (My Faction).
                            We use Chinese filter keys because API expects them when language="zh".
            include_frozen: Whether to include 'frozenActors' in the result. Default True.
                            Set to False for CombatAgent's own units to avoid noise.
                            
        Returns:
            Dict containing merged 'actors' list with 'type' field mapped to English code (e.g. 'e1').
        """
        params = {
            "targets": {
                "range": "all",
                "faction": faction_filter 
            }
        }
        
        data = self._send_request("query_actor", params)
        
        # Merge frozen actors into main list for convenience, but mark them
        actors = data.get("actors", [])
        frozen = data.get("frozenActors", [])
        
        # Helper to process unit list: map CN type to Code
        def process_units(unit_list, is_frozen=False):
            processed = []
            for u in unit_list:
                # Map type: "步兵" -> "e1"
                raw_type = u.get("type", "")
                u["type"] = map_cn_to_code(raw_type)
                u["_raw_type"] = raw_type # Keep original just in case
                
                if is_frozen:
                    u["isFrozen"] = True
                processed.append(u)
            return processed

        merged_actors = process_units(actors, False)
        
        if include_frozen:
            merged_actors += process_units(frozen, True)
        
        data["actors"] = merged_actors
        return data

    def send_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a generic order.
        Note: The API wrapper expects 'orders' list for 'command' request.
        But for 'move_actor' and 'attack', we use top-level commands.
        This generic method is for other in-game commands like 'Stop'.
        """
        return self._send_request("command", {"orders": [order]})

    def attack_move(self, actor_ids: list, target_location: Dict[str, int]):
        """
        Helper for attack move.
        Uses top-level 'move_actor' command with isAttackMove=1.
        """
        return self._send_request("move_actor", {
            "targets": {
                "actorId": actor_ids
            },
            "location": target_location,
            "isAttackMove": 1
        })

    def attack(self, attacker_ids: list, target_ids: list):
        """
        Send attack command using top-level 'attack' API.
        Ensures IDs are integers to prevent JSON serialization issues or server type mismatches.
        """
        # Ensure all IDs are integers
        safe_attackers = [int(aid) for aid in attacker_ids]
        safe_targets = [int(tid) for tid in target_ids]

        if not safe_attackers or not safe_targets:
            return {}

        return self._send_request("attack", {
            "attackers": {
                "actorId": safe_attackers,
                "range": "all"
            },
            "targets": {
                "actorId": safe_targets,
                "range": "all"
            }
        })

    def stop(self, actor_ids: list):
        """Helper for stop"""
        return self.send_order({
            "command": "Stop",
            "actorIds": actor_ids
        })
