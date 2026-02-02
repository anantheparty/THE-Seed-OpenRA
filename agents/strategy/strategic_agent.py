
import os
import sys
import time
import json
import logging
import threading
from typing import Dict, Any, List, Optional
from dataclasses import asdict

# Ensure we can import from root (Robust against CWD)
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv

# Combat Agent Imports
try:
    from agents.combat.combat_agent import CombatAgent
    from agents.combat.infra.game_client import GameClient as CombatGameClient
    from agents.combat.infra.llm_client import LLMClient as CombatLLMClient
    from agents.combat.unit_tracker import UnitTracker
    from agents.combat.squad_manager import SquadManager
    from agents.combat.infra.combat_data import UNIT_COMBAT_INFO
except ImportError:
    pass

# OpenRA State Imports
try:
    from openra_state.api_client import GameAPI as StateGameAPI
    from openra_state.intel.intelligence_service import IntelligenceService
    from openra_state.intel.zone_manager import ZoneInfo
except ImportError:
    pass

# Strategy Imports
try:
    from .llm_client import StrategyLLMClient
except ImportError:
    from agents.strategy.llm_client import StrategyLLMClient

# Economy Agent Imports
try:
    from agents.economy.agent import EconomyAgent
    from agents.economy.api.game_api import GameAPI as EconomyGameAPI
except ImportError:
    pass

# Tactical Core Imports
try:
    from tactical_core.enhancer import BiodsEnhancer
except ImportError:
    pass

# Logging Setup
log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategic_agent.log")

# Handlers
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO) # Console: INFO only

file_handler = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
file_handler.setLevel(logging.DEBUG) # File: DEBUG (Full logs)

logging.basicConfig(
    level=logging.DEBUG, # Root logger: DEBUG (to allow file handler to see debugs)
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[stream_handler, file_handler]
)

# Suppress noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
# Suppress SquadManager details as requested
logging.getLogger("agents.combat.squad_manager").setLevel(logging.WARNING)
# Suppress Economy Agent debugs
logging.getLogger("agents.economy").setLevel(logging.WARNING)
# Suppress Tactical Core internal logs (it has its own UI)
logging.getLogger("tactical_core").setLevel(logging.WARNING)

logger = logging.getLogger("StrategicAgent")

class StrategySink:
    """Simple Sink to store intelligence data for the Strategic Agent."""
    def __init__(self):
        self.data = {}
        
    def update_intelligence(self, key: str, value: Any) -> None:
        self.data[key] = value

