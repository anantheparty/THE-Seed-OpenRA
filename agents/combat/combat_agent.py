import time
import math
import logging
import threading
import json
from typing import Dict, List, Optional, Tuple

from agents.combat.infra.game_client import GameClient
from agents.combat.infra.llm_client import LLMClient
from agents.combat.unit_tracker import UnitTracker
from agents.combat.squad_manager import SquadManager
from agents.combat.stream_parser import StreamParser
from agents.combat.infra.combat_data import get_combat_info, UnitCategory

logger = logging.getLogger(__name__)

# Combat Constants
COMBAT_RADIUS = 50 # Cells

class CombatAgent:
    """
    The Tactician.
    Manages multiple companies, scans local sectors, and uses LLM to make micro-decisions.
    """
    
    def __init__(self, game_client: GameClient, llm_client: LLMClient, tracker: UnitTracker, squad_manager: SquadManager):
        self.game_client = game_client
        self.llm_client = llm_client
        self.tracker = tracker
        self.squad_manager = squad_manager
        
        self.running = False
        self.thread = None
        self.tactical_enhancer = None # Optional: BiodsEnhancer
        
        # Company State: {company_id: {"is_processing": bool, "status": "combat"|"relocate", "target_pos": ...}}
        self.company_states: Dict[str, Dict] = {}
        
        # Pending Orders: {company_id: (order_type, params)}
        # Used to buffer strategic commands until the start of the next tactical cycle
        self.pending_orders: Dict[str, Tuple[str, Dict]] = {}
        self.orders_lock = threading.Lock()
        
        # Prompt Templates
        self.PROMPT_SYSTEM_TEMPLATE = """
你是连长（战斗专家）。根据兵种克制、位置与血量，为每个我方单位分配一个合理的敌方目标。

单位分类 (Category) 与代码 (Code) 对照表（苏军/盟军通用）：
- 核心目标: mcv (基地车)
- 步兵 (INF):
  * 炮灰 (INF_MEAT): e1
  * 反甲/防空 (INF_AT): e3
- 车辆 (VEHICLE):
  * 主战 (MBT): 2tnk, 3tnk, 4tnk, ctnk
  * 远程 (ARTY): v2rl, arty
  * 轻型/防空 (AFV): ftrk, jeep, 1tnk, apc
  * 后勤: harv (矿车)
- 防御 (DEFENSE):
  * 对空: sam, agun
  * 反步兵: ftur, pbox
  * 反坦克: tsla, gun
- 飞机 (AIRCRAFT): yak, mig, heli (及其他空中单位)
- 建筑 (BUILDING): fact (建造厂), 其他 (weap, barr, pwr, dome, fix, proc...)

核心规则：
1. **对空限制**：仅 e3, 4tnk, ftrk, heli, sam, agun 可对空。
2. **斩首行动**：若 mcv 可见，全军集火 mcv。
3. **威胁优先**：单位/防御 > fact > 其他建筑。
4. **自主决策**：综合考虑距离、血量、兵种克制。允许集火。

基于 UnitCategory 的兵种克制与优先攻击链：
- INF_AT (e3): 优先攻击 -> MBT
- INF_MEAT (e1): 优先攻击 -> INF_AT
- MBT (2tnk/3tnk/ctnk): 优先攻击 -> ARTY > MBT > AFV
- MBT (4tnk): 全能。优先攻击 -> MBT/ARTY > DEFENSE
- AFV (jeep/1tnk/apc): 优先攻击 -> ARTY > INF > AFV
- AFV (ftrk): 优先攻击 -> AIRCRAFT > INF
- ARTY (v2rl/arty): 优先攻击 -> INF > ARTY > DEFENSE

输出格式提醒：仅返回 JSON；唯一合法格式 [[attacker_id, target_id], ...]；必须是二维整数数组；允许多个 attacker 指向同一 target 以实现集火；禁止任何其他键名或冗余字段。
"""

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._agent_loop, daemon=True, name="CombatAgentThread")
        self.thread.start()
        logger.info("CombatAgent started.")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        logger.info("CombatAgent stopped.")

    def _agent_loop(self):
        while self.running:
            try:
                # 1. Apply any pending orders from Strategy
                self._apply_pending_orders()
                
                # 2. Process tactical logic
                self._process_companies()
            except Exception as e:
                logger.error(f"Agent loop error: {e}", exc_info=True)
            time.sleep(0.1) # Small delay to prevent CPU spinning

    def _apply_pending_orders(self):
        """
        Check pending orders queue and apply them to company states.
        This runs at the start of each tactical cycle.
        """
        with self.orders_lock:
            if not self.pending_orders:
                return
                
            for company_id, (order_type, params) in self.pending_orders.items():
                self._apply_order_to_state(company_id, order_type, params)
            
            self.pending_orders.clear()

    def _apply_order_to_state(self, company_id: str, order_type: str, params: Dict):
        """
        Internal method to apply a single order to company state.
        Contains logic previously in set_company_order.
        """
        # New logic: Immediate Combat Loop Activation for 'attack' move_mode
        # If relocate + attack mode, we execute move once, then switch to COMBAT immediately.
        
        is_immediate_combat = False
        if order_type == "relocate":
            move_mode = params.get("move_mode", "attack")
            if move_mode == "attack":
                # Execute the move command ONCE
                self._execute_relocate(company_id, params)
                # Switch to combat mode immediately, using target_pos as scan center
                order_type = "combat"
                is_immediate_combat = True
                logger.info(f"Company {company_id}: Relocate(attack) -> Auto-switch to COMBAT immediately.")

        self.company_states[company_id] = {
            "is_processing": False, 
            "last_relocate_time": 0,
            "status": order_type, 
            "params": params,
            # Keep the strategic destination stable for UI/telemetry.
            "strategic_target_pos": params.get("target_pos"),
            "last_pos_check_time": 0,
            "last_center": None,
            "stable_count": 0
        }
        
        logger.info(f"Company {company_id} state updated: {order_type} -> {params}")
        
        # If relocate (normal mode), execute immediately once
        if order_type == "relocate" and not is_immediate_combat:
            self._execute_relocate(company_id, params)

    def set_company_order(self, company_id: str, order_type: str, params: Dict):
        """
        API for Superior to set order.
        Respects turn-based logic: strategic orders are queued and only take effect 
        at the start of the next tactical cycle.
        Latest order for a company overrides previous pending ones.
        """
        if company_id not in self.squad_manager.companies:
            logger.warning(f"Order failed: Company {company_id} not found")
            return

        with self.orders_lock:
            self.pending_orders[company_id] = (order_type, params)
            logger.info(f"Order queued for Company {company_id}: {order_type} -> {params}")

    def _execute_relocate(self, company_id: str, params: Dict):
        """
        Execute relocate command.
        Uses move_mode to decide between AttackMove and Move.
        """
        if company_id not in self.squad_manager.companies:
            return
            
        squad = self.squad_manager.companies[company_id]
        if squad.unit_count == 0:
            return

        target_pos = params.get("target_pos")
        if not target_pos:
            return
            
        move_mode = params.get("move_mode", "attack") # default to attack
        
        ids = list(squad.units.keys())
        if not ids:
            return
            
        try:
            if move_mode == "normal":
                # Use move_actor with isAttackMove=0
                self.game_client._send_request("move_actor", {
                    "targets": {"actorId": ids},
                    "location": target_pos,
                    "isAttackMove": 0
                })
            else:
                # Use AttackMove (engage enemies)
                self.game_client.attack_move(ids, target_pos)
                
        except Exception as e:
            logger.warning(f"Relocate failed for {company_id}: {e}")

    def _process_companies(self):
        now = time.time()
        
        # Iterate over managed companies
        for company_id, squad in self.squad_manager.companies.items():
            state = self.company_states.get(company_id)
            if not state:
                continue # No orders yet
            
            # Check Status
            if state["status"] == "relocate":
                # For "normal" move mode, we just wait.
                # No auto-switch logic needed as requested by user.
                # "如果是move_mode='normal'，则暂停combat循环，直至等待上游下达新命令"
                continue
                
            # Combat Mode
            if state["status"] == "combat":
                # Check if already processing (LLM streaming)
                if state.get("is_processing"):
                    continue
                    
                # Start new cycle immediately
                # We launch it in a separate thread/task so other companies aren't blocked?
                # Actually, _execute_company_cycle blocks on LLM stream.
                # To support multi-company parallelism, we should use a ThreadPool or async.
                # For this simplified standalone agent, we can use a simple Thread for each cycle.
                
                # Update target_pos to current center to scan local area
                # Dynamic Update: Always scan around the current center of the squad.
                # This ensures that as the squad moves, the scan area moves with it.
                current_center = squad.get_center_coordinates()
                if current_center:
                     state["params"]["target_pos"] = current_center
                
                state["is_processing"] = True
                threading.Thread(target=self._run_combat_cycle_thread, args=(company_id, squad, state), daemon=True).start()

    def _run_combat_cycle_thread(self, company_id, squad, state):
        try:
            self._execute_company_cycle(company_id, squad, state["params"])
        except Exception as e:
            logger.error(f"Combat cycle error for {company_id}: {e}", exc_info=True)
        finally:
            state["is_processing"] = False # Unlock for next cycle

    def _execute_company_cycle(self, cid: str, squad: "Squad", params: Dict):
        if squad.unit_count == 0:
            return

        target_pos = params.get("target_pos")
        
        # 1. Scan Local Zone
        enemies = self._scan_enemies(target_pos, COMBAT_RADIUS)
        
        if not enemies:
            # Optimization: Avoid infinite fast-loop LLM calls when no enemies.
            # We just sleep for a while and return.
            time.sleep(2.0)
            return

        # 2. Prepare Context
        # Re-fetch unit status from SquadManager to ensure fresh data (HP, Position)
        # Squad object holds references to CombatUnit objects which are updated by UnitTracker.
        # So accessing squad.units.values() gives current state.
        
        allies_data = []
        for u in squad.units.values():
            # Filter out dead units just in case
            if u.hp_ratio <= 0:
                 continue
                 
            allies_data.append({
                "id": u.id,
                "type": u.type,
                "x": u.position["x"],
                "y": u.position["y"]
            })
            
        if not allies_data:
             logger.warning(f"Company {cid} has no valid units to fight.")
             return

        enemies_data = []
        for e in enemies:
            enemies_data.append({
                "id": e["id"],
                "type": e["type"],
                "x": e["position"]["x"],
                "y": e["position"]["y"],
                "hp": e["hp_ratio"]
            })
            
        context_json = {
            "enemies": enemies_data,
            "allies": allies_data
        }
        ally_ids = {u["id"] for u in allies_data}
        enemy_ids = {e["id"] for e in enemies_data}
        
        # 3. Call LLM (Streaming)
        # Dynamic Prompt Construction
        system_content = self.PROMPT_SYSTEM_TEMPLATE
        
        # Optimized Strategy:
        # System: Role, Rules, JSON Constraint (General)
        # User: 
        # "Situation Report:
        #  Enemies: [...]
        #  Allies: [...]
        #  
        #  Instructions: Assign targets. Output JSON only."
        
        user_content_parts = []
        user_content_parts.append(f"Enemy:\n{json.dumps(context_json['enemies'], ensure_ascii=False)}")
        user_content_parts.append(f"Ally:\n{json.dumps(context_json['allies'], ensure_ascii=False)}")
        
        user_content_parts.append('\n输出格式提醒：仅返回 JSON；唯一合法格式 [[attacker_id, target_id], ...]；必须是二维整数数组；允许多个 attacker 指向同一 target 以实现集火；禁止任何其他键名或冗余字段。')
        
        messages = [
            {"role": "system", "content": self.PROMPT_SYSTEM_TEMPLATE},
            {"role": "user", "content": "\n".join(user_content_parts)}
        ]
        
        logger.debug(f"LLM Prompt Context for {cid}: {json.dumps(context_json)}")
        
        # Instantiate a new parser for this thread/cycle to avoid race conditions
        parser = StreamParser() 
        full_response = ""
        assigned_attackers = set()
        
        try:
            stream = self.llm_client.chat_stream(messages)
            for chunk in stream:
                logger.debug(f"LLM Chunk: {repr(chunk)}")
                full_response += chunk
                pairs = parser.parse_chunk(chunk)
                if pairs:
                    # Filter pairs to ensure one target per attacker
                    unique_pairs = []
                    for attacker_id, target_id in pairs:
                        if attacker_id not in ally_ids or target_id not in enemy_ids:
                            logger.debug(f"Invalid pair ignored: {attacker_id} -> {target_id}")
                            continue
                        if attacker_id not in assigned_attackers:
                            unique_pairs.append((attacker_id, target_id))
                            assigned_attackers.add(attacker_id)
                        else:
                            logger.debug(f"Duplicate order ignored: {attacker_id} -> {target_id}")
                    
                    if unique_pairs:
                        self._execute_pairs(unique_pairs)
            logger.info(f"Full LLM Response for {cid}: {full_response}")
        except Exception as e:
            logger.error(f"LLM Error for Company {cid}: {e}. Partial response: {full_response}")

    def _scan_enemies(self, center: Dict[str, int], radius: int) -> List[Dict]:
        """
        Query GameAPI for enemies near center.
        Filter out mpspawn, husk, etc.
        """
        # We need a new query method in GameClient?
        # GameClient.query_actors returns ALL.
        # We can fetch all enemies and filter by distance locally.
        # This is expensive if map is huge, but acceptable for standalone agent.
        # Ideally GameAPI supports spatial query, but our socket API doesn't seem to support 'distance' efficiently without 'targets' param.
        # We can use 'targets' with 'location' and 'distance' in query_actor!
        # Reference socket-apis.md: query_actor params support location & distance.
        
        params = {
            "targets": {
                "range": "all",
                "faction": "敌方", # Enemy
                "location": center,
                "distance": radius
            }
        }
        
        try:
            
            raw_data = self.game_client._send_request("query_actor", params)
            actors = raw_data.get("actors", [])
            # Frozen actors are in separate list, we just ignore them.
            
            valid_enemies = []
            for e in actors:
                # Filter Husk/Spawns
                u_type = e.get("type", "") # This is Chinese name
                # We need to map it to code for logic
                # But GameClient.query_actors does mapping.
                # Here we called _send_request directly, so we need to map manually or use wrapper.
                # Let's use wrapper logic but with custom params.
                # Better: Add support for params in GameClient.query_actors?
                # Or just map here.
                
                try:
                    from agents.combat.infra.dataset_map import map_cn_to_code
                except ImportError:
                    from infra.dataset_map import map_cn_to_code
                    
                code = map_cn_to_code(u_type)
                
                if "husk" in code or "mpspawn" in code or "camera" in code:
                    continue
                    
                # Calculate HP
                hp = e.get("hp", 0)
                max_hp = e.get("maxHp", 1)
                hp_ratio = round(hp / max_hp, 2) if max_hp > 0 else 0.0
                
                valid_enemies.append({
                    "id": e["id"],
                    "type": code,
                    "position": e.get("position"),
                    "hp_ratio": hp_ratio
                })
                
            return valid_enemies
            
        except Exception as e:
            logger.warning(f"Scan enemies failed: {e}")
            return []

    def set_tactical_enhancer(self, enhancer):
        """
        Inject tactical enhancer (BiodsEnhancer) to intercept and optimize attack commands.
        """
        self.tactical_enhancer = enhancer
        logger.info(f"Tactical Enhancer injected: {enhancer}")

    def _execute_pairs(self, pairs: List[Tuple[int, int]]):
        """
        Execute attack orders.
        Group by target to optimize? No, stream is pair by pair.
        But we can batch slightly if needed.
        For now, execute immediately.
        """
        # Intercept with Tactical Enhancer if available and enabled
        if self.tactical_enhancer and getattr(self.tactical_enhancer, "enabled", False):
            try:
                # Ensure strict type compliance: List[Tuple[int, int]] for tactical_core
                # Although StreamParser returns tuples, defensive coding here ensures compliance.
                validated_pairs = [(int(a), int(t)) for a, t in pairs]
                
                success, msg = self.tactical_enhancer.enhance_execute(None, validated_pairs)
                if success:
                    # Log injection success, but continue to standard execution as requested (Copy mode)
                    logger.info(f"TacticalEnhancer: Injected {len(pairs)} cmds for enhancement")
            except Exception as e:
                logger.error(f"TacticalEnhancer injection failed: {e}")

        for attacker_id, target_id in pairs:
            # We can't batch easily because stream yields [A, T], [B, T].
            # Just send one by one. OpenRA handles high freq commands well enough?
            # Better: Maybe accumulate a bit?
            # But requirement says "Stream Execution".
            
            try:
                self.game_client.attack([attacker_id], [target_id])
                logger.info(f"Cmd sent: {attacker_id} -> {target_id} (执行成功)")
            except Exception as e:
                logger.error(f"Cmd failed: {attacker_id}->{target_id}: {e}")

    def _move_squad(self, squad: "Squad", pos: Dict[str, int]):
        ids = list(squad.units.keys())
        if ids:
            self.game_client.attack_move(ids, pos)