class StrategicAgent:
    """
    The Strategic Commander.
    Coordinators CombatAgent and makes high-level decisions based on IntelligenceService.
    """
    
    PROMPT_SYSTEM = """
你是指挥 OpenRA 战役的**战略指挥官**。
你的职责是根据战场态势（Intel），指挥下属的战术连队（Combat Companies）进行作战。

### 1. 游戏基础知识
*   **坐标系**: 地图左上角为 (0,0)，右下角为 (MapWidth, MapHeight)。
*   **资源**: Ore (矿石), Gem (宝石, 价值更高)。控制矿区、控制视野、歼灭敌军主力部队，这三点是胜利关键。

### 2. 指挥接口 (Output JSON)
你需要输出 JSON 格式的指令来控制连队。
**重要规则**：
1. **先激活后使用**：只能对 `Squad Status` 中已存在的连队下达指令。
2. **扩充兵力**：若需要更多连队（最多支持 ID 1-5），必须在 `orders` 列表中显式包含 `enable` 指令。
3. **简化指挥**：不需要具体的战斗指令，只需将部队**部署/移动**到关键位置即可。部队到达后会自动进入战斗状态。

格式如下：
```json
{
    "orders": [
        {
            "company_id": "1",          // 连队 ID (必须是已存在的连队)
            "action": "relocate",       // "relocate": 部署/移动部队
            "target_pos": {"x": 10, "y": 20}, // 部署目标坐标
            "move_mode": "attack",      // (可选) "attack" (默认, 推进并消灭沿途敌人) 或 "normal" (快速行军/撤退)
            "weight": 2.0               // (可选) 兵力补充权重。注意：只影响补充速度，不影响现有兵力。
        },
        {
            "company_id": "3",
            "action": "enable",         // 激活新连队！只有激活后才能在后续回合指挥它。
            "weight": 1.0
        }
    ],
    "thoughts": "简要战术分析..."
}
```

*   **Action 说明**:
    *   `enable`: **激活连队**（仅限 ID 1-5）。若连队未出现在 Squad Status 中，必须先执行此指令。
    *   `relocate`: **部署/移动连队**。战略专家的核心指令。
        *   `move_mode` (可选参数):
            *   `attack` (默认): **推进模式**。部队在移动中会自动攻击视野内的敌人，到达目标后自动转入战斗防御状态。适用于进攻、侦察、占领、防守。
            *   `normal`: **急行军/撤退模式**。部队会忽略敌人，全速前往目标。适用于紧急撤退、诱敌。
    *   `weight`: 调整连队兵力补充优先级。注意：此机制是单向的（只进不出），降低权重不会减少现有兵力，仅减缓补充速度。主攻设为 3.0，牵制/防守设为 1.0。

### 3. 核心战略原则
1.  **集中优势兵力**: 避免添油战术。若有敌情，**必须集中所有战力**合围歼灭敌人主力。
2.  **多线应对**: 若多线受敌，需准确判断敌军主攻方向。
    *   **主力决战**: 集中大部分兵力在主战场与敌军主力决战。
    *   **分支牵制**: 派遣小股部队（需选择**当前战力较弱**的连队，并设 weight=1.0 限制后续补兵）去处理或拖延分支战场的敌军。
3.  **步步为营与口袋阵**:
    *   **无敌情时**: 以主基地为中心，逐步向外扩张控制矿区。此时可适度分散侦察，形成预设的包围网（口袋阵）。
    *   **有敌情时**: 一旦某方向发现敌人，周围分散的部队应迅速向该方向收缩，形成合围。
4.  **迷雾意识**: 始终假设迷雾中隐藏着更多敌人。若我方正面战力不足，应果断撤退诱敌（Relocate normal mode），配合侧翼部队形成包围。

### 4. 态势感知 (Context)
你将收到：
*   **Map Info**: 地图尺寸。
*   **User Command**: 上级（玩家）的总体指令（如“进攻”、“防守”）。
*   **Squad Status**: 现有连队的状态（位置、兵力、当前权重）。
*   **Zone Intel**: 战场区域划分，包括资源价值、敌我力量对比、所有者等。
    *   `is_explored`: 该区域是否已被探索。
    *   `is_visible`: 该区域当前是否可见（无战争迷雾）。若 `False`，说明该区域被迷雾覆盖，敌情已过时。

请根据 User Command 和 Zone Intel，灵活制定战略。
如果 User Command 为空，请自主决策（默认策略：步步为营，占领矿区，消灭敌人）。
"""

    def __init__(self):
        self.running = False
        
        # 1. Load Strategy Config
        load_dotenv(os.path.join("agents", "strategy", ".env"))
        self.api_key = os.getenv("LLM_API_KEY")
        self.base_url = os.getenv("LLM_BASE_URL")
        self.model = os.getenv("LLM_MODEL")
        self.game_host = os.getenv("GAME_HOST", "127.0.0.1")
        self.game_port = int(os.getenv("GAME_PORT", "7445"))
        
        # 2. Initialize Strategy LLM
        self.llm = StrategyLLMClient(self.api_key, self.base_url, self.model)
        
        # 3. Initialize Combat Agent (Sub-system)
        self.combat_game_client = CombatGameClient(host=self.game_host, port=self.game_port)
        self.combat_llm_client = CombatLLMClient(self.api_key, self.base_url, self.model)
        self.unit_tracker = UnitTracker(self.combat_game_client)
        self.squad_manager = SquadManager(self.unit_tracker)
        self.combat_agent = CombatAgent(
            self.combat_game_client, 
            self.combat_llm_client, 
            self.unit_tracker, 
            self.squad_manager
        )
        
        # 4. Initialize Intel Service (OpenRA State)
        self.state_api = StateGameAPI(host=self.game_host, port=self.game_port)
        self.intel_sink = StrategySink()
        self.intel_service = IntelligenceService(self.state_api, self.intel_sink)
        
        # 5. Initialize Economy Agent (Sub-system)
        # Default to inactive, enabled via CLI
        self.economy_api = EconomyGameAPI(host=self.game_host, port=self.game_port)
        self.economy_agent = EconomyAgent("EcoSpec", self.economy_api)
        self.economy_agent.set_active(False)
        self.economy_thread = None

        # 6. Initialize Tactical Core (BiodsEnhancer)
        # Default to disabled, can be enabled via CLI
        self.tactical_enhancer = BiodsEnhancer(enabled=False)
        self.combat_agent.set_tactical_enhancer(self.tactical_enhancer)

        # User Command File (Optional, managed via CLI or manual edit)
        self.cmd_file = "user_command.txt" 
        # Default command if file doesn't exist
        self.default_command = "自主决策"

    def start(self):
        self.running = True
        
        # Start Sub-systems
        self.unit_tracker.start() # UnitTracker needs start?
        # Check UnitTracker implementation: It has start() method?
        # Looking at unit_tracker.py code... 
        # It has _poll_loop but looks like it needs to be started manually.
        # Wait, UnitTracker in `unit_tracker.py` doesn't have a public start() method shown in snippet?
        # Let's check `unit_tracker.py` full code if needed.
        # Assuming typical thread pattern:
        if not self.unit_tracker.running:
            self.unit_tracker.running = True
            self.unit_tracker.thread = threading.Thread(target=self.unit_tracker._poll_loop, daemon=True)
            self.unit_tracker.thread.start()

        self.combat_agent.start() # This starts _agent_loop
        
        # Start Tactical Core (Always start background thread, but enabled flag controls logic)
        # Note: We pass None as api_client because BiodsEnhancer creates its own independent client.
        if self.tactical_enhancer:
            self.tactical_enhancer.start(None, show_log_window=False)

        # Start Economy Thread
        self.economy_thread = threading.Thread(target=self._economy_loop, daemon=True, name="EconomyAgentThread")
        self.economy_thread.start()

        logger.info("Strategic Agent Started.")
        
        try:
            while self.running:
                # Add a minimal buffer time to allow tactical agent to execute at least one cycle
                # before being potentially overridden by a new strategic order.
                # Combat agent loop is ~0.1s + processing time. 
                # Strategic LLM takes 5-10s. So naturally this is fine.
                # But to be safe and avoid CPU spinning if LLM is somehow instant (mock):
                time.sleep(2.0) 
                
                self._strategy_loop()
        except KeyboardInterrupt:
            self.stop()
            
    def stop(self):
        self.running = False
        self.combat_agent.stop()
        if self.tactical_enhancer:
            self.tactical_enhancer.stop()
        self.unit_tracker.running = False
        if self.economy_thread:
            self.economy_thread.join(timeout=1.0)
        logger.info("Strategic Agent Stopped.")

    def enable_economy(self):
        """Enable Economy Agent via CLI"""
        if self.economy_agent:
            self.economy_agent.set_active(True)

    def disable_economy(self):
        """Disable Economy Agent via CLI"""
        if self.economy_agent:
            self.economy_agent.set_active(False)

    def _economy_loop(self):
        """Background thread for Economy Agent"""
        while self.running:
            try:
                if self.economy_agent:
                    self.economy_agent.tick()
            except Exception as e:
                logger.error(f"Economy loop error: {e}")
            
            # Economy doesn't need to run super fast, 2s is enough for macro management
            time.sleep(2.0)

    def _strategy_loop(self):
        # 1. Update Intel
        try:
            self.intel_service.tick()
        except Exception as e:
            logger.error(f"Intel Tick Failed: {e}")
            return

        # 2. Gather Context
        map_width = self.intel_sink.data.get("map_width", 0)
        map_height = self.intel_sink.data.get("map_height", 0)
        
        if map_width == 0:
            logger.warning("Waiting for Map Data...")
            return

        # Zone Info
        zm = self.intel_sink.data.get("zone_manager")
        zones_summary = []
        if zm:
            for z in zm.zones.values():
                zones_summary.append({
                    "id": z.id,
                    "type": z.type, # MAIN_BASE, RESOURCE...
                    "center": {"x": z.center.x, "y": z.center.y} if z.center else None,
                    "owner": z.owner_faction,
                    "my_strength": round(z.my_strength, 1),
                    "enemy_strength": round(z.enemy_strength, 1),
                    "resource_val": round(z.resource_value, 1),
                    "is_visible": z.is_visible,
                    "is_explored": z.is_explored
                })
                
        # Squad Status
        squad_status = self.squad_manager.get_status()
        
        # User Command
        user_cmd = self.default_command
        if os.path.exists(self.cmd_file):
            try:
                with open(self.cmd_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        user_cmd = content
            except Exception:
                pass
                
        # 3. Build Prompt
        context = {
            "map_info": {"width": map_width, "height": map_height},
            "user_command": user_cmd,
            "squad_status": squad_status,
            "zones": zones_summary
        }
        
        user_msg = f"""
Current Game State (JSON):
{json.dumps(context, indent=2, ensure_ascii=False)}

Please analyze the situation and issue commands for my companies.
"""
        messages = [
            {"role": "system", "content": self.PROMPT_SYSTEM},
            {"role": "user", "content": user_msg}
        ]
        
        # 4. LLM Decision
        try:
            logger.info("Thinking...")
            response = self.llm.chat_completion(messages)
            self._execute_strategy(response)
        except Exception as e:
            logger.error(f"Strategy Error: {e}")

    def _execute_strategy(self, response_str: str):
        try:
            # Extract JSON from potential markdown code blocks
            clean_str = response_str.strip()
            if "```json" in clean_str:
                clean_str = clean_str.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_str:
                clean_str = clean_str.split("```")[1].split("```")[0].strip()
                
            data = json.loads(clean_str)
            logger.info(f"Strategy Decision: {data.get('thoughts', 'No thoughts')}")
            
            orders = data.get("orders", [])
            for order in orders:
                cid = str(order.get("company_id"))
                action = order.get("action")
                weight = order.get("weight")
                
                # Handle Enable/Weight
                if action == "enable":
                    self.squad_manager.enable_company(cid, weight if weight else 1.0)
                    continue
                    
                if weight is not None:
                    self.squad_manager.update_company_weight(cid, float(weight))
                    
                # Handle Movement/Combat
                # Note: "combat" is deprecated in prompt but kept here for compatibility if LLM hallucinates it.
                # We treat "combat" as "relocate" (which auto-switches to combat on arrival).
                if action in ["combat", "relocate"]:
                    target = order.get("target_pos")
                    move_mode = order.get("move_mode", "attack")
                    
                    # Ensure move_mode is passed for relocate
                    params = {"target_pos": target}
                    if action == "relocate":
                        params["move_mode"] = move_mode
                    # Compatibility: "combat" action implies attacking
                    elif action == "combat":
                         # Force action to relocate with attack mode to align with new simplified logic
                         action = "relocate"
                         params["move_mode"] = "attack"

                    if target:
                        self.combat_agent.set_company_order(cid, action, params)
                        
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON from Strategy LLM: {response_str}")
        except Exception as e:
            logger.error(f"Execution Error: {e}")

if __name__ == "__main__":
    agent = StrategicAgent()
    agent.start()
